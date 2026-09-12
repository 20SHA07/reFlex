import test from "node:test";
import assert from "node:assert/strict";
import {dispatchPreview, initialState, publicState} from "../preview.mjs";
import {parseContext, sameContext, panelSender} from "../context.mjs";

const context = {workspace_id: "T123", channel_id: "C123", url: "https://app.slack.com/client/T123/C123"};
const request = (operation, extra = {}) => ({operation, context, request_id: crypto.randomUUID(), ...extra});
const approve = entry => ({target: {id: entry.id, revision: entry.revision}});

test("shared-log deletion is deferred until report approval and then requires a fresh review", () => {
  let state = dispatchPreview(null, request("start_report"));
  state = dispatchPreview(state, request("start_cleanup"));
  const [log] = state.cleanup.items;
  assert.deepEqual(state.cleanup.items.map(i => i.verdict), ["DEFER"]);
  assert.throws(() => dispatchPreview(state, request("approve_cleanup", approve(log))), {code: "not_reviewable"});
  state = dispatchPreview(state, request("approve_report", approve(state.report)));
  assert.equal(state.report.status, "completed");
  const released = state.cleanup.items[0];
  assert.equal(released.verdict, "REVIEW");
  assert.notEqual(released.id, log.id);
  assert.throws(() => dispatchPreview(state, request("approve_cleanup", approve(log))), {code: "stale_review"});
  const logApproval = request("approve_cleanup", approve(released));
  state = dispatchPreview(state, logApproval);
  assert.equal(state.cleanup.items[0].executed, true);
  const afterQuarantine = structuredClone(state);
  assert.deepEqual(dispatchPreview(state, logApproval), afterQuarantine);
  assert.deepEqual(dispatchPreview(state, request("approve_cleanup", approve(released))), afterQuarantine);
  assert.throws(() => dispatchPreview(state, request("start_report")), {code: "missing_input"});
  assert.equal(Object.keys(publicState(state)).some(key => key.startsWith("_")), false);
});

test("a newly started report invalidates a previously safe input cleanup", () => {
  let state = dispatchPreview(null, request("start_cleanup"));
  const old = state.cleanup.items[0];
  assert.equal(old.verdict, "REVIEW");
  state = dispatchPreview(state, request("start_report"));
  assert.equal(state.cleanup.items[0].verdict, "DEFER");
  assert.throws(() => dispatchPreview(state, request("approve_cleanup", approve(old))), {code: "stale_review"});
});

test("cross-channel approvals and stale revisions are rejected", () => {
  const state = dispatchPreview(null, request("start_report"));
  const other = {workspace_id: "T123", channel_id: "C999", url: "https://app.slack.com/client/T123/C999"};
  assert.throws(() => dispatchPreview(state, request("approve_report", {...approve(state.report), context: other})), {code: "context_changed"});
  assert.throws(() => dispatchPreview(state, request("approve_report", {target: {id: state.report.id, revision: "changed"}})), {code: "stale_review"});
  assert.equal(state.report.status, "awaiting_approval");
});

test("request ID retries cannot be repurposed", () => {
  const original = request("start_report");
  const state = dispatchPreview(null, original);
  assert.deepEqual(dispatchPreview(state, original), state);
  assert.throws(() => dispatchPreview(state, {...original, operation: "start_cleanup"}), {code: "request_conflict"});
});

test("only canonical Slack contexts and the extension panel can cross the worker boundary", () => {
  assert.ok(parseContext(context));
  assert.equal(parseContext({...context, url: "https://app.slack.com.evil.example/client/T123/C123"}), null);
  assert.equal(parseContext({...context, channel_id: "C999"}), null);
  assert.equal(sameContext(context, {...context, channel_id: "C999"}), false);
  const runtime = {id: "extension-id", getURL: path => `chrome-extension://extension-id/${path}`};
  assert.equal(panelSender({id: runtime.id, url: runtime.getURL("panel.html")}, runtime), true);
  assert.equal(panelSender({id: runtime.id, url: context.url}, runtime), false);
  assert.equal(panelSender({id: "other", url: runtime.getURL("panel.html")}, runtime), false);
});
