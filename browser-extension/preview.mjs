// Deterministic rehearsal only. No filesystem, network, model, or Slack messages.
import {contextKey} from "./context.mjs";

export class PreviewError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
const id = () => crypto.randomUUID();
const fail = (code, message) => { throw new PreviewError(code, message); };
const event = (state, text, kind = "info") => {
  state.events.push({id: id(), kind, text, at: new Date().toISOString()});
  state.events = state.events.slice(-20);
  state.message = text;
};
const item = (path, verdict, reason) => ({id: id(), revision: id(), path, verdict, reason, executed: false});

export function initialState(context) {
  contextKey(context);
  return {
    mode: "preview", principal: {id: "preview-owner", display_name: "Preview owner"},
    context: {workspace_id: context.workspace_id, channel_id: context.channel_id},
    report: null, cleanup: null, events: [], message: "Ready to rehearse. No files will change.",
    _inputAvailable: true, _debugAvailable: true, _requests: {}, _approved: {},
  };
}

export function publicState(state) {
  return Object.fromEntries(Object.entries(state).filter(([key]) => !key.startsWith("_")));
}

function cleanupItems(state) {
  const active = state.report?.status === "awaiting_approval";
  return [
    item("data/source_metrics.csv", "BLOCK", "Protected source data. This file cannot be quarantined."),
    item("working/report_input.csv", !state._inputAvailable ? "BLOCK" : active ? "DEFER" : "REVIEW",
      !state._inputAvailable ? "Already quarantined in this preview." : active ? "Report Agent still needs this input. Waiting for report approval." : "No active dependency. Review before quarantining."),
    item("scratch/debug.log", state._debugAvailable ? "REVIEW" : "BLOCK",
      state._debugAvailable ? "Temporary log with no active dependency. Review before quarantining." : "Already quarantined in this preview."),
  ];
}

export function dispatchPreview(current, request) {
  const state = structuredClone(current || initialState(request.context));
  if (`${state.context.workspace_id}:${state.context.channel_id}` !== contextKey(request.context)) {
    fail("context_changed", "Switch back to the Slack channel for this review.");
  }
  const {operation, target} = request;
  if (operation === "status") return state;
  if (!["start_report", "start_cleanup", "approve_report", "approve_cleanup"].includes(operation)) {
    fail("invalid_operation", "Unknown referee operation.");
  }
  if (typeof request.request_id !== "string" || !/^[a-zA-Z0-9-]{8,100}$/.test(request.request_id)) {
    fail("invalid_request", "A valid request ID is required.");
  }
  const signature = JSON.stringify([operation, request.input?.text || "", target || null]);
  const prior = state._requests[request.request_id];
  if (prior) {
    if (prior !== signature) fail("request_conflict", "This request ID was already used for a different action.");
    return state;
  }
  if (operation === "start_report") {
    if (state.report?.status === "awaiting_approval") fail("active_report", "Review the current report before starting another.");
    if (!state._inputAvailable) fail("missing_input", "The sample input was quarantined. Disconnect or restart the preview to reset the fixture.");
    const reportId = id();
    state.report = {
      id: reportId, revision: id(), status: "awaiting_approval",
      input_path: "working/report_input.csv", output_path: `reports/client_update-${reportId}.md`,
      draft: "# Client update\n\nSample report for the referee demo.\n\n- Completed tasks: 18\n- Open tasks: 4\n- Next step: review the remaining tasks with the client.\n\nGenerated from sample data. No model was called.",
    };
    if (state.cleanup) {
      state.cleanup.items = state.cleanup.items.map(entry => entry.path === "working/report_input.csv" && !entry.executed
        ? item(entry.path, "DEFER", "Report Agent now needs this input. The previous review is no longer valid.") : entry);
    }
    event(state, "Report Agent reserved its input and prepared a sample draft. Owner approval is required.");
  } else if (operation === "start_cleanup") {
    state.cleanup = {id: id(), items: cleanupItems(state)};
    event(state, "Cleanup reviewed three sample files. Each allowed operation needs its own approval.");
  } else {
    if (!target || typeof target.id !== "string" || typeof target.revision !== "string") fail("invalid_request", "Select a stored proposal to approve.");
    const approvalKey = `${operation}:${target.id}:${target.revision}`;
    if (state._approved[approvalKey]) return state;
    if (operation === "approve_report") {
      const report = state.report;
      if (!report || target.id !== report.id || target.revision !== report.revision || report.status !== "awaiting_approval") {
        fail("stale_review", "That report is no longer current. Refresh before approving.");
      }
      report.status = "completed";
      event(state, "Preview: report published and its input dependency released.", "success");
      if (state.cleanup) {
        state.cleanup.items = state.cleanup.items.map(entry => entry.verdict === "DEFER"
          ? item(entry.path, "REVIEW", "Report completed. This is a fresh cleanup review and needs a new approval.") : entry);
      }
    } else {
      const entry = state.cleanup?.items.find(value => value.id === target.id && value.revision === target.revision);
      if (!entry) fail("stale_review", "That cleanup review was replaced. Refresh before approving.");
      if (entry.verdict !== "REVIEW") fail("not_reviewable", "Blocked or deferred actions cannot be approved.");
      if (entry.path === "working/report_input.csv" && state.report?.status === "awaiting_approval") {
        fail("dependency_active", "Report Agent still needs this file. Request a fresh review.");
      }
      entry.executed = true;
      entry.verdict = "ALLOW";
      entry.reason = "Preview: moved to quarantine. No real file changed.";
      if (entry.path === "working/report_input.csv") state._inputAvailable = false;
      if (entry.path === "scratch/debug.log") state._debugAvailable = false;
      event(state, `Preview: ${entry.path} quarantined after approval.`, "success");
    }
    state._approved[approvalKey] = true;
  }
  state._requests[request.request_id] = signature;
  // A rehearsal session is bounded; old request IDs have no destructive authority.
  state._requests = Object.fromEntries(Object.entries(state._requests).slice(-100));
  return state;
}
