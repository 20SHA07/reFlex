/* Content wiring: the only place where Ember and Slack-specific code meet. */
(function () {
  'use strict';
  if (window.top !== window.self || document.getElementById('ember-overlay')) return;
  const host = document.createElement('div');
  host.id = 'ember-overlay'; host.setAttribute('aria-hidden', 'true'); document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: 'closed' });
  const mount = document.createElement('div');
  mount.style.cssText = 'position:absolute;inset:0;pointer-events:none;overflow:hidden;'; shadow.appendChild(mount);
  const ember = new Ember(mount, { state: 'idle', onUserDrag: () => abortSequence() });
  let proposal = null; let controller = null;
  const abortSequence = () => { if (controller) controller.abort(); controller = null; ember.cancelActions(); ember.setState('idle'); };
  const startReaction = async (verdictMessage, verdicts, options = {}) => {
    if (!proposal) return false;
    abortSequence(); const sequenceController = new AbortController(); controller = sequenceController;
    try { return await runRefereeChoreography({ ember, adapter, proposal, verdictMessage, verdicts, signal: sequenceController.signal, demo: options.demo }); }
    catch (error) { console.debug('[Ember] choreography cancelled or unavailable:', error); return false; }
    finally { if (controller === sequenceController && !sequenceController.signal.aborted) ember.setState('idle'); }
  };
  const adapter = new SlackAdapter({
    onProposal(message) { proposal = message; abortSequence(); const rect = adapter.getMessageRect(message); if (rect) ember.gazeThenGlow(rect.x + rect.width / 2, rect.y + rect.height / 2, 'searching'); },
    onVerdict({ message, verdicts }) { startReaction(message, verdicts); }
  });
  adapter.start();
  const updateIdlePerch = () => {
    const anchor = adapter.getIdleAnchor();
    if (anchor) ember.setHome(anchor.x, anchor.y);
  };
  updateIdlePerch();
  const perchTimer = window.setInterval(updateIdlePerch, 1500);
  window.addEventListener('resize', updateIdlePerch, { passive: true });
  const forwardPointer = (event) => {
    const rect = host.getBoundingClientRect();
    ember.handlePointerMove(event.clientX - rect.left, event.clientY - rect.top);
  };
  document.addEventListener('pointermove', forwardPointer, { passive: true });
  document.addEventListener('pointerup', () => ember.handlePointerUp(), { passive: true });
  const demoVerdicts = [{ verdict: 'ALLOW', path: 'data/source_metrics.csv' }, { verdict: 'DEFER', path: 'working/report_input.csv' }, { verdict: 'BLOCK', path: 'scratch/debug.log' }];
  window.ember = ember;
  window.runChoreography = (verdicts = demoVerdicts) => { proposal = proposal || adapter.getAnyMessage(); return proposal ? startReaction({ id: null, text: '[REFEREE] dev harness' }, verdicts, { demo: true }) : Promise.resolve(false); };
  window.injectFakeRefereeMessage = () => {
    try { const target = adapter.container; if (!target) return false; const fake = document.createElement('div'); fake.setAttribute('role', 'listitem'); fake.dataset.messageId = 'ember-dev-' + Date.now(); fake.style.cssText = 'display:none'; fake.textContent = '[REFEREE] ALLOW:data/source_metrics.csv | DEFER:working/report_input.csv | BLOCK:scratch/debug.log'; target.appendChild(fake); return true; }
    catch (error) { console.debug('[Ember] fake message unavailable:', error); return false; }
  };
  document.addEventListener('keydown', (event) => { if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey) return; if (event.key.toLowerCase() === 'e') { event.preventDefault(); window.runChoreography(); } if (event.key.toLowerCase() === 'f') { event.preventDefault(); window.injectFakeRefereeMessage(); } });
  window.addEventListener('pagehide', () => { abortSequence(); adapter.stop(); window.clearInterval(perchTimer); window.removeEventListener('resize', updateIdlePerch); document.removeEventListener('pointermove', forwardPointer); ember.destroy(); }, { once: true });
  window.__ember = { ember, adapter, host };
})();
