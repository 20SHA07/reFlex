import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

test("content script sends selected text only when explicitly requested", async () => {
  let handler, selectionReads = 0;
  const sandbox = {
    chrome: {runtime: {id: "test", onMessage: {addListener(fn) {handler = fn;}}}},
    location: new URL("https://app.slack.com/client/T123/C123"),
    window: {getSelection() { selectionReads++; return "private sample message"; }},
  };
  vm.runInNewContext(await readFile(new URL("../content.js", import.meta.url), "utf8"), sandbox);
  const call = (message, sender = {id: "test"}) => {let value; handler(message, sender, response => {value = response;}); return value;};
  const result = call({type: "REFLEX_PAGE_CONTEXT"});
  assert.equal(result.ok, true);
  assert.equal(result.context.selected_text, undefined);
  assert.equal(selectionReads, 0);
  assert.equal(call({type: "REFLEX_PAGE_CONTEXT", selection: true}).context.selected_text, "private sample message");
  assert.equal(selectionReads, 1);
  assert.equal(call({type: "REFLEX_PAGE_CONTEXT", selection: true}, {id: "foreign"}), undefined);
});

test("worker requires trusted panel, isolates channel state, and rechecks active channel before approvals", async () => {
  let handler, activeChannel = "C123", permission = false;
  let connectionCalls = 0, accessLevel;
  const data = {};
  const context = () => ({workspace_id: "T123", channel_id: activeChannel, url: `https://app.slack.com/client/T123/${activeChannel}`});
  const runtime = {id: "test", getURL: path => `chrome-extension://test/${path}`, onMessage: {addListener(fn) {handler = fn;}}};
  globalThis.chrome = {
    runtime, sidePanel: {setPanelBehavior: async () => {}},
    tabs: {query: async () => [{id: 1}], sendMessage: async () => ({ok: true, context: context()})},
    permissions: {contains: async () => permission},
    storage: {session: {
      setAccessLevel: async value => {accessLevel = value.accessLevel;},
      get: async key => ({[key]: structuredClone(data[key])}),
      set: async value => Object.assign(data, structuredClone(value)),
      remove: async key => {delete data[key];},
    }},
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    connectionCalls++;
    assert.equal(url, "http://127.0.0.1:8765/v1/session");
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    return {ok: true, json: async () => ({ok: true, mode: "local-demo", principal: {id: "local-owner", display_name: "Local demo owner"}})};
  };
  try {
    await import("../background.js");
    const sender = {id: "test", url: runtime.getURL("panel.html")};
    const call = (message, source = sender) => new Promise(resolve => handler(message, source, resolve));
    const dispatch = (operation, extra = {}) => call({type: "DISPATCH", operation, context: context(), request_id: crypto.randomUUID(), ...extra});
    assert.equal((await call({type: "GET_CONFIG"}, {id: "test", url: context().url})).error.code, "untrusted_sender");
    assert.equal(accessLevel, "TRUSTED_CONTEXTS");
    const report = (await dispatch("start_report")).state.report;
    const originalContext = context();
    activeChannel = "C456";
    const stale = await dispatch("approve_report", {context: originalContext, target: {id: report.id, revision: report.revision}});
    assert.equal(stale.error.code, "context_changed");
    assert.equal((await dispatch("status")).state.report, null);
    activeChannel = "C123";
    assert.equal((await dispatch("status")).state.report.id, report.id);
    const connectedWithoutPermission = await call({type: "CONNECT", token: "a".repeat(40)});
    assert.equal(connectedWithoutPermission.error.code, "permission_required");
    assert.equal(connectionCalls, 0);
    permission = true;
    const connected = await call({type: "CONNECT", token: "a".repeat(40)});
    assert.equal(connected.mode, "local-demo");
    assert.equal(JSON.stringify(connected).includes("a".repeat(40)), false);
    assert.equal(data.connection.token, "a".repeat(40));
    assert.equal(JSON.stringify(await call({type: "GET_CONFIG"})).includes("a".repeat(40)), false);
    assert.equal((await call({type: "DISCONNECT"})).mode, "preview");
    assert.equal(data.connection, undefined);
  } finally { globalThis.fetch = originalFetch; delete globalThis.chrome; }
});
