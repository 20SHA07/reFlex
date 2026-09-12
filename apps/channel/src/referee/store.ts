import { randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import Database from "better-sqlite3";
import { migrate } from "./migrations";
import type {
  ApprovalRecord,
  CleanupAction,
  Decision,
  DependencyRecord,
  OperationRecord,
  ProjectConfig,
  ReportMetrics,
  TaskKind,
  TaskRecord,
  TaskState,
} from "./types";

type Row = Record<string, unknown>;
const value = (row: Row, key: string): unknown => row[key];
const text = (row: Row, key: string): string => String(value(row, key) ?? "");
const number = (row: Row, key: string): number => Number(value(row, key));
const bool = (row: Row, key: string): boolean => Boolean(Number(value(row, key)));
const json = <T>(row: Row, key: string, fallback: T): T => {
  try { return JSON.parse(text(row, key)) as T; } catch { return fallback; }
};

export class TruceStore {
  readonly db: Database.Database;

  constructor(readonly dbPath: string) {
    this.db = new Database(dbPath);
    migrate(this.db);
  }

  static async open(dbPath: string): Promise<TruceStore> {
    await mkdir(path.dirname(dbPath), { recursive: true });
    return new TruceStore(dbPath);
  }

  close(): void { this.db.close(); }

  transaction<T>(fn: () => T): T {
    return this.db.transaction(fn)();
  }

  ensureProject(config: ProjectConfig): void {
    this.db.prepare(`INSERT INTO projects
      (id, root_path, workspace_key, channel_key, conversation_key, policy_version, member_ids_json, demo_operator_ids_json, created_at)
      VALUES (@id, @rootPath, @workspaceKey, @channelKey, @conversationKey, @policyVersion, @members, @operators, @createdAt)
      ON CONFLICT(id) DO UPDATE SET root_path=@rootPath, workspace_key=@workspaceKey,
      channel_key=@channelKey, conversation_key=@conversationKey, policy_version=@policyVersion,
      member_ids_json=@members, demo_operator_ids_json=@operators`)
      .run({
        id: config.projectId,
        rootPath: config.workspaceRoot,
        workspaceKey: config.allowedWorkspaceKey,
        channelKey: config.allowedChannelKey,
        conversationKey: config.allowedConversationKey,
        policyVersion: config.policyVersion,
        members: JSON.stringify([...config.memberIds]),
        operators: JSON.stringify([...config.demoOperatorIds]),
        createdAt: new Date().toISOString(),
      });
  }

  getTask(taskId: string): TaskRecord | undefined {
    const row = this.db.prepare("SELECT * FROM tasks WHERE id = ?").get(taskId) as Row | undefined;
    return row ? task(row) : undefined;
  }

  listTasks(projectId: string): TaskRecord[] {
    return (this.db.prepare("SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at").all(projectId) as Row[]).map(task);
  }

  findActiveReport(projectId: string): TaskRecord | undefined {
    const row = this.db.prepare("SELECT * FROM tasks WHERE project_id=? AND kind='report' AND state NOT IN ('completed','cancelled') ORDER BY created_at DESC LIMIT 1").get(projectId) as Row | undefined;
    return row ? task(row) : undefined;
  }

  createTask(input: {
    projectId: string; kind: TaskKind; ownerId: string; ownerLabel: string; eventId: string;
    conversationKey: string; inputFileId?: string; inputPath?: string; inputHash?: string;
  }): TaskRecord {
    const now = new Date().toISOString();
    const record: TaskRecord = {
      id: randomUUID(), projectId: input.projectId, kind: input.kind, ownerId: input.ownerId,
      ownerLabel: input.ownerLabel, state: "running", revision: 1, eventId: input.eventId,
      conversationKey: input.conversationKey, inputFileId: input.inputFileId, inputPath: input.inputPath,
      inputHash: input.inputHash, createdAt: now, updatedAt: now,
    };
    this.db.prepare(`INSERT INTO tasks
      (id, project_id, kind, owner_id, owner_label, state, revision, event_id, conversation_key, input_file_id, input_path, input_hash, created_at, updated_at)
      VALUES (@id,@projectId,@kind,@ownerId,@ownerLabel,@state,@revision,@eventId,@conversationKey,@inputFileId,@inputPath,@inputHash,@createdAt,@updatedAt)`).run({
        ...record, projectId: input.projectId, inputFileId: input.inputFileId ?? null,
        inputPath: input.inputPath ?? null, inputHash: input.inputHash ?? null,
      });
    return record;
  }

  updateTask(taskId: string, patch: Partial<Pick<TaskRecord, "state" | "revision" | "inputHash" | "draftHash" | "narrative" | "error" | "metrics">>): TaskRecord {
    const current = this.getTask(taskId);
    if (!current) throw new Error("Task not found.");
    const next = { ...current, ...patch, updatedAt: new Date().toISOString() };
    this.db.prepare(`UPDATE tasks SET state=@state, revision=@revision, input_hash=@inputHash,
      draft_hash=@draftHash, narrative=@narrative, metrics_json=@metrics, error=@error, updated_at=@updatedAt WHERE id=@id`).run({
      id: taskId, state: next.state, revision: next.revision,
      inputHash: next.inputHash ?? null, draftHash: next.draftHash ?? null,
      narrative: next.narrative ?? null, metrics: next.metrics ? JSON.stringify(next.metrics) : null,
      error: next.error ?? null, updatedAt: next.updatedAt,
    });
    return next;
  }

  addDependency(input: { projectId: string; taskId: string; fileId: string; relativePath: string; observedHash: string; reason: string }): DependencyRecord {
    const record: DependencyRecord = { id: randomUUID(), ...input, active: true };
    this.db.prepare(`INSERT INTO dependencies (id,project_id,task_id,file_id,relative_path,observed_hash,active,reason)
      VALUES (@id,@projectId,@taskId,@fileId,@relativePath,@observedHash,1,@reason)`).run(record);
    return record;
  }

  dependenciesFor(projectId: string, activeOnly = false): DependencyRecord[] {
    const query = activeOnly ? "SELECT * FROM dependencies WHERE project_id=? AND active=1" : "SELECT * FROM dependencies WHERE project_id=?";
    return (this.db.prepare(query).all(projectId) as Row[]).map((row) => ({
      id: text(row, "id"), projectId: text(row, "project_id"), taskId: text(row, "task_id"),
      fileId: text(row, "file_id"), relativePath: text(row, "relative_path"), observedHash: text(row, "observed_hash"),
      active: bool(row, "active"), reason: text(row, "reason"),
    }));
  }

  releaseDependencies(taskId: string): void {
    this.db.prepare("UPDATE dependencies SET active=0 WHERE task_id=? AND active=1").run(taskId);
  }

  addReportRevision(input: { taskId: string; revision: number; inputHash: string; draftHash: string; stagedPath: string; metrics: ReportMetrics; narrative: string }): void {
    this.db.prepare(`INSERT INTO report_revisions (task_id,revision,input_hash,draft_hash,staged_path,metrics_json,narrative,created_at)
      VALUES (@taskId,@revision,@inputHash,@draftHash,@stagedPath,@metrics,@narrative,@createdAt)`).run({
      ...input, metrics: JSON.stringify(input.metrics), createdAt: new Date().toISOString(),
    });
  }

  latestReportRevision(taskId: string): { revision: number; inputHash: string; draftHash: string; stagedPath: string; metrics: ReportMetrics; narrative: string } | undefined {
    const row = this.db.prepare("SELECT * FROM report_revisions WHERE task_id=? ORDER BY revision DESC LIMIT 1").get(taskId) as Row | undefined;
    if (!row) return undefined;
    return { revision: number(row, "revision"), inputHash: text(row, "input_hash"), draftHash: text(row, "draft_hash"), stagedPath: text(row, "staged_path"), metrics: json<ReportMetrics>(row, "metrics_json", {} as ReportMetrics), narrative: text(row, "narrative") };
  }

  setPublicationOperation(taskId: string, operationId: string): void {
    this.db.prepare("UPDATE report_revisions SET publication_operation_id=? WHERE task_id=? AND revision=(SELECT revision FROM tasks WHERE id=?)").run(operationId, taskId, taskId);
  }

  getAction(actionId: string): CleanupAction | undefined {
    const row = this.db.prepare("SELECT * FROM cleanup_actions WHERE id=?").get(actionId) as Row | undefined;
    return row ? action(row) : undefined;
  }

  actionsForTask(taskId: string): CleanupAction[] {
    return (this.db.prepare("SELECT * FROM cleanup_actions WHERE cleanup_task_id=? ORDER BY relative_path").all(taskId) as Row[]).map(action);
  }

  upsertAction(input: { taskId: string; projectId: string; fileId: string; relativePath: string; reason: string; decision: Decision; state: CleanupAction["state"]; revision: number; expectedHash?: string; blockingTaskIds: string[]; requiredApproverId?: string }): CleanupAction {
    const existing = this.db.prepare("SELECT * FROM cleanup_actions WHERE cleanup_task_id=? AND file_id=?").get(input.taskId, input.fileId) as Row | undefined;
    const id = existing ? text(existing, "id") : randomUUID();
    const now = new Date().toISOString();
    this.db.prepare(`INSERT INTO cleanup_actions
      (id,cleanup_task_id,project_id,file_id,relative_path,proposed_reason,decision,state,revision,expected_hash,blocking_task_ids_json,required_approver_id,updated_at)
      VALUES (@id,@taskId,@projectId,@fileId,@relativePath,@reason,@decision,@state,@revision,@expectedHash,@blocking,@approver,@updatedAt)
      ON CONFLICT(cleanup_task_id,file_id) DO UPDATE SET proposed_reason=@reason, decision=@decision,
      state=@state, revision=@revision, expected_hash=@expectedHash, blocking_task_ids_json=@blocking,
      required_approver_id=@approver, updated_at=@updatedAt`).run({
      id, ...input, expectedHash: input.expectedHash ?? null, blocking: JSON.stringify(input.blockingTaskIds), approver: input.requiredApproverId ?? null, updatedAt: now,
    });
    return this.getAction(id) as CleanupAction;
  }

  updateAction(actionId: string, patch: Partial<Pick<CleanupAction, "decision" | "state" | "revision" | "expectedHash" | "blockingTaskIds" | "requiredApproverId">>): CleanupAction {
    const current = this.getAction(actionId);
    if (!current) throw new Error("Cleanup action not found.");
    const next = { ...current, ...patch, updatedAt: new Date().toISOString() };
    this.db.prepare(`UPDATE cleanup_actions SET decision=@decision,state=@state,revision=@revision,
      expected_hash=@expectedHash,blocking_task_ids_json=@blocking,required_approver_id=@approver,updated_at=@updatedAt WHERE id=@id`).run({
      id: actionId, decision: next.decision, state: next.state, revision: next.revision,
      expectedHash: next.expectedHash ?? null, blocking: JSON.stringify(next.blockingTaskIds), approver: next.requiredApproverId ?? null, updatedAt: next.updatedAt,
    });
    return next;
  }

  createApproval(input: Omit<ApprovalRecord, "id">): ApprovalRecord {
    const record: ApprovalRecord = { id: randomUUID(), ...input };
    this.db.prepare(`INSERT INTO approvals
      (id,kind,task_id,action_ids_json,revision,expected_hash,approver_id,workspace_key,channel_key,conversation_key,policy_version,process_session,expires_at,consumed_at,cancelled_at,decision)
      VALUES (@id,@kind,@taskId,@actionIds,@revision,@expectedHash,@approverId,@workspaceKey,@channelKey,@conversationKey,@policyVersion,@processSession,@expiresAt,@consumedAt,@cancelledAt,@decision)`).run({
      id: record.id, kind: record.kind, taskId: record.taskId, actionIds: JSON.stringify(record.actionIds), revision: record.revision,
      expectedHash: record.expectedHash ?? null, approverId: record.approverId, workspaceKey: record.workspaceKey, channelKey: record.channelKey,
      conversationKey: record.conversationKey, policyVersion: record.policyVersion, processSession: record.processSession,
      expiresAt: record.expiresAt, consumedAt: record.consumedAt ?? null, cancelledAt: record.cancelledAt ?? null, decision: record.decision ?? null,
    });
    return record;
  }

  getApproval(approvalId: string): ApprovalRecord | undefined {
    const row = this.db.prepare("SELECT * FROM approvals WHERE id=?").get(approvalId) as Row | undefined;
    return row ? approval(row) : undefined;
  }

  approvalsForTask(taskId: string): ApprovalRecord[] {
    return (this.db.prepare("SELECT * FROM approvals WHERE task_id=? ORDER BY expires_at").all(taskId) as Row[]).map(approval);
  }

  expireApprovalsFromOtherSessions(session: string): void {
    this.db.prepare("UPDATE approvals SET cancelled_at=COALESCE(cancelled_at,?), decision=COALESCE(decision,'expired') WHERE process_session<>? AND consumed_at IS NULL AND cancelled_at IS NULL").run(new Date().toISOString(), session);
  }

  cancelApprovalsForTask(taskId: string): void {
    this.db.prepare("UPDATE approvals SET cancelled_at=COALESCE(cancelled_at,?) WHERE task_id=? AND consumed_at IS NULL").run(new Date().toISOString(), taskId);
  }

  consumeApproval(approvalId: string, decision: "approve" | "decline"): boolean {
    const result = this.db.prepare("UPDATE approvals SET consumed_at=?, decision=? WHERE id=? AND consumed_at IS NULL AND cancelled_at IS NULL").run(new Date().toISOString(), decision, approvalId);
    return result.changes === 1;
  }

  createOperation(input: Omit<OperationRecord, "receiptId">): OperationRecord {
    const record: OperationRecord = { ...input };
    this.db.prepare(`INSERT INTO fs_operations (id,kind,task_id,action_id,source_path,destination_path,expected_hash,state,created_at,updated_at)
      VALUES (@id,@kind,@taskId,@actionId,@sourcePath,@destinationPath,@expectedHash,@state,@createdAt,@updatedAt)`).run({
      ...record, actionId: record.actionId ?? null, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    return record;
  }

  getOperation(operationId: string): OperationRecord | undefined {
    const row = this.db.prepare("SELECT * FROM fs_operations WHERE id=?").get(operationId) as Row | undefined;
    return row ? operation(row) : undefined;
  }

  unfinishedOperations(): OperationRecord[] {
    return (this.db.prepare("SELECT * FROM fs_operations WHERE state IN ('prepared','executing') ORDER BY created_at").all() as Row[]).map(operation);
  }

  operationForAction(actionId: string): OperationRecord | undefined {
    const row = this.db.prepare("SELECT * FROM fs_operations WHERE action_id=? ORDER BY created_at DESC LIMIT 1").get(actionId) as Row | undefined;
    return row ? operation(row) : undefined;
  }

  updateOperation(operationId: string, state: OperationRecord["state"], receiptId?: string): OperationRecord {
    this.db.prepare("UPDATE fs_operations SET state=?, receipt_id=?, updated_at=? WHERE id=?").run(state, receiptId ?? null, new Date().toISOString(), operationId);
    return this.getOperation(operationId) as OperationRecord;
  }

  recordAudit(input: { projectId: string; taskId?: string; actionId?: string; actorId: string; eventType: string; payload: Record<string, unknown> }): void {
    this.db.prepare(`INSERT INTO audit_events (id,project_id,task_id,action_id,actor_id,event_type,payload_json,created_at)
      VALUES (@id,@projectId,@taskId,@actionId,@actorId,@eventType,@payload,@createdAt)`).run({
      id: randomUUID(), projectId: input.projectId, taskId: input.taskId ?? null, actionId: input.actionId ?? null,
      actorId: input.actorId, eventType: input.eventType, payload: JSON.stringify(input.payload), createdAt: new Date().toISOString(),
    });
  }

  claimInbound(eventKey: string): { isNew: boolean; taskIds: string[] } {
    const existing = this.db.prepare("SELECT * FROM inbound_events WHERE provider_event_key=?").get(eventKey) as Row | undefined;
    if (existing) return { isNew: false, taskIds: json<string[]>(existing, "resulting_task_ids_json", []) };
    this.db.prepare("INSERT INTO inbound_events(provider_event_key,processing_state,resulting_task_ids_json,created_at) VALUES (?, 'processing', '[]', ?)").run(eventKey, new Date().toISOString());
    return { isNew: true, taskIds: [] };
  }

  completeInbound(eventKey: string, taskIds: string[]): void {
    this.db.prepare("UPDATE inbound_events SET processing_state='complete', resulting_task_ids_json=? WHERE provider_event_key=?").run(JSON.stringify(taskIds), eventKey);
  }
}

function task(row: Row): TaskRecord {
  const metrics = value(row, "metrics_json");
  return {
    id: text(row, "id"), projectId: text(row, "project_id"), kind: text(row, "kind") as TaskKind,
    ownerId: text(row, "owner_id"), ownerLabel: text(row, "owner_label"), state: text(row, "state") as TaskState,
    revision: number(row, "revision"), eventId: text(row, "event_id"), conversationKey: text(row, "conversation_key"),
    inputFileId: value(row, "input_file_id") ? text(row, "input_file_id") : undefined,
    inputPath: value(row, "input_path") ? text(row, "input_path") : undefined,
    inputHash: value(row, "input_hash") ? text(row, "input_hash") : undefined,
    draftHash: value(row, "draft_hash") ? text(row, "draft_hash") : undefined,
    narrative: value(row, "narrative") ? text(row, "narrative") : undefined,
    metrics: metrics ? json<ReportMetrics>(row, "metrics_json", {} as ReportMetrics) : undefined,
    error: value(row, "error") ? text(row, "error") : undefined, createdAt: text(row, "created_at"), updatedAt: text(row, "updated_at"),
  };
}

function action(row: Row): CleanupAction {
  return { id: text(row, "id"), taskId: text(row, "cleanup_task_id"), projectId: text(row, "project_id"), fileId: text(row, "file_id"), relativePath: text(row, "relative_path"), reason: text(row, "proposed_reason"), decision: text(row, "decision") as Decision, state: text(row, "state") as CleanupAction["state"], revision: number(row, "revision"), expectedHash: value(row, "expected_hash") ? text(row, "expected_hash") : undefined, blockingTaskIds: json<string[]>(row, "blocking_task_ids_json", []), requiredApproverId: value(row, "required_approver_id") ? text(row, "required_approver_id") : undefined, updatedAt: text(row, "updated_at") };
}

function approval(row: Row): ApprovalRecord {
  return { id: text(row, "id"), kind: text(row, "kind") as ApprovalRecord["kind"], taskId: text(row, "task_id"), actionIds: json<string[]>(row, "action_ids_json", []), revision: number(row, "revision"), expectedHash: value(row, "expected_hash") ? text(row, "expected_hash") : undefined, approverId: text(row, "approver_id"), workspaceKey: text(row, "workspace_key"), channelKey: text(row, "channel_key"), conversationKey: text(row, "conversation_key"), policyVersion: text(row, "policy_version"), processSession: text(row, "process_session"), expiresAt: text(row, "expires_at"), consumedAt: value(row, "consumed_at") ? text(row, "consumed_at") : undefined, cancelledAt: value(row, "cancelled_at") ? text(row, "cancelled_at") : undefined, decision: value(row, "decision") ? text(row, "decision") as ApprovalRecord["decision"] : undefined };
}

function operation(row: Row): OperationRecord {
  return { id: text(row, "id"), kind: text(row, "kind") as OperationRecord["kind"], taskId: text(row, "task_id"), actionId: value(row, "action_id") ? text(row, "action_id") : undefined, sourcePath: text(row, "source_path"), destinationPath: text(row, "destination_path"), expectedHash: text(row, "expected_hash"), state: text(row, "state") as OperationRecord["state"], receiptId: value(row, "receipt_id") ? text(row, "receipt_id") : undefined };
}
