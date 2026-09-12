export function parseContext(value) {
  if (!value || typeof value !== "object") return null;
  try {
    const url = new URL(value.url);
    if (url.origin !== "https://app.slack.com") return null;
    const match = url.pathname.match(/^\/client\/([A-Z0-9]+)\/([A-Z0-9]+)(?:\/|$)/);
    if (!match || value.workspace_id !== match[1] || value.channel_id !== match[2]) return null;
    return {workspace_id: match[1], channel_id: match[2], url: `${url.origin}/client/${match[1]}/${match[2]}`};
  } catch { return null; }
}

export function contextKey(context) {
  const valid = parseContext(context);
  if (!valid) throw new Error("Open a channel in Slack's website first.");
  return `${valid.workspace_id}:${valid.channel_id}`;
}

export function sameContext(a, b) {
  const left = parseContext(a), right = parseContext(b);
  return Boolean(left && right && contextKey(left) === contextKey(right));
}

export function panelSender(sender, runtime) {
  return sender?.id === runtime.id && sender?.url === runtime.getURL("panel.html");
}
