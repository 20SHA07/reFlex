const $ = (id) => document.getElementById(id);
let config = { mode: "preview", principal: { display_name: "Preview owner" } };
let context = null;
let state = null;
let busy = false;
let refreshing = false;
let epoch = 0;
let refreshTimer;
let inputContextKey = "";

async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) {
    const error = new Error(response?.error?.message || "The extension could not complete that request. Please try again.");
    error.code = response?.error?.code || "request_failed";
    throw error;
  }
  return response;
}

function showError(error) {
  $("error-text").textContent = error.message || "Something went wrong. Please try again.";
  $("error-box").hidden = false;
}

function clearError() { $("error-box").hidden = true; }
function status(text) { $("activity-status").textContent = text; }
function contextKey(value) { return value ? `${value.workspace_id}/${value.channel_id}` : ""; }

function setControls() {
  const unavailable = busy || refreshing || !context;
  for (const id of ["start-report", "start-cleanup", "use-selection", "approve-report"]) $(id).disabled = unavailable;
  document.querySelectorAll("[data-cleanup-approval]").forEach((button) => { button.disabled = unavailable; });
  $("request-text").disabled = busy || refreshing || !context;
  $("connect-local").disabled = busy;
  $("disconnect-local").disabled = busy;
  $("pairing-token").disabled = busy;
  $("refresh-context").disabled = busy || refreshing;
}

function renderConfig() {
  const local = config.mode === "local-demo";
  $("mode-badge").textContent = local ? "Local demo" : "Preview";
  $("mode-badge").classList.toggle("local", local);
  $("mode-notice").classList.toggle("local", local);
  $("mode-description").textContent = local
    ? "Connected to real file operations in a disposable demo folder. The report is a fixed sample, not AI-generated."
    : "Preview uses sample data. No files are changed.";
  $("principal-name").textContent = config.principal?.display_name || (local ? "Local demo owner" : "Preview owner");
  $("connection-summary").textContent = local ? "Local demo connected" : "Connect local demo";
  $("connection-light").classList.toggle("connected", local);
  $("connect-form").hidden = local;
  $("disconnect-local").hidden = !local;
}

function renderContext() {
  $("channel-name").textContent = context ? `# ${context.channel_id}` : refreshing ? "Finding your channel…" : "Open a Slack channel";
  $("workspace-name").textContent = context ? `Workspace ${context.workspace_id}` : "Use app.slack.com in this browser window, then refresh.";
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderResults() {
  const report = state?.report;
  const cleanup = state?.cleanup;
  const events = Array.isArray(state?.events) ? state.events : [];
  $("empty-state").hidden = Boolean(report || cleanup);
  $("report-section").hidden = !report;
  if (report) {
    const completed = report.status === "completed";
    $("report-status").textContent = completed ? "Published" : "Needs approval";
    $("report-status").className = `status-badge${completed ? " completed" : ""}`;
    $("report-path").textContent = report.output_path;
    $("report-dependency").textContent = `${completed ? "Released input" : "Holding input"}: ${report.input_path}`;
    $("report-draft").textContent = report.draft;
    $("approve-report").hidden = completed || report.status !== "awaiting_approval";
    $("report-footnote").textContent = completed
      ? "Report published. Any held cleanup requires a fresh review before approval."
      : "Approval publishes this draft and releases its input file.";
  }
  $("cleanup-section").hidden = !cleanup;
  $("cleanup-items").replaceChildren();
  if (cleanup) {
    const items = Array.isArray(cleanup.items) ? cleanup.items : [];
    $("cleanup-count").textContent = `${items.length} file${items.length === 1 ? "" : "s"}`;
    for (const item of items) {
      const knownVerdict = ["BLOCK", "DEFER", "REVIEW", "ALLOW"].includes(item.verdict) ? item.verdict : "UNKNOWN";
      const className = item.executed ? "completed" : knownVerdict.toLowerCase();
      const card = node("article", `cleanup-card ${className}`);
      const top = node("div", "section-line");
      top.append(node("p", "file-path", item.path), node("span", `status-badge ${className}`, item.executed ? "Quarantined" : knownVerdict));
      card.append(top, node("p", "cleanup-reason", item.reason));
      if (knownVerdict === "REVIEW" && !item.executed) {
        const button = node("button", "button button-secondary full-width", "Approve quarantine");
        button.type = "button";
        button.dataset.cleanupApproval = item.id;
        button.setAttribute("aria-label", `Approve quarantine of ${item.path}`);
        button.addEventListener("click", () => dispatch("approve_cleanup", { target: { id: item.id, revision: item.revision } }));
        card.append(button);
      }
      $("cleanup-items").append(card);
    }
  }
  $("events-section").hidden = events.length === 0;
  $("event-list").replaceChildren();
  for (const event of events.slice(-20).reverse()) {
    const row = node("li", "event-item");
    const marker = node("span", "event-marker");
    marker.setAttribute("aria-hidden", "true");
    const body = node("div");
    body.append(node("p", "event-text", event.text));
    const date = new Date(event.at);
    if (!Number.isNaN(date.getTime())) {
      const time = node("time", "event-time", date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      time.dateTime = date.toISOString();
      body.append(time);
    }
    row.append(marker, body);
    $("event-list").append(row);
  }
  setControls();
}

function render() { renderConfig(); renderContext(); renderResults(); }

async function refreshContext({ preserveError = false } = {}) {
  const currentEpoch = ++epoch;
  refreshing = true;
  context = null;
  state = null;
  if (!preserveError) clearError();
  render();
  status("Checking the active Slack channel…");
  try {
    const response = await send({ type: "GET_CONTEXT", selection: false });
    if (epoch !== currentEpoch) return;
    if (!response.context?.workspace_id || !response.context?.channel_id) throw new Error("Open a Slack channel in this browser window to begin.");
    context = response.context;
    if (inputContextKey && inputContextKey !== contextKey(context)) $("request-text").value = "";
    inputContextKey = contextKey(context);
    renderContext();
    const result = await send({ type: "DISPATCH", operation: "status", context, request_id: crypto.randomUUID() });
    if (epoch !== currentEpoch) return;
    state = result.state;
    if (state?.mode) config = { mode: state.mode, principal: state.principal };
    status(state?.message || "Ready to review.");
  } catch (error) {
    if (epoch !== currentEpoch) return;
    if (context) showError(error);
    status(context ? "Could not refresh this channel. Try again." : error.message);
  } finally {
    if (epoch === currentEpoch) { refreshing = false; render(); }
  }
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  epoch += 1;
  context = null;
  state = null;
  refreshing = true;
  render();
  refreshTimer = setTimeout(() => refreshContext(), 160);
}

async function dispatch(operation, extra = {}) {
  if (busy || refreshing || !context) return;
  const currentEpoch = epoch;
  const originalContext = contextKey(context);
  busy = true;
  clearError();
  setControls();
  status(operation.startsWith("approve") ? "Checking this approval with the referee…" : "The referee is reviewing the task…");
  let needsRefresh = false;
  try {
    const response = await send({ type: "DISPATCH", operation, context, request_id: crypto.randomUUID(), ...extra });
    if (currentEpoch !== epoch || contextKey(context) !== originalContext) return;
    state = response.state;
    if (state?.mode) config = { mode: state.mode, principal: state.principal };
    status(state?.message || "Review updated.");
  } catch (error) {
    if (currentEpoch !== epoch) return;
    showError(error);
    status("Could not confirm the outcome. Refreshing the current state before another action.");
    needsRefresh = true;
  } finally {
    busy = false;
    render();
    if (needsRefresh && currentEpoch === epoch) await refreshContext({ preserveError: true });
  }
}

$("start-report").addEventListener("click", () => dispatch("start_report", { input: { text: $("request-text").value.trim() } }));
$("start-cleanup").addEventListener("click", () => dispatch("start_cleanup", { input: { text: $("request-text").value.trim() } }));
$("approve-report").addEventListener("click", () => {
  const report = state?.report;
  if (report?.status === "awaiting_approval") return dispatch("approve_report", { target: { id: report.id, revision: report.revision } });
});
$("dismiss-error").addEventListener("click", clearError);
$("refresh-context").addEventListener("click", () => refreshContext());

$("use-selection").addEventListener("click", async () => {
  if (busy || refreshing || !context) return;
  const currentEpoch = epoch;
  const originalContext = contextKey(context);
  busy = true;
  clearError();
  setControls();
  try {
    const response = await send({ type: "GET_CONTEXT", selection: true });
    if (epoch !== currentEpoch) return;
    if (contextKey(response.context) !== originalContext) { scheduleRefresh(); return; }
    const selection = response.context?.selected_text?.trim();
    if (selection) {
      $("request-text").value = selection.slice(0, 2000);
      status("Selected text added. Review it before starting a task.");
    } else status("Select text in the Slack page, then choose Use selection.");
  } catch (error) { if (epoch === currentEpoch) showError(error); }
  finally { busy = false; setControls(); }
});

$("connect-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const token = $("pairing-token").value.trim();
  $("pairing-token").value = "";
  if (!token) { showError(new Error("Paste the pairing token from your local demo bridge.")); return; }
  busy = true;
  clearError();
  setControls();
  try {
    // This call stays inside the user gesture that submitted the form.
    const granted = await chrome.permissions.request({ origins: ["http://127.0.0.1/*"] });
    if (!granted) throw new Error("Local connection permission was not granted. You can continue in preview.");
    config = await send({ type: "CONNECT", token });
    $("connection-details").open = false;
    await refreshContext();
  } catch (error) { showError(error); status("Local connection was not established."); }
  finally { busy = false; render(); }
});

$("disconnect-local").addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  clearError();
  setControls();
  try {
    config = await send({ type: "DISCONNECT" });
    await refreshContext();
  } catch (error) { showError(error); }
  finally { busy = false; render(); }
});

chrome.tabs.onActivated.addListener(scheduleRefresh);
chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
  if (tab.active && (changeInfo.url || changeInfo.status === "complete")) scheduleRefresh();
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) scheduleRefresh(); });

async function initialize() {
  setControls();
  try { config = await send({ type: "GET_CONFIG" }); }
  catch (error) { showError(error); }
  await refreshContext({ preserveError: true });
}
initialize();
