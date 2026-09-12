/* Cancellable reaction choreography. All verdict-specific choices live here. */
(function (global) {
  'use strict';

  // Backward-compatible REVIEW is shown as a yellow defer card.
  const REACTION = {
    ALLOW:  { severity: 1, state: 'found',     card: 'accept' },
    REVIEW: { severity: 2, state: 'searching', card: 'defer' },
    DEFER:  { severity: 2, state: 'searching', card: 'defer' },
    BLOCK:  { severity: 3, state: 'certain',   card: 'reject' }
  };
  const active = (signal) => !signal || !signal.aborted;

  async function runChoreography({ ember, adapter, proposal, verdictMessage, verdicts, signal, demo = false }) {
    if (!ember || !adapter || !proposal || !active(signal)) return false;
    const proposalRect = adapter.getMessageRect(proposal);
    if (proposalRect) await ember.gazeThenGlow(proposalRect.x + proposalRect.width / 2, proposalRect.y + proposalRect.height / 2, 'searching');
    if (!active(signal)) return false;
    ember.setState('searching');
    const ordered = verdicts.slice().filter((item) => REACTION[item.verdict]).sort((left, right) => REACTION[left.verdict].severity - REACTION[right.verdict].severity);
    for (const item of ordered) {
      if (!active(signal)) return false;
      const reaction = REACTION[item.verdict];
      const rect = adapter.getPathRect(proposal, item.path) || (demo && adapter.getMessageRect(proposal));
      if (!rect) { ember.releaseIllumination(); ember.setState('idle'); return false; }
      let targetLost = false;
      const stopTracking = adapter.trackPath(proposal, item.path, ember, () => { targetLost = true; });
      ember.setState(reaction.state);
      await ember.illuminate(rect, { state: reaction.state });
      if (active(signal)) await ember.presentCard(reaction.card);
      stopTracking(); ember.releaseIllumination();
      if (!active(signal) || targetLost) { ember.setState('idle'); return false; }
    }
    const verdictRect = adapter.getMessageRect(verdictMessage);
    ember.setState('found');
    if (verdictRect) await ember.moveTo(verdictRect.x + verdictRect.width / 2, verdictRect.y + verdictRect.height / 2);
    ember.setState('idle');
    return true;
  }

  global.runRefereeChoreography = runChoreography;
})(window);
