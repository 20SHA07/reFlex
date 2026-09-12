import { randomUUID } from "node:crypto";
import { copyFile, lstat, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { cleanupProposalSchema, narrativeSchema } from "./schemas";
import { assertDemoOperator, assertMember, assertOwner } from "./identity";
import { decisionState, evaluateCandidate } from "./policy";
import { DEBUG_LOG, REPORT_INPUT, SOURCE_METRICS, fileId, fileInfo, hashFile, moveSameFilesystem, requireWorkerFile, resolveWorkerFile, safeRelativePath, ensureDirectory, verifyHash } from "./paths";
import { readMetricsFile, renderMetricsTable } from "./metrics";
import { TruceStore } from "./store";
import type { ApprovalRecord, CleanupAction, CleanupOutcome, FileInventoryItem, OperationRecord, ProjectConfig, PublicationOutcome, RefereeVerdict, ReportMetrics, StatusSnapshot, TaskRecord, TrustedActor } from "./types";

export class TruceCoordinator {
  private mutationQueue: Promise<void> = Promise.resolve();

  constructor(readonly config: ProjectConfig, readonly store: TruceStore) {
    store.ensureProject(config);
    store.expireApprovalsFromOtherSessions(config.processSession);
  }

  static async open(config: ProjectConfig): Promise<TruceCoordinator> {
    return new TruceCoordinator(config, await TruceStore.open(config.dbPath));
  }

  close(): void { this.store.close(); }

  private serial<T>(work: () => Promise<T>): Promise<T> {
    const result = this.mutationQueue.then(work, work);
    this.mutationQueue = result.then(() => undefined, () => undefined);
    return result;
  }

  private member(actor: TrustedActor): void { assertMember(actor, this.config); }

  async startReport(actor: TrustedActor): Promise<TaskRecord> {
    this.member(actor);
    if (!actor.eventId) throw new Error("The request has no trusted event id and cannot be deduplicated.");
    const inbound = this.store.claimInbound(actor.eventId);
    if (!inbound.isNew && inbound.taskIds[0]) {
      const existing = this.store.getTask(inbound.taskIds[0]);
      if (existing) return existing;
    }
    const active = this.store.findActiveReport(this.config.projectId);
    if (active) {
      this.store.completeInbound(actor.eventId, [active.id]);
      this.store.recordAudit({ projectId: this.config.projectId, taskId: active.id, actorId: actor.canonicalUserId, eventType: "report_request_reused_active", payload: {} });
      return active;
    }
    const input = await requireWorkerFile(this.config.workspaceRoot, REPORT_INPUT);
    const inputHash = await hashFile(input.absolutePath);
    const task = this.store.createTask({ projectId: this.config.projectId, kind: "report", ownerId: actor.canonicalUserId, ownerLabel: actor.displayName, eventId: actor.eventId, conversationKey: actor.conversationKey, inputFileId: fileId(REPORT_INPUT), inputPath: REPORT_INPUT, inputHash });
    this.store.addDependency({ projectId: this.config.projectId, taskId: task.id, fileId: fileId(REPORT_INPUT), relativePath: REPORT_INPUT, observedHash: inputHash, reason: "Report review and possible revision require the working input." });
    this.store.completeInbound(actor.eventId, [task.id]);
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_started", payload: { inputPath: REPORT_INPUT, inputHash } });
    return task;
  }

  async readReportInput(actor: TrustedActor, taskId: string): Promise<{ task: TaskRecord; content: string; hash: string }> {
    this.member(actor);
    const task = this.requireTask(taskId, "report");
    assertOwner(actor, task.ownerId);
    if (task.state !== "running" && task.state !== "stale") throw new Error(`Report task is ${task.state}; it cannot be read for a new draft.`);
    const input = await requireWorkerFile(this.config.workspaceRoot, REPORT_INPUT);
    return { task, content: await readFile(input.absolutePath, "utf8"), hash: await hashFile(input.absolutePath) };
  }

  async getReportMetrics(actor: TrustedActor, taskId: string): Promise<{ task: TaskRecord; metrics: ReportMetrics; inputHash: string }> {
    this.member(actor);
    const task = this.requireTask(taskId, "report");
    assertOwner(actor, task.ownerId);
    const input = await requireWorkerFile(this.config.workspaceRoot, REPORT_INPUT);
    return { task, metrics: await readMetricsFile(input.absolutePath), inputHash: await hashFile(input.absolutePath) };
  }

  async submitReportDraft(actor: TrustedActor, taskId: string, narrative: string): Promise<{ task: TaskRecord; approval: ApprovalRecord; metrics: ReportMetrics }> {
    this.member(actor);
    const task = this.requireTask(taskId, "report");
    assertOwner(actor, task.ownerId);
    if (task.state !== "running" && task.state !== "stale") throw new Error(`Report task is ${task.state}; regenerate before submitting another draft.`);
    const safeNarrative = narrativeSchema.parse(narrative);
    const input = await requireWorkerFile(this.config.workspaceRoot, REPORT_INPUT);
    const inputHash = await hashFile(input.absolutePath);
    const metrics = await readMetricsFile(input.absolutePath);
    const markdown = renderReport(task, metrics, safeNarrative, inputHash);
    const revision = task.revision;
    const stagedPath = path.join(this.config.draftsRoot, `${task.id}-${revision}.md`);
    await ensureDirectory(this.config.draftsRoot);
    await writeFile(stagedPath, markdown, "utf8");
    const draftHash = await hashFile(stagedPath);
    const updated = this.store.updateTask(task.id, { state: "awaiting_review", inputHash, draftHash, narrative: safeNarrative, metrics });
    this.store.addReportRevision({ taskId: task.id, revision, inputHash, draftHash, stagedPath, metrics, narrative: safeNarrative });
    this.store.cancelApprovalsForTask(task.id);
    const approval = this.store.createApproval({ kind: "report_publish", taskId: task.id, actionIds: [], revision, expectedHash: draftHash, approverId: task.ownerId, workspaceKey: actor.workspaceKey, channelKey: actor.channelKey, conversationKey: actor.conversationKey, policyVersion: this.config.policyVersion, processSession: this.config.processSession, expiresAt: new Date(Date.now() + this.config.approvalTtlSeconds * 1000).toISOString() });
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_draft_ready", payload: { revision, inputHash, draftHash } });
    return { task: updated, approval, metrics };
  }

  async regenerateReport(actor: TrustedActor, taskId: string): Promise<TaskRecord> {
    this.member(actor);
    const task = this.requireTask(taskId, "report");
    assertOwner(actor, task.ownerId);
    if (task.state !== "stale" && task.state !== "awaiting_review") throw new Error(`Report task is ${task.state}; it cannot be regenerated now.`);
    this.store.cancelApprovalsForTask(task.id);
    const next = this.store.updateTask(task.id, { state: "running", revision: task.revision + 1, draftHash: undefined, narrative: undefined, metrics: undefined });
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_regeneration_requested", payload: { revision: next.revision } });
    return next;
  }

  async cancelReport(actor: TrustedActor, taskId: string): Promise<{ task: TaskRecord; rechecked: CleanupOutcome[] }> {
    this.member(actor);
    const task = this.requireTask(taskId, "report");
    assertOwner(actor, task.ownerId);
    if (task.state === "completed" || task.state === "cancelled") return { task, rechecked: [] };
    this.store.cancelApprovalsForTask(task.id);
    const updated = this.store.updateTask(task.id, { state: "cancelled" });
    this.store.releaseDependencies(task.id);
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_cancelled", payload: {} });
    return { task: updated, rechecked: await this.recheckDeferredCleanups() };
  }

  async startCleanup(actor: TrustedActor): Promise<TaskRecord> {
    this.member(actor);
    if (!actor.eventId) throw new Error("The request has no trusted event id and cannot be deduplicated.");
    const inbound = this.store.claimInbound(actor.eventId);
    if (!inbound.isNew && inbound.taskIds[0]) {
      const existing = this.store.getTask(inbound.taskIds[0]);
      if (existing) return existing;
    }
    const task = this.store.createTask({ projectId: this.config.projectId, kind: "cleanup", ownerId: actor.canonicalUserId, ownerLabel: actor.displayName, eventId: actor.eventId, conversationKey: actor.conversationKey });
    this.store.completeInbound(actor.eventId, [task.id]);
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "cleanup_started", payload: {} });
    return task;
  }

  async inventory(actor: TrustedActor, taskId?: string): Promise<FileInventoryItem[]> {
    this.member(actor);
    if (taskId) { const task = this.requireTask(taskId, "cleanup"); assertOwner(actor, task.ownerId); }
    const items: FileInventoryItem[] = [];
    const walk = async (directory: string, prefix: string): Promise<void> => {
      for (const entry of await readdir(directory, { withFileTypes: true })) {
        const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
        if (relative === ".truce-disposable-run.json" || relative.startsWith("reports/")) continue;
        const absolute = path.join(directory, entry.name);
        if (entry.isDirectory()) { await walk(absolute, relative); continue; }
        if (!entry.isFile() || !/\.(csv|log|md)$/i.test(entry.name)) continue;
        try {
          const resolved = await requireWorkerFile(this.config.workspaceRoot, relative);
          const info = await fileInfo(resolved.absolutePath);
          items.push({ fileId: fileId(relative), relativePath: relative, size: info.size, modifiedAt: info.modifiedAt, hash: await hashFile(resolved.absolutePath), kind: entry.name.toLowerCase().endsWith(".csv") ? "csv" : entry.name.toLowerCase().endsWith(".log") ? "log" : "markdown" });
        } catch { /* unsafe filesystem entries are not offered to workers */ }
      }
    };
    await walk(this.config.workspaceRoot, "");
    return items.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
  }

  async proposeCleanup(actor: TrustedActor, proposal: unknown, injected = false): Promise<CleanupOutcome> {
    this.member(actor);
    const parsed = cleanupProposalSchema.parse(proposal);
    const task = this.requireTask(parsed.taskId, "cleanup");
    assertOwner(actor, task.ownerId);
    if (injected) assertDemoOperator(actor, this.config);
    const unique = new Map<string, { fileId: string; reason: string }>();
    for (const candidate of parsed.candidates) {
      const relative = safeRelativePath(candidate.fileId.startsWith("file:") ? candidate.fileId.slice(5) : "");
      if (candidate.fileId !== fileId(relative)) throw new Error("Cleanup proposals must use server-issued file identifiers.");
      unique.set(candidate.fileId, { fileId: candidate.fileId, reason: candidate.reason });
    }
    const verdicts: RefereeVerdict[] = [];
    for (const candidate of unique.values()) {
      const prior = this.store.actionsForTask(task.id).find((action) => action.fileId === candidate.fileId);
      if (prior?.state === "succeeded" || prior?.state === "cancelled") continue;
      const action = this.store.upsertAction({ taskId: task.id, projectId: this.config.projectId, fileId: candidate.fileId, relativePath: safeRelativePath(candidate.fileId.slice(5)), reason: candidate.reason, decision: "BLOCK", state: "blocked", revision: (prior?.revision ?? 0) + 1, blockingTaskIds: [], requiredApproverId: task.ownerId });
      const verdict = await evaluateCandidate({ action, config: this.config, store: this.store });
      this.store.updateAction(action.id, { decision: verdict.decision, state: decisionState(verdict.decision), revision: verdict.revision, expectedHash: verdict.currentHash, blockingTaskIds: verdict.blockingTaskIds, requiredApproverId: verdict.requiredApproverId });
      verdicts.push(verdict);
      if (verdict.decision === "REVIEW") this.ensureCleanupApproval(task, action.id, verdict.revision, verdict.currentHash, actor);
    }
    const updated = this.store.updateTask(task.id, { state: "awaiting_review" });
    const approvals = this.store.approvalsForTask(task.id).filter((approval) => approval.kind === "cleanup" && !approval.cancelledAt && !approval.consumedAt && approval.processSession === this.config.processSession && new Date(approval.expiresAt).getTime() > Date.now());
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: injected ? "injected_cleanup_proposal" : "cleanup_proposed", payload: { candidates: [...unique.keys()], verdicts: verdicts.map(({ relativePath, decision, reasonCode }) => ({ relativePath, decision, reasonCode })) } });
    return { task: updated, verdicts, approvals, injected };
  }

  async injectUnsafeCleanup(actor: TrustedActor): Promise<CleanupOutcome> {
    assertDemoOperator(actor, this.config);
    const task = await this.startCleanup(actor);
    return this.proposeCleanup(actor, { taskId: task.id, candidates: [
      { fileId: fileId(SOURCE_METRICS), reason: "Injected unsafe candidate for the labelled demo." },
      { fileId: fileId(REPORT_INPUT), reason: "Injected active-input candidate for the labelled demo." },
      { fileId: fileId(DEBUG_LOG), reason: "Injected disposable-log candidate for the labelled demo." },
    ] }, true);
  }

  async approveCleanup(actor: TrustedActor, approvalId: string): Promise<import("./types").ExecutionOutcome> {
    return this.serial(async () => {
      const approval = this.store.getApproval(approvalId);
      if (!approval || approval.kind !== "cleanup") return { status: "rejected", message: "That cleanup review no longer exists." };
      const task = this.requireTask(approval.taskId, "cleanup");
      this.member(actor);
      if (approval.approverId !== actor.canonicalUserId) {
        this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "cleanup_approval_rejected_wrong_owner", payload: { approvalId } });
        return { status: "rejected", message: "Only the cleanup requester can approve this file. The pending approval remains available to them." };
      }
      if (approval.processSession !== this.config.processSession || new Date(approval.expiresAt).getTime() <= Date.now()) return { status: "rejected", message: "This review has expired. Ask for status to receive a fresh review." };
      if (approval.conversationKey !== actor.conversationKey || approval.workspaceKey !== actor.workspaceKey || approval.channelKey !== actor.channelKey || approval.policyVersion !== this.config.policyVersion) return { status: "rejected", message: "This approval is bound to a different verified Truce scope." };
      const actionId = approval.actionIds[0];
      if (!actionId) return { status: "rejected", message: "The review did not contain an executable action." };
      const action = this.store.getAction(actionId);
      if (!action || action.taskId !== task.id || action.revision !== approval.revision || action.state !== "pending_review" || action.expectedHash !== approval.expectedHash) return { status: "stale", message: "The file changed or this review is no longer current. Truce requires a fresh evaluation.", action };
      const checked = await evaluateCandidate({ action, config: this.config, store: this.store });
      if (checked.decision === "DEFER") {
        const next = this.store.updateAction(action.id, { decision: "DEFER", state: "deferred", revision: action.revision + 1, blockingTaskIds: checked.blockingTaskIds });
        return { status: "deferred", message: "The file now supports active work, so cleanup was deferred.", action: next };
      }
      if (checked.decision !== "REVIEW" || checked.currentHash !== approval.expectedHash) {
        const next = this.store.updateAction(action.id, { decision: "BLOCK", state: "stale", revision: action.revision + 1, expectedHash: checked.currentHash });
        return { status: "stale", message: "The file changed or is no longer eligible. No file was moved.", action: next };
      }
      const expectedHash = approval.expectedHash;
      if (!expectedHash) return { status: "rejected", message: "The approval did not include a file hash." };
      if (!this.store.consumeApproval(approval.id, "approve")) {
        const settledAction = this.store.getAction(action.id);
        const op = this.findOperationForAction(action.id);
        return { status: "already_settled", action: settledAction, operation: op };
      }
      this.store.updateAction(action.id, { state: "executing" });
      const source = await requireWorkerFile(this.config.workspaceRoot, action.relativePath);
      const destination = path.join(this.config.quarantineRoot, task.id, `${action.id}-${path.basename(source.safePath)}`);
      await ensureDirectory(path.dirname(destination));
      const operation = this.store.createOperation({ id: randomUUID(), kind: "quarantine", taskId: task.id, actionId: action.id, sourcePath: source.absolutePath, destinationPath: destination, expectedHash, state: "prepared" });
      try {
        this.store.updateOperation(operation.id, "executing");
        await moveSameFilesystem(source.absolutePath, destination);
        const verified = await verifyHash(destination, operation.expectedHash);
        const sourceStillThere = await lstat(source.absolutePath).then(() => true, () => false);
        if (!verified || sourceStillThere) throw new Error("Quarantine verification did not match the reviewed bytes.");
        const receiptId = `quarantine:${operation.id}`;
        const verifiedOperation = this.store.updateOperation(operation.id, "verified", receiptId);
        const succeeded = this.store.updateAction(action.id, { state: "succeeded", decision: "ALLOW" });
        this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actionId: action.id, actorId: actor.canonicalUserId, eventType: "cleanup_succeeded", payload: { originalPath: action.relativePath, receiptId, expectedHash: operation.expectedHash } });
        return { status: "succeeded", action: succeeded, operation: verifiedOperation };
      } catch (error) {
        const sourceExists = await lstat(source.absolutePath).then(() => true, () => false);
        const destinationExists = await lstat(destination).then(() => true, () => false);
        const uncertain = sourceExists === destinationExists || (destinationExists && !(await verifyHash(destination, operation.expectedHash)));
        this.store.updateOperation(operation.id, uncertain ? "uncertain" : "failed");
        this.store.updateAction(action.id, { state: uncertain ? "uncertain" : "failed" });
        this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actionId: action.id, actorId: actor.canonicalUserId, eventType: uncertain ? "cleanup_uncertain" : "cleanup_failed", payload: { message: error instanceof Error ? error.message : "Filesystem operation failed." } });
        return { status: uncertain ? "uncertain" : "failed", message: error instanceof Error ? error.message : "Filesystem operation failed.", action: this.store.getAction(action.id) };
      }
    });
  }

  async keepCleanup(actor: TrustedActor, taskId: string): Promise<TaskRecord> {
    this.member(actor);
    const task = this.requireTask(taskId, "cleanup");
    assertOwner(actor, task.ownerId);
    for (const action of this.store.actionsForTask(task.id)) if (action.state !== "succeeded") this.store.updateAction(action.id, { state: "cancelled", decision: "BLOCK" });
    this.store.cancelApprovalsForTask(task.id);
    const updated = this.store.updateTask(task.id, { state: "cancelled" });
    this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "cleanup_cancelled", payload: {} });
    return updated;
  }

  async publishReport(actor: TrustedActor, approvalId: string): Promise<PublicationOutcome> {
    return this.serial(async () => {
      const approval = this.store.getApproval(approvalId);
      if (!approval || approval.kind !== "report_publish") return { status: "rejected", message: "That report review no longer exists." };
      const task = this.requireTask(approval.taskId, "report");
      this.member(actor);
      if (task.ownerId !== actor.canonicalUserId) return { status: "rejected", message: "Only the report owner can publish this report." };
      if (approval.processSession !== this.config.processSession || new Date(approval.expiresAt).getTime() <= Date.now()) return { status: "rejected", message: "This report review has expired. Ask for status to receive a fresh review." };
      const revision = this.store.latestReportRevision(task.id);
      if (!revision || revision.revision !== approval.revision || revision.draftHash !== approval.expectedHash || task.state !== "awaiting_review") return { status: "stale", message: "The reviewed report revision is no longer current." , task };
      const input = await requireWorkerFile(this.config.workspaceRoot, REPORT_INPUT);
      if (await hashFile(input.absolutePath) !== revision.inputHash || !(await verifyHash(revision.stagedPath, revision.draftHash))) {
        const stale = this.store.updateTask(task.id, { state: "stale" });
        this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_publication_stale", payload: {} });
        return { status: "stale", message: "The input or draft bytes changed. Regenerate the report before publishing.", task: stale };
      }
      if (!this.store.consumeApproval(approval.id, "approve")) return { status: "stale", message: "This report approval has already been settled." , task };
      this.store.updateTask(task.id, { state: "publishing" });
      const destination = path.join(this.config.reportsRoot, `${task.id}.md`);
      const operation = this.store.createOperation({ id: randomUUID(), kind: "publication", taskId: task.id, sourcePath: revision.stagedPath, destinationPath: destination, expectedHash: revision.draftHash, state: "prepared" });
      try {
        await ensureDirectory(this.config.reportsRoot);
        if (await lstat(destination).then(() => true, () => false)) throw new Error("The server-generated report destination already exists.");
        this.store.updateOperation(operation.id, "executing");
        await copyFile(revision.stagedPath, destination);
        if (!(await verifyHash(destination, operation.expectedHash))) throw new Error("Published report verification failed.");
        const verifiedOperation = this.store.updateOperation(operation.id, "verified", `publication:${operation.id}`);
        this.store.setPublicationOperation(task.id, operation.id);
        const completed = this.store.updateTask(task.id, { state: "completed" });
        this.store.releaseDependencies(task.id);
        this.store.recordAudit({ projectId: this.config.projectId, taskId: task.id, actorId: actor.canonicalUserId, eventType: "report_published", payload: { reportPath: path.relative(this.config.workspaceRoot, destination), draftHash: operation.expectedHash, operationId: operation.id } });
        return { status: "succeeded", task: completed, operation: verifiedOperation, rechecked: await this.recheckDeferredCleanups() };
      } catch (error) {
        const present = await lstat(destination).then(() => true, () => false);
        this.store.updateOperation(operation.id, present ? "uncertain" : "failed");
        const failed = this.store.updateTask(task.id, { state: present ? "failed" : "failed", error: error instanceof Error ? error.message : "Publication failed." });
        return { status: present ? "uncertain" : "failed", message: error instanceof Error ? error.message : "Publication failed.", task: failed };
      }
    });
  }

  async recheckDeferredCleanups(): Promise<CleanupOutcome[]> {
    const outcomes: CleanupOutcome[] = [];
    for (const task of this.store.listTasks(this.config.projectId).filter((item) => item.kind === "cleanup" && item.state !== "cancelled" && item.state !== "completed")) {
      const changedVerdicts: RefereeVerdict[] = [];
      for (const action of this.store.actionsForTask(task.id).filter((item) => item.state === "deferred" || item.state === "pending_review")) {
        const verdict = await evaluateCandidate({ action, config: this.config, store: this.store });
        const materiallyChanged = verdict.decision !== action.decision || verdict.currentHash !== action.expectedHash || JSON.stringify(verdict.blockingTaskIds) !== JSON.stringify(action.blockingTaskIds);
        const nextRevision = materiallyChanged ? action.revision + 1 : action.revision;
        this.store.updateAction(action.id, { decision: verdict.decision, state: decisionState(verdict.decision), revision: nextRevision, expectedHash: verdict.currentHash, blockingTaskIds: verdict.blockingTaskIds });
        if (materiallyChanged) {
          changedVerdicts.push({ ...verdict, revision: nextRevision });
          if (verdict.decision === "REVIEW") this.ensureCleanupApproval(task, action.id, nextRevision, verdict.currentHash, { canonicalUserId: task.ownerId, displayName: task.ownerLabel, workspaceKey: this.config.allowedWorkspaceKey, channelKey: this.config.allowedChannelKey, conversationKey: task.conversationKey, eventId: task.eventId, scopeVerified: true });
        }
      }
      if (changedVerdicts.length > 0) outcomes.push({ task: this.store.getTask(task.id) as TaskRecord, verdicts: changedVerdicts, approvals: this.store.approvalsForTask(task.id).filter((approval) => approval.kind === "cleanup" && !approval.cancelledAt && !approval.consumedAt && approval.processSession === this.config.processSession), injected: false });
    }
    return outcomes;
  }

  async status(): Promise<StatusSnapshot> {
    const files: StatusSnapshot["files"] = [];
    const problems: string[] = [];
    for (const relativePath of [SOURCE_METRICS, REPORT_INPUT, DEBUG_LOG]) {
      const resolved = await resolveWorkerFile(this.config.workspaceRoot, relativePath, false);
      if (!resolved) { files.push({ relativePath, exists: false }); problems.push(`Missing ${relativePath}`); continue; }
      files.push({ relativePath, exists: true, hash: await hashFile(resolved.absolutePath), size: resolved.info.size });
    }
    for (const action of this.store.actionsForTask(this.store.listTasks(this.config.projectId).find((task) => task.kind === "cleanup")?.id ?? "")) {
      if (action.state === "succeeded") {
        const original = await resolveWorkerFile(this.config.workspaceRoot, action.relativePath, false);
        if (original) problems.push(`Succeeded cleanup source still exists: ${action.relativePath}`);
      }
    }
    return { projectId: this.config.projectId, runRoot: this.config.workspaceRoot, files, tasks: this.store.listTasks(this.config.projectId), actions: this.store.listTasks(this.config.projectId).flatMap((task) => task.kind === "cleanup" ? this.store.actionsForTask(task.id) : []), activeDependencies: this.store.dependenciesFor(this.config.projectId, true), unfinishedOperations: this.store.unfinishedOperations(), consistent: problems.length === 0, problems };
  }

  async reconcileOperations(): Promise<OperationRecord[]> {
    const recovered: OperationRecord[] = [];
    for (const operation of this.store.unfinishedOperations()) {
      const sourceExists = await lstat(operation.sourcePath).then(() => true, () => false);
      const destinationExists = await lstat(operation.destinationPath).then(() => true, () => false);
      const destinationMatches = destinationExists && await verifyHash(operation.destinationPath, operation.expectedHash);
      if (operation.kind === "quarantine" && !sourceExists && destinationMatches) {
        const verified = this.store.updateOperation(operation.id, "verified", `quarantine:${operation.id}`);
        if (operation.actionId) this.store.updateAction(operation.actionId, { state: "succeeded", decision: "ALLOW" });
        recovered.push(verified);
      } else if (operation.kind === "quarantine" && sourceExists && !destinationExists && await verifyHash(operation.sourcePath, operation.expectedHash)) {
        this.store.updateOperation(operation.id, "failed");
        if (operation.actionId) {
          const action = this.store.getAction(operation.actionId);
          if (action) this.store.updateAction(action.id, { state: "stale", decision: "REVIEW", revision: action.revision + 1, expectedHash: operation.expectedHash });
        }
      } else {
        recovered.push(this.store.updateOperation(operation.id, "uncertain"));
        if (operation.actionId) this.store.updateAction(operation.actionId, { state: "uncertain" });
      }
    }
    return recovered;
  }

  explainDecision(verdicts: RefereeVerdict[]): string {
    const blocked = verdicts.filter((item) => item.decision === "BLOCK");
    const deferred = verdicts.filter((item) => item.decision === "DEFER");
    const review = verdicts.filter((item) => item.decision === "REVIEW");
    return [
      blocked.length ? `Blocked: ${blocked.map((item) => `${item.relativePath} (${item.reason})`).join("; ")}.` : "",
      deferred.length ? `Waiting: ${deferred.map((item) => `${item.relativePath} (${item.reason})`).join("; ")}.` : "",
      review.length ? `Ready for the cleanup owner's approval: ${review.map((item) => item.relativePath).join(", ")}.` : "",
    ].filter(Boolean).join("\n");
  }

  private ensureCleanupApproval(task: TaskRecord, actionId: string, revision: number, expectedHash: string | undefined, actor: TrustedActor): ApprovalRecord {
    const existing = this.store.approvalsForTask(task.id).find((approval) => approval.kind === "cleanup" && approval.actionIds.length === 1 && approval.actionIds[0] === actionId && approval.revision === revision && approval.expectedHash === expectedHash && approval.processSession === this.config.processSession && !approval.cancelledAt && !approval.consumedAt && new Date(approval.expiresAt).getTime() > Date.now());
    if (existing) return existing;
    return this.store.createApproval({ kind: "cleanup", taskId: task.id, actionIds: [actionId], revision, expectedHash, approverId: task.ownerId, workspaceKey: actor.workspaceKey, channelKey: actor.channelKey, conversationKey: task.conversationKey, policyVersion: this.config.policyVersion, processSession: this.config.processSession, expiresAt: new Date(Date.now() + this.config.approvalTtlSeconds * 1000).toISOString() });
  }

  private requireTask(taskId: string, kind: TaskRecord["kind"]): TaskRecord {
    const task = this.store.getTask(taskId);
    if (!task || task.projectId !== this.config.projectId || task.kind !== kind) throw new Error("Unknown Truce task.");
    return task;
  }

  private findOperationForAction(actionId: string): OperationRecord | undefined {
    return this.store.operationForAction(actionId);
  }
}

function renderReport(task: TaskRecord, metrics: ReportMetrics, narrative: string, inputHash: string): string {
  return `# Client update: ${metrics.fromPeriod} to ${metrics.toPeriod}\n\n${renderMetricsTable(metrics)}\n\n## Narrative\n\n${narrative}\n\n## Provenance\n\n- Task: ${task.id}\n- Revision: ${task.revision}\n- Input: ${REPORT_INPUT}\n- Input SHA-256: ${inputHash}\n- Published by Truce after owner approval.\n`;
}
