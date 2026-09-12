import type { InteractionContext, Thread } from "@copilotkit/channels";
import { Actions, Button, Context, Divider, Field, Fields, Header, Markdown, Message, Section, Table, Row, Cell } from "@copilotkit/channels";
import { formatPercent } from "./metrics";
import type { ApprovalRecord, CleanupOutcome, PublicationOutcome, RefereeVerdict, ReportMetrics, StatusSnapshot, TaskRecord } from "./types";

export function welcomeMessage(platform: string) {
  return <Message accent="#246BFD"><Header>Truce</Header><Section><Markdown>{`Coordinate report work and cleanup in this ${platform} thread.`}</Markdown></Section><Fields><Field label="Project">One disposable project folder</Field><Field label="Supported">report, cleanup, status, demo unsafe-cleanup</Field><Field label="Boundary">Protected source and reports stay intact; cleanup moves only to quarantine after approval.</Field></Fields><Context>Use @Truce report: ..., @Truce cleanup: ..., or @Truce status.</Context></Message>;
}

export function reportProgress(task: TaskRecord) {
  return <Message accent="#246BFD"><Header>Report Agent working</Header><Section><Markdown>{`Task **${task.id}** is preparing a draft for **${task.ownerLabel}**.`}</Markdown></Section><Context>Input: {task.inputPath ?? "working/report_input.csv"} · dependency registered before worker reads</Context></Message>;
}

export function reportReviewCard(task: TaskRecord, approval: ApprovalRecord, metrics: ReportMetrics, handlers: { publish: (approvalId: string, ctx: InteractionContext<string>) => Promise<void>; regenerate: (taskId: string, ctx: InteractionContext<string>) => Promise<void>; cancel: (taskId: string, ctx: InteractionContext<string>) => Promise<void> }) {
  return <Message accent="#246BFD"><Header>Report Agent draft ready</Header><Section><Markdown>{task.narrative ?? "The report draft is ready for review."}</Markdown></Section><Table columns={[{ header: "Metric" }, { header: "Change" }, { header: "%" }]}><Row><Cell>Revenue (AED)</Cell><Cell>{metrics.revenueAed.change.toLocaleString()}</Cell><Cell>{formatPercent(metrics.revenueAed.percent)}</Cell></Row><Row><Cell>Closed deals</Cell><Cell>{metrics.closedDeals.change}</Cell><Cell>{formatPercent(metrics.closedDeals.percent)}</Cell></Row><Row><Cell>Open tickets</Cell><Cell>{metrics.openTickets.change}</Cell><Cell>{formatPercent(metrics.openTickets.percent)}</Cell></Row></Table><Fields><Field label="Owner">{task.ownerLabel}</Field><Field label="Revision">{task.revision}</Field><Field label="Input">{task.inputPath ?? "working/report_input.csv"}</Field><Field label="Review">Restart may require @Truce status for fresh controls.</Field></Fields><Actions><Button value={approval.id} style="primary" onClick={async (ctx) => { await handlers.publish(approval.id, ctx); }}>Publish report</Button><Button value={task.id} onClick={async (ctx) => { await handlers.regenerate(task.id, ctx); }}>Regenerate draft</Button><Button value={task.id} style="danger" onClick={async (ctx) => { await handlers.cancel(task.id, ctx); }}>Cancel report</Button></Actions></Message>;
}

function verdictLine(verdict: RefereeVerdict): string {
  const label = verdict.decision === "BLOCK" ? "Blocked" : verdict.decision === "DEFER" ? "Waiting" : verdict.decision === "REVIEW" ? "Ready for approval" : verdict.decision;
  return `*${label}:* ${verdict.relativePath} — ${verdict.reason}`;
}

export function cleanupDecisionCard(outcome: CleanupOutcome, handlers: { approve: (approvalId: string, ctx: InteractionContext<string>) => Promise<void>; keep: (taskId: string, ctx: InteractionContext<string>) => Promise<void> }) {
  const approval = outcome.approvals[0];
  return <Message accent="#C47F17"><Header>{outcome.injected ? "Injected cleanup proposal for testing" : "Truce referee decision"}</Header><Section><Markdown>{outcome.verdicts.map(verdictLine).join("\n\n") || "No new cleanup actions were proposed."}</Markdown></Section><Context>{outcome.verdicts.some((item) => item.decision === "DEFER") ? "Truce will reassess deferred files after the dependent report is published or cancelled." : "Approval applies only to the exact current eligible file revision shown here."}</Context><Actions>{approval && <Button value={approval.id} style="primary" onClick={async (ctx) => { await handlers.approve(approval.id, ctx); }}>Clean eligible file</Button>}<Button value={outcome.task.id} style="danger" onClick={async (ctx) => { await handlers.keep(outcome.task.id, ctx); }}>Keep all remaining files</Button></Actions></Message>;
}

export function cleanupReceipt(outcome: { action: { relativePath: string }; operation: { receiptId?: string; expectedHash: string } }) {
  return <Message accent="#2E8B57"><Header>Cleanup verified</Header><Section><Markdown>{`Moved **${outcome.action.relativePath}** into quarantine.`}</Markdown></Section><Fields><Field label="Receipt">{outcome.operation.receiptId ?? "verified operation"}</Field><Field label="Original hash">{outcome.operation.expectedHash}</Field><Field label="Recovery">The original path is now absent; the quarantine copy was hash-verified.</Field></Fields></Message>;
}

export function publicationReceipt(outcome: PublicationOutcome & { status: "succeeded" }) {
  return <Message accent="#2E8B57"><Header>Report published and verified</Header><Section><Markdown>{`The report for **${outcome.task.ownerLabel}** is saved under the server-generated reports path.`}</Markdown></Section><Fields><Field label="Task">{outcome.task.id}</Field><Field label="Operation">{outcome.operation.receiptId ?? outcome.operation.id}</Field><Field label="Dependency">Released only after output verification</Field></Fields></Message>;
}

export function deferredRecheckCard(outcome: CleanupOutcome) {
  return <Message accent="#C47F17"><Header>Deferred cleanup rechecked</Header><Section><Markdown>{outcome.verdicts.map(verdictLine).join("\n\n")}</Markdown></Section><Context>This is a new review revision. A previous approval cannot authorize it.</Context></Message>;
}

export function statusCard(snapshot: StatusSnapshot) {
  return <Message accent={snapshot.consistent ? "#2E8B57" : "#C4145F"}><Header>Truce status</Header><Section><Markdown>{snapshot.consistent ? "The fixture, database, dependencies, and known file outcomes are consistent." : `Attention: ${snapshot.problems.join("; ")}`}</Markdown></Section><Fields><Field label="Project">{snapshot.projectId}</Field><Field label="Run">{snapshot.runRoot}</Field><Field label="Tasks">{snapshot.tasks.length}</Field><Field label="Active dependencies">{snapshot.activeDependencies.length}</Field><Field label="Pending actions">{snapshot.actions.filter((action) => action.state === "pending_review").length}</Field></Fields><Divider /><Markdown>{snapshot.files.map((file) => `*${file.exists ? "Present" : "Missing"}:* ${file.relativePath}${file.hash ? ` · ${file.hash.slice(0, 12)}` : ""}`).join("\n")}</Markdown></Message>;
}

export function errorCard(message: string) {
  return <Message accent="#C4145F"><Header>Truce could not complete that request</Header><Section><Markdown>{message}</Markdown></Section><Context>No file was changed unless a verified receipt was shown. Use @Truce status or correct the setup.</Context></Message>;
}

export type CardThread = Thread;
