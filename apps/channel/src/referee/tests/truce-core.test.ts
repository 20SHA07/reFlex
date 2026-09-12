import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, it } from "node:test";
import { createDisposableRun, fileId, hashFile, requireWorkerFile } from "../paths";
import { localConfigFromPaths } from "../config";
import { parseMetricsCsv } from "../metrics";
import { TruceCoordinator } from "../coordinator";
import { TruceStore } from "../store";
import { testActor } from "../identity";

type Harness = { root: string; coordinator: TruceCoordinator; store: TruceStore; paths: Awaited<ReturnType<typeof createDisposableRun>> };

async function harness(overrides: Parameters<typeof localConfigFromPaths>[1] = {}): Promise<Harness> {
  const root = await mkdtemp(path.join(os.tmpdir(), "truce-test-"));
  const paths = await createDisposableRun(path.join(root, "data"));
  const config = localConfigFromPaths(paths, overrides);
  const store = await TruceStore.open(config.dbPath);
  return { root, paths, store, coordinator: new TruceCoordinator(config, store) };
}

async function close(h: Harness): Promise<void> { h.coordinator.close(); await rm(h.root, { recursive: true, force: true }); }

describe("Truce referee P0 domain", () => {
  it("blocks protected source, defers active input, reviews the log, and executes only the approved file", async () => {
    const h = await harness();
    try {
      const alex = testActor("slack:alex", "Alex", "report-1");
      const sam = testActor("slack:sam", "Sam", "cleanup-1");
      const report = await h.coordinator.startReport(alex);
      const cleanup = await h.coordinator.startCleanup(sam);
      const outcome = await h.coordinator.proposeCleanup(sam, { taskId: cleanup.id, candidates: [
        { fileId: fileId("data/source_metrics.csv"), reason: "source" },
        { fileId: fileId("working/report_input.csv"), reason: "working input" },
        { fileId: fileId("scratch/debug.log"), reason: "temporary debug log" },
      ] });
      assert.deepEqual(Object.fromEntries(outcome.verdicts.map((item) => [item.relativePath, item.decision])), {
        "data/source_metrics.csv": "BLOCK", "scratch/debug.log": "REVIEW", "working/report_input.csv": "DEFER",
      });
      const sourceHash = await hashFile(path.join(h.paths.workspaceRoot, "data/source_metrics.csv"));
      const approval = outcome.approvals.find((item) => item.actionIds.length === 1);
      assert.ok(approval);
      const wrong = await h.coordinator.approveCleanup(alex, approval.id);
      assert.equal(wrong.status, "rejected");
      assert.equal(h.store.getApproval(approval.id)?.consumedAt, undefined);
      const moved = await h.coordinator.approveCleanup(sam, approval.id);
      assert.equal(moved.status, "succeeded");
      if (moved.status !== "succeeded") return;
      assert.equal(await stat(moved.operation.destinationPath).then(() => true, () => false), true);
      assert.equal(await stat(path.join(h.paths.workspaceRoot, "scratch/debug.log")).then(() => true, () => false), false);
      assert.equal(await hashFile(path.join(h.paths.workspaceRoot, "data/source_metrics.csv")), sourceHash);
      assert.equal((await h.coordinator.approveCleanup(sam, approval.id)).status, "stale");
      assert.equal(h.store.operationForAction(moved.action.id)?.state, "verified");
      assert.equal(h.store.listTasks(h.coordinator.config.projectId).some((task) => task.id === report.id), true);
    } finally { await close(h); }
  });

  it("publishes a reviewed report, releases its dependency, and creates a fresh deferred approval", async () => {
    const h = await harness();
    try {
      const alex = testActor("slack:alex", "Alex", "report-2");
      const sam = testActor("slack:sam", "Sam", "cleanup-2");
      const report = await h.coordinator.startReport(alex);
      const cleanup = await h.coordinator.startCleanup(sam);
      const pending = await h.coordinator.proposeCleanup(sam, { taskId: cleanup.id, candidates: [{ fileId: fileId("working/report_input.csv"), reason: "temporary copy" }] });
      assert.equal(pending.verdicts[0]?.decision, "DEFER");
      const draft = await h.coordinator.submitReportDraft(alex, report.id, "Revenue and closed deals increased while open tickets declined in the compared periods.");
      const published = await h.coordinator.publishReport(alex, draft.approval.id);
      assert.equal(published.status, "succeeded");
      if (published.status !== "succeeded") return;
      assert.equal((await readFile(published.operation.destinationPath, "utf8")).includes("Input SHA-256"), true);
      assert.equal(h.store.dependenciesFor(h.coordinator.config.projectId, true).some((item) => item.taskId === report.id), false);
      const recheck = published.rechecked.find((item) => item.task.id === cleanup.id);
      assert.ok(recheck);
      assert.equal(recheck.verdicts[0]?.decision, "REVIEW");
      const freshApproval = recheck.approvals.find((item) => item.kind === "cleanup");
      assert.ok(freshApproval);
      assert.notEqual(freshApproval.id, pending.approvals[0]?.id);
      const result = await h.coordinator.approveCleanup(sam, freshApproval.id);
      assert.equal(result.status, "succeeded");
      assert.equal(await stat(path.join(h.paths.workspaceRoot, "working/report_input.csv")).then(() => true, () => false), false);
    } finally { await close(h); }
  });

  it("binds reports to input and draft hashes and rejects a changed input", async () => {
    const h = await harness();
    try {
      const alex = testActor("slack:alex", "Alex", "report-3");
      const report = await h.coordinator.startReport(alex);
      const draft = await h.coordinator.submitReportDraft(alex, report.id, "A measured update.");
      await writeFile(path.join(h.paths.workspaceRoot, "working/report_input.csv"), "period,revenue_aed,closed_deals,open_tickets\n2026-08,120000,12,20\n2026-09,150001,15,16\n");
      const result = await h.coordinator.publishReport(alex, draft.approval.id);
      assert.equal(result.status, "stale");
      assert.equal(h.store.getTask(report.id)?.state, "stale");
      assert.equal(await stat(path.join(h.paths.workspaceRoot, "reports", `${report.id}.md`)).then(() => true, () => false), false);
    } finally { await close(h); }
  });

  it("rejects a different owner from publishing or cancelling a report", async () => {
    const h = await harness();
    try {
      const alex = testActor("slack:alex", "Alex", "report-4");
      const sam = testActor("slack:sam", "Sam", "wrong-owner");
      const report = await h.coordinator.startReport(alex);
      const draft = await h.coordinator.submitReportDraft(alex, report.id, "Owner-controlled draft.");
      assert.equal((await h.coordinator.publishReport(sam, draft.approval.id)).status, "rejected");
      await assert.rejects(() => h.coordinator.cancelReport(sam, report.id), /task owner/);
    } finally { await close(h); }
  });

  it("deduplicates the same inbound report event and does not replace the owner", async () => {
    const h = await harness();
    try {
      const first = await h.coordinator.startReport(testActor("slack:alex", "Alex", "same-event"));
      const second = await h.coordinator.startReport(testActor("slack:sam", "Sam", "same-event"));
      assert.equal(first.id, second.id);
      assert.equal(second.ownerId, "slack:alex");
      assert.equal(h.store.listTasks(h.coordinator.config.projectId).length, 1);
    } finally { await close(h); }
  });

  it("defers an approved action if a dependency appears after review", async () => {
    const h = await harness();
    try {
      const sam = testActor("slack:sam", "Sam", "cleanup-race");
      const cleanup = await h.coordinator.startCleanup(sam);
      const outcome = await h.coordinator.proposeCleanup(sam, { taskId: cleanup.id, candidates: [{ fileId: fileId("scratch/debug.log"), reason: "safe" }] });
      const approval = outcome.approvals[0];
      assert.ok(approval);
      const file = await requireWorkerFile(h.paths.workspaceRoot, "scratch/debug.log");
      h.store.addDependency({ projectId: h.coordinator.config.projectId, taskId: cleanup.id, fileId: fileId("scratch/debug.log"), relativePath: "scratch/debug.log", observedHash: await hashFile(file.absolutePath), reason: "new active work" });
      const result = await h.coordinator.approveCleanup(sam, approval.id);
      assert.equal(result.status, "deferred");
      assert.equal(await stat(file.absolutePath).then(() => true, () => false), true);
    } finally { await close(h); }
  });

  it("expires old-process approvals without allowing the next process to execute them", async () => {
    const h = await harness();
    try {
      const sam = testActor("slack:sam", "Sam", "restart-cleanup");
      const cleanup = await h.coordinator.startCleanup(sam);
      const outcome = await h.coordinator.proposeCleanup(sam, { taskId: cleanup.id, candidates: [{ fileId: fileId("scratch/debug.log"), reason: "safe" }] });
      const approval = outcome.approvals[0];
      assert.ok(approval);
      h.coordinator.close();
      const config = localConfigFromPaths(h.paths);
      const store = await TruceStore.open(config.dbPath);
      const next = new TruceCoordinator(config, store);
      try { assert.equal((await next.approveCleanup(sam, approval.id)).status, "rejected"); } finally { next.close(); }
    } finally { await rm(h.root, { recursive: true, force: true }); }
  });

  it("handles exact fixture arithmetic and explicit zero-baseline percentages", () => {
    const metrics = parseMetricsCsv("period,revenue_aed,closed_deals,open_tickets\n2026-08,120000,12,20\n2026-09,150000,15,16\n");
    assert.equal(metrics.revenueAed.change, 30000);
    assert.equal(metrics.revenueAed.percent, 25);
    assert.equal(parseMetricsCsv("period,revenue_aed,closed_deals,open_tickets\n2026-08,0,0,20\n2026-09,100,1,16\n").revenueAed.percent, null);
    assert.throws(() => parseMetricsCsv("period,revenue_aed,closed_deals,open_tickets\n2026-09,1,1,1\n2026-08,1,1,1\n"));
  });
});
