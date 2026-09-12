import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { webcrypto } from "node:crypto";

// These tests exercise the panel's actual event handlers without a browser.
// Chromium integration and rendering are covered separately by the smoke test.
const html = readFileSync(new URL("../panel.html", import.meta.url), "utf8");
const source = readFileSync(new URL("../panel.js", import.meta.url), "utf8");
const htmlIds = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
const slackContext = (channel = "C123") => ({
  workspace_id: "T123", channel_id: channel,
  url: `https://app.slack.com/client/T123/${channel}`,
});
const report = () => ({
  id: "report-1", revision: "revision-1", status: "awaiting_approval",
  input_path: "working/report_input.csv", output_path: "reports/result.md",
  draft: "<img src=x onerror=alert(1)> is literal draft text.",
});
const cleanup = () => ({
  id: "cleanup-1",
  items: [
    { id: "protected", revision: "revision-b", path: "data/source_metrics.csv", verdict: "BLOCK", reason: "Protected source data." },
    { id: "held", revision: "revision-h", path: "working/report_input.csv", verdict: "DEFER", reason: "An active report needs this file." },
    { id: "review", revision: "revision-r", path: "scratch/debug.log", verdict: "REVIEW", reason: "Ready for your review." },
  ],
});
const settled = () => new Promise((resolve) => setImmediate(resolve));

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.listeners = {};
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.classList = { toggle() {} };
  }
  set innerHTML(_value) { throw new Error("The panel must render untrusted content as text."); }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  fire(type, event = { preventDefault() {} }) { return this.listeners[type]?.(event); }
}

async function harness({ context = slackContext() } = {}) {
  const elements = Object.fromEntries(htmlIds.map((id) => [id, new Element("div")]));
  const created = [];
  const timers = new Map();
  let timerId = 0;
  const server = {
    context,
    config: { mode: "preview", principal: { id: "preview-owner", display_name: "Preview owner" } },
    state: { mode: "preview", context, report: null, cleanup: null, events: [], message: "Ready." },
    messages: [], permissions: [], dispatchOverride: null,
  };
  server.state.principal = server.config.principal;
  const chrome = {
    runtime: {
      async sendMessage(message) {
        server.messages.push(structuredClone(message));
        if (message.type === "GET_CONFIG") return { ok: true, ...server.config };
        if (message.type === "GET_CONTEXT") {
          return server.context
            ? { ok: true, context: { ...server.context, ...(message.selection ? { selected_text: "Selected Slack request" } : {}) } }
            : { ok: false, error: { code: "no_context", message: "Open a Slack channel." } };
        }
        if (message.type === "CONNECT" || message.type === "DISCONNECT") {
          const local = message.type === "CONNECT";
          server.config = {
            mode: local ? "local-demo" : "preview",
            principal: { id: local ? "local-owner" : "preview-owner", display_name: local ? "Local demo owner" : "Preview owner" },
          };
          server.state.mode = server.config.mode;
          server.state.principal = server.config.principal;
          return { ok: true, ...server.config };
        }
        if (server.dispatchOverride) {
          const overridden = server.dispatchOverride(message);
          if (overridden !== undefined) return overridden;
        }
        if (message.operation === "start_report") server.state.report = report();
        if (message.operation === "start_cleanup") server.state.cleanup = cleanup();
        if (message.operation === "approve_report") server.state.report.status = "completed";
        if (message.operation === "approve_cleanup") server.state.cleanup.items.find((item) => item.id === message.target.id).executed = true;
        return { ok: true, state: structuredClone(server.state) };
      },
    },
    permissions: { async request(permission) { server.permissions.push(permission); return true; } },
    tabs: {
      onActivated: { addListener(fn) { this.callback = fn; } },
      onUpdated: { addListener(fn) { this.callback = fn; } },
    },
  };
  const document = {
    getElementById: (id) => elements[id],
    createElement(tag) { const element = new Element(tag); created.push(element); return element; },
    querySelectorAll: () => created.filter((element) => element.dataset.cleanupApproval),
    addEventListener() {}, hidden: false,
  };
  vm.runInNewContext(source, {
    document, chrome, crypto: webcrypto, Date,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  await settled();
  return {
    elements, chrome, server,
    async flushTimers() { for (const [id, fn] of timers) { timers.delete(id); await fn(); } await settled(); },
  };
}

test("panel DOM references resolve to unique HTML ids", () => {
  assert.equal(new Set(htmlIds).size, htmlIds.length);
  for (const [, id] of source.matchAll(/\$\("([^"]+)"\)/g)) assert(htmlIds.includes(id), `Missing HTML id: ${id}`);
});

test("without Slack context the panel disables task and approval controls", async () => {
  const { elements, server } = await harness({ context: null });
  for (const id of ["start-report", "start-cleanup", "use-selection", "approve-report"]) assert.equal(elements[id].disabled, true);
  await elements["start-report"].fire("click");
  assert.equal(server.messages.filter((message) => message.type === "DISPATCH").length, 0);
  assert.equal(elements["mode-badge"].textContent, "Preview");
  assert.match(elements["mode-description"].textContent, /No files are changed/);
});

test("report approval targets the reviewed revision and untrusted draft stays text", async () => {
  const { elements, server } = await harness();
  await elements["start-report"].fire("click");
  assert.equal(elements["report-section"].hidden, false);
  assert.equal(elements["report-draft"].textContent, report().draft);
  await elements["approve-report"].fire("click");
  assert.deepEqual(server.messages.find((message) => message.operation === "approve_report").target, { id: "report-1", revision: "revision-1" });
  assert.equal(elements["report-status"].textContent, "Published");
  assert.equal(elements["approve-report"].hidden, true);
});

test("only REVIEW files expose approval and execution removes that control", async () => {
  const { elements, server } = await harness();
  await elements["start-cleanup"].fire("click");
  const cards = elements["cleanup-items"].children;
  assert.equal(cards.length, 3);
  assert.equal(cards[0].children.length, 2);
  assert.equal(cards[1].children.length, 2);
  await cards[2].children[2].fire("click");
  assert.deepEqual(server.messages.find((message) => message.operation === "approve_cleanup").target, { id: "review", revision: "revision-r" });
  assert.equal(elements["cleanup-items"].children[2].children.length, 2);
  assert.equal(elements["cleanup-items"].children[2].children[0].children[1].textContent, "Quarantined");
});

test("stale approval preserves the error, refreshes status and never auto-approves", async () => {
  const { elements, server } = await harness();
  await elements["start-cleanup"].fire("click");
  server.dispatchOverride = (message) => message.operation === "approve_cleanup"
    ? { ok: false, error: { code: "stale_review", message: "Request a fresh review." } }
    : undefined;
  await elements["cleanup-items"].children[2].children[2].fire("click");
  assert.equal(elements["error-box"].hidden, false);
  assert.equal(elements["error-text"].textContent, "Request a fresh review.");
  assert.equal(server.messages.filter((message) => message.operation === "approve_cleanup").length, 1);
  assert.equal(server.messages.at(-1).operation, "status");
});

test("selection imports only on request and changes of channel clear composed text", async () => {
  const { elements, server } = await harness();
  assert.equal(server.messages.some((message) => message.selection === true), false);
  await elements["use-selection"].fire("click");
  assert.equal(elements["request-text"].value, "Selected Slack request");
  server.context = slackContext("C456");
  await elements["refresh-context"].fire("click");
  assert.equal(elements["request-text"].value, "");
  assert.equal(elements["channel-name"].textContent, "# C456");
});

test("an in-flight result from the old channel cannot reveal old approval controls", async () => {
  const { elements, server, chrome, flushTimers } = await harness();
  let finishOldRequest;
  server.dispatchOverride = (message) => message.operation === "start_report"
    ? new Promise((resolve) => { finishOldRequest = resolve; })
    : undefined;
  const request = elements["start-report"].fire("click");
  server.context = slackContext("C456");
  chrome.tabs.onActivated.callback();
  assert.equal(elements["approve-report"].disabled, true);
  finishOldRequest({ ok: true, state: { ...server.state, report: report() } });
  await request;
  await flushTimers();
  assert.equal(elements["report-section"].hidden, true);
  assert.equal(elements["channel-name"].textContent, "# C456");
  assert.equal(server.messages.filter((message) => message.operation === "approve_report").length, 0);
});

test("pairing requests localhost permission, clears token and labels the actual mode", async () => {
  const { elements, server } = await harness();
  elements["pairing-token"].value = "a-local-demo-pairing-token";
  await elements["connect-form"].fire("submit");
  assert.equal(elements["pairing-token"].value, "");
  assert.equal(server.permissions.length, 1);
  assert.equal(server.permissions[0].origins[0], "http://127.0.0.1/*");
  assert.equal(elements["mode-badge"].textContent, "Local demo");
  assert.match(elements["mode-description"].textContent, /disposable demo folder/);
  assert.match(elements["mode-description"].textContent, /not AI-generated/);
  assert.equal(elements["principal-name"].textContent, "Local demo owner");
  await elements["disconnect-local"].fire("click");
  assert.equal(elements["mode-badge"].textContent, "Preview");
  assert.equal(elements["principal-name"].textContent, "Preview owner");
});
