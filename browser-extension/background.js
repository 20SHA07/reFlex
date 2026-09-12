import {contextKey, parseContext, sameContext, panelSender} from "./context.mjs";
import {dispatchPreview, initialState, publicState} from "./preview.mjs";
import {createBridgeClient, BRIDGE_JOB_TIMEOUT_MS} from "./bridge.mjs";

const bridge = createBridgeClient();
const operations = new Set(["status", "start_report", "start_cleanup", "approve_report", "approve_cleanup"]);
let pending = Promise.resolve();
const error = (code, message) => ({ok: false, error: {code, message}});

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
    return {ok: true, context, tabId: tab.id};
  } catch {
    return error("no_context", "Open Slack in this window. Reload Slack once after installing reFlex.");
  }
}

async function publishSpriteState(tabId, context, result) {
  if (!result?.ok || !result.state || !tabId) return;
  try {
    await chrome.tabs.sendMessage(tabId, {type: "REFLEX_REFEREE_STATE", context, state: result.state});
  } catch {
    // The Slack content script may not be available yet; the panel remains usable.
  }
}

async function config() {
  const {connection} = await chrome.storage.session.get("connection");
  return connection
    ? {ok: true, mode: "local-demo", generation: connection.generation || "sample", principal: connection.principal}
    : {ok: true, mode: "preview", principal: {id: "preview-owner", display_name: "Preview owner"}};
}

async function handle(message, deadline) {
  await storageReady;
  switch (message?.type) {
    case "GET_CONTEXT": {
      const active = await activeContext(message.selection === true);
      return active.ok ? {ok: true, context: active.context} : active;
    }
    case "GET_CONFIG": return config();
    case "CONNECT": {
      if (!await chrome.permissions.contains({origins: ["http://127.0.0.1/*"]})) {
        return error("permission_required", "Allow the local connection before pairing.");
      }
      if (typeof message.token !== "string" || message.token.length < 20 || message.token.length > 256 || /[\r\n]/.test(message.token)) {
        return error("invalid_token", "Paste the pairing token created by your local referee.");
      }
      const token = message.token.trim();
      const result = await bridge.session(token);
      if (!result.ok) return result;
      if (result.mode !== "local-demo" || typeof result.principal?.id !== "string") {
        return error("bridge_response", "This extension needs the local-demo bridge contract.");
      }
      await chrome.storage.session.set({connection: {token, principal: result.principal, generation: result.generation === "openrouter" ? "openrouter" : "sample"}});
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
        const result = await bridge.dispatch(connection.token, request, deadline);
        await publishSpriteState(active.tabId, active.context, result);
        return result;
      }
      const key = `preview:${contextKey(active.context)}`;
      const stored = await chrome.storage.session.get(key);
      try {
        const next = dispatchPreview(stored[key] || initialState(active.context), request);
        await chrome.storage.session.set({[key]: next});
        const result = {ok: true, state: publicState(next)};
        await publishSpriteState(active.tabId, active.context, result);
        return result;
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
  // Include queue time so a refresh behind an AI job cannot hold its own
  // service-worker message open for another four minutes.
  const deadline = Date.now() + BRIDGE_JOB_TIMEOUT_MS;
  const task = pending.then(() => handle(message, deadline));
  pending = task.catch(() => {});
  task.then(respond).catch(() => respond(error("extension_error", "The extension could not finish this request. Refresh and try again.")));
  return true;
});
