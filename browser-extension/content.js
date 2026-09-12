// Slack context source plus the visual-only referee overlay.
(() => {
  const stateMessage = "REFLEX_REFEREE_STATE";
  const contextMessage = "REFLEX_PAGE_CONTEXT";
  const hostId = "reflex-referee-sprite";
  const reaction = {
    ALLOW: { card: "accept", state: "found", severity: 1 },
    REVIEW: { card: "defer", state: "searching", severity: 2 },
    DEFER: { card: "defer", state: "searching", severity: 2 },
    BLOCK: { card: "reject", state: "certain", severity: 3 },
  };
  const shortcutVerdicts = { e: "ALLOW", w: "DEFER", r: "BLOCK" };

  const pageContext = () => {
    const match = location.pathname.match(/^\/client\/([A-Z0-9]+)\/([A-Z0-9]+)(?:\/|$)/);
    if (location.origin !== "https://app.slack.com" || !match) return null;
    return { workspace_id: match[1], channel_id: match[2], url: `${location.origin}/client/${match[1]}/${match[2]}` };
  };
  const sameContext = (left, right) => left && right && left.workspace_id === right.workspace_id && left.channel_id === right.channel_id;

  let ember = null;
  let adapter = null;
  let controller = null;
  let knownItems = new Map();
  let perchTimer = null;

  const stopReaction = () => {
    if (controller) controller.abort();
    controller = null;
    if (ember) { ember.cancelActions(); ember.setState("idle"); }
  };
  const updatePerch = () => {
    const anchor = adapter && adapter.getIdleAnchor();
    if (anchor && ember) ember.setHome(anchor.x, anchor.y);
  };
  const delay = (milliseconds, signal) => new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });

  async function showDecisions(items) {
    stopReaction();
    const sequence = new AbortController();
    controller = sequence;
    const ordered = items.slice().sort((left, right) => reaction[left.verdict].severity - reaction[right.verdict].severity);
    for (const item of ordered) {
      if (sequence.signal.aborted) return;
      const display = reaction[item.verdict];
      ember.setState(display.state);
      await ember.presentCard(display.card);
      await delay(120, sequence.signal);
    }
    if (!sequence.signal.aborted) ember.setState("idle");
  }

  function observeRefereeState(state) {
    const items = Array.isArray(state?.cleanup?.items) ? state.cleanup.items : [];
    const nextItems = new Map();
    const changes = [];
    items.forEach((item) => {
      if (!reaction[item.verdict]) return;
      const signature = `${item.verdict}:${Boolean(item.executed)}:${item.revision || ""}`;
      nextItems.set(item.id, signature);
      if (knownItems.get(item.id) !== signature) changes.push(item);
    });
    knownItems = nextItems;
    if (changes.length) showDecisions(changes);
  }

  function mountSprite() {
    if (document.getElementById(hostId) || !document.body) return;
    const host = document.createElement("div");
    host.id = hostId;
    host.setAttribute("aria-hidden", "true");
    host.style.cssText = "position:fixed;inset:0;z-index:2147483646;pointer-events:none;margin:0;padding:0;border:0;";
    document.body.appendChild(host);
    const shadow = host.attachShadow({ mode: "closed" });
    const mount = document.createElement("div");
    mount.style.cssText = "position:absolute;inset:0;pointer-events:none;overflow:hidden;";
    shadow.appendChild(mount);
    ember = new Ember(mount, { state: "idle", onUserDrag: stopReaction });
    adapter = new SlackAdapter();
    const forwardPointer = (event) => {
      const rect = host.getBoundingClientRect();
      ember.handlePointerMove(event.clientX - rect.left, event.clientY - rect.top);
    };
    document.addEventListener("pointermove", forwardPointer, { passive: true });
    document.addEventListener("pointerup", () => ember.handlePointerUp(), { passive: true });
    document.addEventListener("keydown", (event) => {
      if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey) return;
      const verdict = shortcutVerdicts[event.key.toLowerCase()];
      if (!verdict) return;
      event.preventDefault();
      showDecisions([{ verdict }]);
    });
    updatePerch();
    perchTimer = setInterval(updatePerch, 1500);
    window.addEventListener("resize", updatePerch, { passive: true });
  }

  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id) return;
    if (message?.type === contextMessage) {
      const context = pageContext();
      if (!context) { respond({ ok: false, error: { code: "no_context", message: "Open a Slack channel first." } }); return; }
      if (message.selection === true) context.selected_text = String(window.getSelection() || "").slice(0, 2000);
      respond({ ok: true, context });
      return;
    }
    if (message?.type === stateMessage && sameContext(pageContext(), message.context)) observeRefereeState(message.state);
  });

  if (typeof document !== "undefined") {
    mountSprite();
    window.addEventListener("pagehide", () => {
      stopReaction();
      if (perchTimer) clearInterval(perchTimer);
      if (ember) ember.destroy();
    }, { once: true });
  }
})();
