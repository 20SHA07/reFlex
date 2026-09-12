import {contextKey, parseContext, sameContext, panelSender} from "./context.mjs";
import {dispatchPreview, initialState, publicState} from "./preview.mjs";

const BRIDGE = "http://127.0.0.1:8765";
const operations = new Set(["status", "start_report", "start_cleanup", "approve_report", "approve_cleanup"]);
let pending = Promise.resolve();
const error = (code, message) => ({ok: false, error: {code, message}});

function agentDescriptor(value) {
  if (value === undefined || (value?.provider === "sample" && value.model === null)) {
    return {provider: "sample", model: null};
  }
  if (value?.provider === "openrouter" && typeof value.model === "string" && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$/.test(value.model)) {
    return {provider: "openrouter", model: value.model};
  }
  return null;
}

function principalDescriptor(value) {
  if (typeof value?.id !== "string" || !value.id || value.id.length > 128) return null;
  return {id: value.id, display_name: typeof value.display_name === "string" ? value.display_name.slice(0, 120) : value.id};
}

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {});
// Session storage defaults to trusted contexts. Keep it that way explicitly.
const storageReady = chrome.storage.session.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});

async function activeContext(selection = false) {
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  if (!tab?.id) return error("no_context", "Open a channel in Slack's website, then refresh.");
  try {
    const result = await chrome.tabs.sendMessage(tab.id, {type: "REFLEX_PAGE_CONTEXT", selection});
    const context = result?.ok && parseContext(result.context);
    if (!context) return error("no_context", "Open a channel in Slack's website, then refresh.");
    if (selection) context.selected_text = String(result.context.selected_text || "").slice(0, 2000);
    return {ok: true, context};
  } catch {
    return error("no_context", "Open Slack in this window. Reload Slack once after installing reFlex.");
  }
}

async function config() {
  const {connection} = await chrome.storage.session.get("connection");
  return connection
    ? {ok: true, mode: "local-demo", principal: principalDescriptor(connection.principal), agent: agentDescriptor(connection.agent)}
    : {ok: true, mode: "preview", principal: {id: "preview-owner", display_name: "Preview owner"}, agent: agentDescriptor()};
}

async function bridgeFetch(path, token, body) {
  try {
    const response = await fetch(`${BRIDGE}${path}`, {
      method: body ? "POST" : "GET", credentials: "omit", redirect: "error", cache: "no-store",
      headers: {Authorization: `Bearer ${token}`, ...(body ? {"Content-Type": "application/json"} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}), signal: AbortSignal.timeout(28000),
    });
    const result = await response.json();
    if (typeof result?.ok !== "boolean" || (!response.ok && result.ok)) return error("bridge_response", "The local referee returned an invalid response.");
    return result;
  } catch {
    return error("bridge_unavailable", "Cannot confirm the local referee's response. Check browser_bridge.py and pairing, then refresh to see whether the action completed before retrying.");
  }
}

async function handle(message) {
  await storageReady;
  switch (message?.type) {
    case "GET_CONTEXT": return activeContext(message.selection === true);
    case "GET_CONFIG": return config();
    case "CONNECT": {
      if (!await chrome.permissions.contains({origins: ["http://127.0.0.1/*"]})) {
        return error("permission_required", "Allow the local connection before pairing.");
      }
      if (typeof message.token !== "string" || message.token.length < 20 || message.token.length > 256 || /[\r\n]/.test(message.token)) {
        return error("invalid_token", "Paste the pairing token created by your local referee.");
      }
      const token = message.token.trim();
      const result = await bridgeFetch("/v1/session", token);
      if (!result.ok) return result;
      const principal = principalDescriptor(result.principal);
      const agent = agentDescriptor(result.agent);
      if (result.mode !== "local-demo" || !principal || !agent) {
        return error("bridge_response", "This extension needs the local-demo bridge contract.");
      }
      await chrome.storage.session.set({connection: {token, principal, agent}});
      return config();
    }
    case "DISCONNECT": {
      await chrome.storage.session.remove("connection");
      return config();
    }
    case "DISPATCH": {
      if (!operations.has(message.operation)) return error("invalid_operation", "Unknown referee operation.");
      const active = await activeContext();
      if (!active.ok) return active;
      if (!sameContext(active.context, message.context)) return error("context_changed", "The active Slack channel changed. Refresh before continuing.");
      if (message.input?.text !== undefined && (typeof message.input.text !== "string" || message.input.text.length > 2000)) {
        return error("invalid_request", "Keep the request under 2,000 characters.");
      }
      const request = {
        operation: message.operation, request_id: message.request_id,
        context: active.context, input: {text: message.input?.text || ""},
        ...(message.target ? {target: {id: message.target.id, revision: message.target.revision}} : {}),
      };
      const {connection} = await chrome.storage.session.get("connection");
      if (connection) {
        const result = await bridgeFetch("/v1/dispatch", connection.token, request);
        if (!result.ok) return result;
        const agent = agentDescriptor(result.state?.agent);
        const principal = principalDescriptor(result.state?.principal);
        if (result.state?.mode !== "local-demo" || !agent || !principal) {
          return error("bridge_response", "The local referee returned invalid session details. Check the bridge and reconnect.");
        }
        await chrome.storage.session.set({connection: {token: connection.token, principal, agent}});
        return {ok: true, state: {...result.state, principal, agent}};
      }
      const key = `preview:${contextKey(active.context)}`;
      const stored = await chrome.storage.session.get(key);
      try {
        const next = dispatchPreview(stored[key] || initialState(active.context), request);
        await chrome.storage.session.set({[key]: next});
        return {ok: true, state: publicState(next)};
      } catch (cause) { return error(cause.code || "preview_error", cause.message); }
    }
    default: return error("invalid_message", "Unknown extension request.");
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!panelSender(sender, chrome.runtime)) {
    respond(error("untrusted_sender", "Use the reFlex extension panel for this action."));
    return false;
  }
  const task = pending.then(() => handle(message));
  pending = task.catch(() => {});
  task.then(respond).catch(() => respond(error("extension_error", "The extension could not finish this request. Refresh and try again.")));
  return true;
});
