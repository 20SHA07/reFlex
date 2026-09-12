// This isolated content script supplies routing context, never approval authority.
(() => {
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || message?.type !== "REFLEX_PAGE_CONTEXT") return;
    const match = location.pathname.match(/^\/client\/([A-Z0-9]+)\/([A-Z0-9]+)(?:\/|$)/);
    if (location.origin !== "https://app.slack.com" || !match) {
      respond({ok: false, error: {code: "no_context", message: "Open a Slack channel first."}});
      return;
    }
    const context = {
      workspace_id: match[1], channel_id: match[2],
      url: `${location.origin}/client/${match[1]}/${match[2]}`,
    };
    if (message.selection === true) context.selected_text = String(window.getSelection() || "").slice(0, 2000);
    respond({ok: true, context});
  });
})();
