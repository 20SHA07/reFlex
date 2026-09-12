/* Slack-only DOM boundary. Ember itself never needs to know this file exists. */
(function (global) {
  'use strict';

  const REFEREE_LINE = /\[\s*REFEREE\s*\]\s*([\s\S]*)/i;
  const ENTRY = /\b(BLOCK|DEFER|REVIEW|ALLOW)\s*:\s*([^|\n]+)/gi;
  const CANDIDATE_SELECTORS = [
    '[data-qa="slack_kit_list"]',
    '[data-qa="message_pane"] [role="list"]',
    '[role="main"] [role="list"]',
    '[role="list"]'
  ];
  const RETRY_MS = 1000;
  const IDLE_PERCH = { horizontalInset: 46, verticalFraction: 0.72 };

  function parseVerdicts(messageText) {
    const line = String(messageText || '').match(REFEREE_LINE);
    if (!line) return [];
    const verdicts = [];
    let match;
    ENTRY.lastIndex = 0;
    while ((match = ENTRY.exec(line[1]))) {
      const path = match[2].trim();
      if (path) verdicts.push({ verdict: match[1].toUpperCase(), path });
    }
    return verdicts;
  }

  class SlackAdapter {
    constructor({ onProposal, onVerdict, logger = console } = {}) {
      this.onProposal = onProposal || (() => {});
      this.onVerdict = onVerdict || (() => {});
      this.logger = logger;
      this.container = null;
      this.observer = null;
      this.retry = null;
      this.seen = new Set();
    }

    start() {
      this._discover();
      this.retry = global.setInterval(() => this._discover(), RETRY_MS);
    }

    stop() {
      if (this.retry) global.clearInterval(this.retry);
      if (this.observer) this.observer.disconnect();
      this.retry = null; this.observer = null; this.container = null;
    }

    _discover() {
      try {
        const found = CANDIDATE_SELECTORS.map((selector) => document.querySelector(selector)).find(Boolean);
        if (!found || found === this.container) return;
        if (this.observer) this.observer.disconnect();
        this.container = found;
        this.logger.debug('[Ember] Slack message container:', found);
        this.observer = new MutationObserver((records) => {
          records.forEach((record) => record.addedNodes.forEach((node) => this._inspectNode(node)));
        });
        this.observer.observe(found, { childList: true, subtree: true });
        found.querySelectorAll('[data-ts], [data-message-id], [role="listitem"]').forEach((node) => this._inspectNode(node));
      } catch (error) {
        this.logger.debug('[Ember] message discovery deferred:', error);
      }
    }

    _inspectNode(node) {
      try {
        if (!(node instanceof Element)) return;
        const message = node.matches('[data-ts], [data-message-id], [role="listitem"]') ? node : node.closest('[data-ts], [data-message-id], [role="listitem"]');
        if (!message) return;
        const identity = this._identity(message);
        if (!identity || this.seen.has(identity)) return;
        const text = message.innerText || message.textContent || '';
        if (!text.trim()) return;
        this.seen.add(identity);
        const verdicts = parseVerdicts(text);
        const descriptor = { id: identity, text };
        if (verdicts.length) this.onVerdict({ message: descriptor, verdicts });
        else this.onProposal(descriptor);
      } catch (error) {
        this.logger.debug('[Ember] message inspection skipped:', error);
      }
    }

    _identity(element) {
      return element.dataset.ts || element.dataset.messageId || element.getAttribute('data-item-key') || null;
    }

    _messageElement(id) {
      try {
        const candidates = document.querySelectorAll('[data-ts], [data-message-id], [data-item-key], [role="listitem"]');
        return Array.from(candidates).find((element) => this._identity(element) === id) || null;
      } catch (_) { return null; }
    }

    getMessageRect(message) {
      const element = this._messageElement(message && message.id);
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return rect.width && rect.height ? this._plainRect(rect) : null;
    }

    getAnyMessage() {
      try {
        const element = document.querySelector('[data-ts], [data-message-id], [data-item-key], [role="listitem"]');
        const id = element && this._identity(element);
        return id ? { id, text: element.innerText || element.textContent || '' } : null;
      } catch (_) { return null; }
    }

    getIdleAnchor() {
      try {
        const list = CANDIDATE_SELECTORS.map((selector) => document.querySelector(selector)).find(Boolean);
        if (!list) return null;
        const rect = list.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;
        return {
          x: rect.right - IDLE_PERCH.horizontalInset,
          y: rect.top + rect.height * IDLE_PERCH.verticalFraction
        };
      } catch (error) {
        this.logger.debug('[Ember] idle perch unavailable:', error);
        return null;
      }
    }

    getPathRect(message, path) {
      try {
        const element = this._messageElement(message && message.id);
        if (!element) return null;
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          const start = node.textContent.indexOf(path);
          if (start < 0) continue;
          const range = document.createRange();
          range.setStart(node, start); range.setEnd(node, start + path.length);
          const rect = range.getClientRects()[0];
          range.detach();
          return rect ? this._plainRect(rect) : null;
        }
      } catch (error) { this.logger.debug('[Ember] path target unavailable:', error); }
      return null;
    }

    trackPath(message, path, ember, onLost) {
      let frame = null;
      let lost = false;
      const measure = () => {
        frame = null;
        const rect = this.getPathRect(message, path);
        if (rect && this._isVisible(rect)) ember.updateIlluminationRect(rect);
        else if (!lost) { lost = true; ember.releaseIllumination(); if (onLost) onLost(); }
      };
      const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
      global.addEventListener('resize', schedule, { passive: true });
      global.addEventListener('scroll', schedule, { passive: true, capture: true });
      return () => {
        if (frame) cancelAnimationFrame(frame);
        global.removeEventListener('resize', schedule);
        global.removeEventListener('scroll', schedule, true);
      };
    }

    _plainRect(rect) { return { x: rect.left, y: rect.top, width: rect.width, height: rect.height }; }
    _isVisible(rect) {
      return rect.x + rect.width > 0 && rect.y + rect.height > 0 && rect.x < global.innerWidth && rect.y < global.innerHeight;
    }
  }

  global.parseVerdicts = parseVerdicts;
  global.SlackAdapter = SlackAdapter;
})(window);
