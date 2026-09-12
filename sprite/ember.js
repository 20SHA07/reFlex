/* The Ember: portable behaviour plus a replaceable canvas skin. */
(function (global) {
  'use strict';

  // Every tuneable number belongs here. Override any nested value via `options`.
  const EMBER_CONFIG = {
    size: {
      coreRadius: 24, eyeRadius: 5, eyeSpacing: 13, eyeVerticalOffset: -4,
      pupilRadius: 2.4, maxPupilTravel: 2.2
    },
    palette: {
      core: '#ffffff', mid: '#60a5fa', outer: '#2563eb', eye: '#1f2937', pupil: '#ffffff',
      state: { idle: null, searching: null, found: null, certain: null }
    },
    glow: { radiusMultiplier: 3, falloffCurve: 2, gradientStops: [0, 0.26, 0.58, 1], stateMultiplier: { idle: 1, searching: 1, found: 1, certain: 1 } },
    tail: { historyLength: 25, radiusTaper: 0.8, alphaFalloff: 0.86, minSpeed: 8 },
    physics: {
      springStiffness: 160, damping: 19, friction: 1.8, dragLag: 0.12,
      bounceRestitution: 0.48, idleDriftSpeed: 2, wanderAmount: 12,
      bobAmplitude: 2, bobPeriod: 3200, squashStretchIntensity: 0.00012,
      seekStiffness: 8, seekDamping: 5, stillDamping: 18, maxVelocity: 1800,
      maxDeltaSeconds: 0.05, substepSeconds: 0.008333333333333333,
      motionMultiplier: { drift: 1, purposeful: 1.8, frozen: 0 }
    },
    timing: {
      stateTransitionDuration: 400, gazeHoldDuration: 350,
      blinkIntervalRange: [3000, 7000], blinkDuration: 150, doubleBlinkChance: 0.16,
      doubleBlinkPause: 90, pulsePeriods: { idle: 0, searching: 1800, found: 900, certain: 0 },
      cardDrawDuration: 220, cardReturnDuration: 220,
      cardHoldDuration: { accept: 800, reject: 2000, defer: 1500 }
    },
    behavior: { hoverBrightnessBoost: 0.12, edgePadding: 6, hitTestRadius: 31, mouseFollowStrength: 0.035, mouseFollowMaxDistance: 18 },
    canvas: { contextType: '2d', ariaHidden: 'true', style: 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;touch-action:none;z-index:' },
    cards: {
      width: 28, height: 40, cornerRadius: 4, offsetX: 26, offsetY: -30,
      labelFont: 'bold 18px system-ui, sans-serif', labelColor: '#ffffff',
      types: { accept: { color: '#22a06b', label: '✓' }, reject: { color: '#d92d20', label: '✕' }, defer: { color: '#f0a202', label: '!' } }
    },
    referee: {
      skinLight: '#fff1df', skinMid: '#f4ad78', uniform: '#1f2937', uniformStripe: '#ffffff', uniformOutline: '#111827', rimLight: '#ffffff', rimDark: '#0f172a',
      headRadius: 15, headOffsetY: -10, headOutlineWidth: 2, bodyRadius: 19, bodyOffsetY: 9, bodyOutlineWidth: 2, rimWidth: 3,
      stripeWidth: 4, stripeOffsets: [-8, 0, 8], armWidth: 4, armStartX: 12, armStartY: 4, armReachX: 25, armReachY: -24,
      lineCap: 'round'
    },
    face: {
      eyeWhite: '#ffffff', pupil: '#263238', eyeRadius: 3.6, pupilRadius: 1.35, eyeSpacing: 5.8, eyeVerticalOffset: -10,
      eyebrow: '#6b3f2c', eyebrowWidth: 1.8, eyebrowLength: 5, eyebrowGap: 2, eyebrowTilt: 2,
      mouth: '#8f3f33', mouthWidth: 5, mouthOffsetY: -2, smileDepth: 3, highlightRadius: 0.65, lineCap: 'round'
    },
    easing: { stateTransition: 'easeInOutQuad', motion: 'easeOutCubic', blink: 'eyeBlink', pulse: 'pulseSine', cardDraw: 'easeOutCubic', cardReturn: 'easeInOutQuad' },
    constants: {
      zero: 0, one: 1, two: 2, half: 0.5, thousand: 1000, tau: Math.PI * 2,
      millisecondsPerSecond: 1000, canvasZIndex: 1, defaultOpacity: 1,
      illuminationGap: 14, illuminationStrength: 0.48, illuminationRadius: 180,
      reducedMotionScale: 0.25, startPositionX: 0.5, startPositionY: 0.65,
      radix: 16
    }
  };

  // States are data. Add a state here; update() and render() require no edits.
  const EMBER_STATES = {
    idle:      { brightness: 0.35, glowScale: 1.0, pulse: null, motion: 'drift',      bob: true,  squint: 0 },
    searching: { brightness: 0.6,  glowScale: 1.4, pulse: { period: 1800, depth: 0.15 }, motion: 'purposeful', bob: true, squint: 0 },
    found:     { brightness: 0.8,  glowScale: 1.8, pulse: { period: 900, depth: 0.25 },  motion: 'drift',      bob: true, squint: 0 },
    certain:   { brightness: 1.0,  glowScale: 2.2, pulse: null, motion: 'frozen',     bob: false, squint: 0.4 }
  };
  EMBER_CONFIG.states = EMBER_STATES;

  const EASING = {
    linear: (t) => t,
    easeOutCubic: (t) => EMBER_CONFIG.constants.one - Math.pow(EMBER_CONFIG.constants.one - t, EMBER_CONFIG.constants.two + EMBER_CONFIG.constants.one),
    easeInOutQuad: (t) => t < EMBER_CONFIG.constants.half
      ? EMBER_CONFIG.constants.two * t * t
      : EMBER_CONFIG.constants.one - Math.pow(-EMBER_CONFIG.constants.two * t + EMBER_CONFIG.constants.two, EMBER_CONFIG.constants.two) / EMBER_CONFIG.constants.two,
    eyeBlink: (t) => Math.abs(Math.cos(t * Math.PI)),
    pulseSine: (t) => Math.sin(t * EMBER_CONFIG.constants.tau)
  };

  const deepMerge = (base, override) => {
    const result = Array.isArray(base) ? base.slice() : Object.assign({}, base);
    if (!override) return result;
    Object.keys(override).forEach((key) => {
      const source = override[key];
      result[key] = source && typeof source === 'object' && !Array.isArray(source)
        ? deepMerge(base[key] || {}, source) : source;
    });
    return result;
  };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const lerp = (from, to, amount) => from + (to - from) * amount;
  const randomBetween = (range) => range[EMBER_CONFIG.constants.zero] + Math.random() * (range[EMBER_CONFIG.constants.one] - range[EMBER_CONFIG.constants.zero]);
  const length = (x, y) => Math.hypot(x, y);

  class Ember {
    constructor(container, options = {}) {
      if (!container || !container.appendChild) throw new Error('Ember needs a container element.');
      this.container = container;
      this.config = deepMerge(EMBER_CONFIG, options.config);
      this.callbacks = { onStateChange: options.onStateChange || null, onUserDrag: options.onUserDrag || null };
      this.reducedMotion = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.destroyed = false;
      this.lastFrame = null;
      this.frame = null;
      this.canvas = container.ownerDocument.createElement('canvas');
      this.canvas.setAttribute('aria-hidden', this.config.canvas.ariaHidden);
      this.canvas.style.cssText = this.config.canvas.style + this.config.constants.canvasZIndex + ';';
      container.appendChild(this.canvas);
      this.ctx = this.canvas.getContext(this.config.canvas.contextType);
      this.state = this._createState(options);
      this._resize = this._resize.bind(this);
      this._onPointerDown = this._onPointerDown.bind(this);
      this._onPointerUp = this._onPointerUp.bind(this);
      this.resizeObserver = new ResizeObserver(this._resize);
      this.resizeObserver.observe(container);
      this.canvas.addEventListener('pointerdown', this._onPointerDown);
      this._resize();
      this.frame = requestAnimationFrame((now) => this._tick(now));
    }

    _createState(options) {
      const constants = this.config.constants;
      const name = this.config.states[options.state] ? options.state : 'idle';
      return {
        x: options.x, y: options.y, vx: constants.zero, vy: constants.zero,
        bounds: { width: constants.zero, height: constants.zero }, time: constants.zero,
        stateName: name, targetStateName: name, transition: null,
        visual: Object.assign({}, this.config.states[name]), gaze: null, pointer: null,
        pupil: { x: constants.zero, y: constants.zero }, blink: { next: randomBetween(this.config.timing.blinkIntervalRange), progress: null, double: false },
        tail: [], wander: { x: constants.zero, y: constants.zero }, home: null, drag: null,
        move: null, sequence: null, illumination: null, card: null, squash: constants.one,
        hover: false, opacity: constants.defaultOpacity, pulsePhase: constants.zero
      };
    }

    setState(name, opts = {}) {
      const target = this.config.states[name];
      if (!target) return false;
      const constants = this.config.constants;
      const duration = opts.immediate ? constants.zero : (opts.duration ?? this.config.timing.stateTransitionDuration);
      this.state.targetStateName = name;
      this.state.transition = { from: Object.assign({}, this.state.visual), to: Object.assign({}, target), elapsed: constants.zero, duration };
      if (duration === constants.zero) this._finishTransition();
      if (this.callbacks.onStateChange) this.callbacks.onStateChange(name);
      return true;
    }

    lookAt(x, y) { this.state.gaze = x == null || y == null ? null : { x, y }; }

    gazeThenGlow(x, y, state = 'found') {
      const constants = this.config.constants;
      this.lookAt(x, y);
      this.state.sequence = { phase: 'gaze', elapsed: constants.zero, target: { x, y }, state };
      return new Promise((resolve) => { this.state.sequence.resolve = resolve; });
    }

    illuminate(rect, opts = {}) {
      if (!rect) return Promise.resolve(false);
      const constants = this.config.constants;
      const gap = opts.gap ?? constants.illuminationGap;
      const target = this._illuminationPosition(rect, gap);
      this.state.illumination = { rect: Object.assign({}, rect), strength: opts.strength ?? constants.illuminationStrength, target };
      return this.gazeThenGlow(target.x, target.y, opts.state || 'found');
    }

    releaseIllumination() { this.state.illumination = null; }
    clearIllumination() { this.releaseIllumination(); }
    updateIlluminationRect(rect) {
      if (this.state.illumination && rect) {
        this.state.illumination.rect = Object.assign({}, rect);
        this.state.illumination.target = this._illuminationPosition(rect, this.config.constants.illuminationGap);
      }
    }

    moveTo(x, y, opts = {}) {
      const constants = this.config.constants;
      this.state.move = { x, y, resolve: null, arrived: false };
      if (opts.freeze) this.state.sequence = { phase: 'move', elapsed: constants.zero, target: { x, y }, state: this.state.targetStateName };
      return new Promise((resolve) => { this.state.move.resolve = resolve; });
    }

    presentCard(type, opts = {}) {
      if (!this.config.cards.types[type]) return Promise.resolve(false);
      const constants = this.config.constants;
      if (this.state.card && this.state.card.resolve) this.state.card.resolve(false);
      this.state.card = { type, phase: 'draw', elapsed: constants.zero, hold: opts.hold ?? this.config.timing.cardHoldDuration[type], opacity: constants.zero, extension: constants.zero, resolve: null };
      return new Promise((resolve) => { this.state.card.resolve = resolve; });
    }

    getPosition() { return { x: this.state.x, y: this.state.y }; }
    getState() { return this.state.stateName; }
    setHome(x, y) {
      if (Number.isFinite(x) && Number.isFinite(y)) this.state.home = { x, y };
    }
    handlePointerMove(x, y) {
      this.state.pointer = { x, y };
      const active = length(x - this.state.x, y - this.state.y) <= this.config.behavior.hitTestRadius;
      this.state.hover = active;
      this.canvas.style.pointerEvents = active || this.state.drag ? 'auto' : 'none';
      this.canvas.style.cursor = this.state.drag ? 'grabbing' : active ? 'grab' : 'default';
      if (this.state.drag) this.state.drag = { x, y };
    }
    handlePointerUp() { this._onPointerUp(); }
    cancelActions() {
      if (this.state.sequence && this.state.sequence.resolve) this.state.sequence.resolve(false);
      if (this.state.move && this.state.move.resolve) this.state.move.resolve(false);
      if (this.state.card && this.state.card.resolve) this.state.card.resolve(false);
      this.state.sequence = null;
      this.state.move = null;
      this.state.card = null;
      this.releaseIllumination();
      this.lookAt(null, null);
    }
    destroy() {
      this.destroyed = true;
      cancelAnimationFrame(this.frame);
      this.resizeObserver.disconnect();
      this.canvas.remove();
    }

    _resize() {
      const rect = this.container.getBoundingClientRect();
      const ratio = global.devicePixelRatio || this.config.constants.one;
      this.canvas.width = Math.max(this.config.constants.one, Math.round(rect.width * ratio));
      this.canvas.height = Math.max(this.config.constants.one, Math.round(rect.height * ratio));
      this.canvas.style.width = rect.width + 'px'; this.canvas.style.height = rect.height + 'px';
      this.ctx.setTransform(ratio, this.config.constants.zero, this.config.constants.zero, ratio, this.config.constants.zero, this.config.constants.zero);
      this.state.bounds.width = rect.width; this.state.bounds.height = rect.height;
      const radius = this.config.size.coreRadius;
      this.state.x = Number.isFinite(this.state.x) ? this.state.x : rect.width * this.config.constants.startPositionX;
      this.state.y = Number.isFinite(this.state.y) ? this.state.y : rect.height * this.config.constants.startPositionY;
      this._clampPosition(radius);
    }

    _tick(now) {
      if (this.destroyed) return;
      const elapsed = this.lastFrame == null ? this.config.constants.zero : (now - this.lastFrame) / this.config.constants.millisecondsPerSecond;
      this.lastFrame = now;
      this.update(Math.min(elapsed, this.config.physics.maxDeltaSeconds));
      this.render(this.ctx);
      this.frame = requestAnimationFrame((time) => this._tick(time));
    }

    // Simulation only: no Canvas calls, DOM reads, or side effects.
    update(dt) {
      const constants = this.config.constants;
      const capped = clamp(dt, constants.zero, this.config.physics.maxDeltaSeconds);
      const steps = Math.max(constants.one, Math.ceil(capped / this.config.physics.substepSeconds));
      const step = capped / steps;
      for (let index = constants.zero; index < steps; index += constants.one) this._simulateStep(step);
    }

    _simulateStep(dt) {
      const state = this.state;
      const config = this.config;
      const constants = config.constants;
      state.time += dt * constants.millisecondsPerSecond;
      this._advanceTransition(dt);
      this._advanceSequence(dt);
      this._advanceBlink(dt);
      this._advanceEyes(dt);
      this._advanceCard(dt);
      this._advanceMotion(dt);
      this._advanceTail(dt);
      state.pulsePhase += dt * constants.millisecondsPerSecond;
    }

    _advanceTransition(dt) {
      const transition = this.state.transition;
      if (!transition) return;
      transition.elapsed += dt * this.config.constants.millisecondsPerSecond;
      const progress = transition.duration ? clamp(transition.elapsed / transition.duration, this.config.constants.zero, this.config.constants.one) : this.config.constants.one;
      const amount = EASING[this.config.easing.stateTransition](progress);
      Object.keys(transition.to).forEach((key) => {
        this.state.visual[key] = typeof transition.to[key] === 'number' ? lerp(transition.from[key], transition.to[key], amount) : transition.to[key];
      });
      if (progress === this.config.constants.one) this._finishTransition();
    }

    _finishTransition() {
      this.state.visual = Object.assign({}, this.config.states[this.state.targetStateName]);
      this.state.stateName = this.state.targetStateName;
      this.state.transition = null;
    }

    _advanceSequence(dt) {
      const sequence = this.state.sequence;
      if (!sequence) return;
      sequence.elapsed += dt * this.config.constants.millisecondsPerSecond;
      if (sequence.phase === 'gaze' && sequence.elapsed >= this.config.timing.gazeHoldDuration) {
        sequence.phase = 'react'; sequence.elapsed = this.config.constants.zero;
        this.setState(sequence.state);
        this.state.move = { x: sequence.target.x, y: sequence.target.y, resolve: null, arrived: false };
      }
      if (sequence.phase === 'react' && !this.state.move) {
        const resolve = sequence.resolve;
        this.state.sequence = null;
        if (resolve) resolve(true);
      }
    }

    _advanceBlink(dt) {
      const blink = this.state.blink;
      const constants = this.config.constants;
      if (blink.progress == null) {
        blink.next -= dt * constants.millisecondsPerSecond;
        if (blink.next <= constants.zero) blink.progress = constants.zero;
        return;
      }
      blink.progress += dt * constants.millisecondsPerSecond / this.config.timing.blinkDuration;
      if (blink.progress >= constants.one) {
        if (!blink.double && Math.random() < this.config.timing.doubleBlinkChance) {
          blink.double = true; blink.progress = -this.config.timing.doubleBlinkPause / this.config.timing.blinkDuration;
        } else {
          blink.progress = null; blink.double = false; blink.next = randomBetween(this.config.timing.blinkIntervalRange);
        }
      }
    }

    _advanceCard(dt) {
      const card = this.state.card;
      if (!card) return;
      const constants = this.config.constants;
      const timing = this.config.timing;
      card.elapsed += dt * constants.millisecondsPerSecond;
      if (card.phase === 'draw') {
        const progress = clamp(card.elapsed / timing.cardDrawDuration, constants.zero, constants.one);
        card.extension = EASING[this.config.easing.cardDraw](progress);
        card.opacity = card.extension;
        if (progress === constants.one) { card.phase = 'hold'; card.elapsed = constants.zero; }
        return;
      }
      if (card.phase === 'hold') {
        card.extension = constants.one; card.opacity = constants.one;
        if (card.elapsed >= card.hold) { card.phase = 'return'; card.elapsed = constants.zero; }
        return;
      }
      const progress = clamp(card.elapsed / timing.cardReturnDuration, constants.zero, constants.one);
      card.extension = constants.one - EASING[this.config.easing.cardReturn](progress);
      card.opacity = card.extension;
      if (progress === constants.one) {
        const resolve = card.resolve;
        this.state.card = null;
        if (resolve) resolve(true);
      }
    }

    _advanceEyes(dt) {
      const target = this.state.gaze || this.state.pointer;
      const constants = this.config.constants;
      if (!target) return;
      const dx = target.x - this.state.x; const dy = target.y - this.state.y;
      const distance = Math.max(constants.one, length(dx, dy));
      const travel = this.config.size.maxPupilTravel;
      const desiredX = dx / distance * travel; const desiredY = dy / distance * travel;
      const amount = constants.one - Math.exp(-dt / this.config.physics.dragLag);
      this.state.pupil.x = lerp(this.state.pupil.x, desiredX, amount);
      this.state.pupil.y = lerp(this.state.pupil.y, desiredY, amount);
    }

    _advanceMotion(dt) {
      const state = this.state; const config = this.config; const constants = config.constants;
      const moving = state.move || state.illumination && { x: state.illumination.target.x, y: state.illumination.target.y };
      const motionScale = this.reducedMotion ? constants.reducedMotionScale : constants.one;
      const mode = state.visual.motion;
      let target = moving || state.drag;
      if (!target && mode !== 'frozen') target = this._wanderTarget(motionScale);
      if (target && !state.drag) this._applySpring(target.x, target.y, config.physics.seekStiffness, config.physics.seekDamping, dt);
      if (state.drag) this._applySpring(target.x, target.y, config.physics.springStiffness, config.physics.damping, dt);
      if (mode === 'frozen' && !state.drag && !moving) { state.vx *= Math.exp(-config.physics.stillDamping * dt); state.vy *= Math.exp(-config.physics.stillDamping * dt); }
      state.vx *= Math.exp(-config.physics.friction * dt); state.vy *= Math.exp(-config.physics.friction * dt);
      const velocity = length(state.vx, state.vy);
      const scale = config.physics.squashStretchIntensity;
      state.squash = clamp(constants.one + velocity * scale, constants.one - scale * config.physics.maxVelocity, constants.one + scale * config.physics.maxVelocity);
      state.x += state.vx * dt; state.y += state.vy * dt;
      this._bounceBounds();
      if (state.move && length(state.move.x - state.x, state.move.y - state.y) <= config.behavior.edgePadding && velocity <= config.behavior.edgePadding) {
        const resolve = state.move.resolve; state.move = null; if (resolve) resolve(true);
      }
    }

    _wanderTarget(motionScale) {
      const state = this.state; const config = this.config; const constants = config.constants;
      const phase = state.time / config.physics.bobPeriod * constants.tau;
      const behaviorScale = config.physics.motionMultiplier[state.visual.motion];
      const amount = config.physics.wanderAmount * motionScale * behaviorScale;
      const speed = config.physics.idleDriftSpeed * motionScale * behaviorScale;
      const home = state.home || { x: state.bounds.width * constants.half, y: state.bounds.height * constants.startPositionY };
      const pointerX = state.pointer ? state.pointer.x - home.x : constants.zero;
      const pointerY = state.pointer ? state.pointer.y - home.y : constants.zero;
      const pointerDistance = Math.max(constants.one, length(pointerX, pointerY));
      const mouseAmount = Math.min(pointerDistance * config.behavior.mouseFollowStrength, config.behavior.mouseFollowMaxDistance);
      return {
        x: home.x + pointerX / pointerDistance * mouseAmount + Math.sin(phase) * amount + Math.cos(phase * constants.half) * speed,
        y: home.y + pointerY / pointerDistance * mouseAmount + (state.visual.bob ? Math.sin(phase) * config.physics.bobAmplitude * motionScale : constants.zero)
      };
    }

    _applySpring(x, y, stiffness, damping, dt) {
      this.state.vx += ((x - this.state.x) * stiffness - this.state.vx * damping) * dt;
      this.state.vy += ((y - this.state.y) * stiffness - this.state.vy * damping) * dt;
    }

    _bounceBounds() {
      const radius = this.config.size.coreRadius; const padding = this.config.behavior.edgePadding; const restitution = this.config.physics.bounceRestitution;
      const minX = radius + padding; const maxX = this.state.bounds.width - radius - padding;
      const minY = radius + padding; const maxY = this.state.bounds.height - radius - padding;
      if (this.state.x < minX || this.state.x > maxX) { this.state.x = clamp(this.state.x, minX, maxX); this.state.vx *= -restitution; }
      if (this.state.y < minY || this.state.y > maxY) { this.state.y = clamp(this.state.y, minY, maxY); this.state.vy *= -restitution; }
    }

    _clampPosition(radius) {
      const padding = this.config.behavior.edgePadding;
      this.state.x = clamp(this.state.x, radius + padding, this.state.bounds.width - radius - padding);
      this.state.y = clamp(this.state.y, radius + padding, this.state.bounds.height - radius - padding);
    }

    _advanceTail() {
      const tail = this.state.tail; const constants = this.config.constants;
      tail.unshift({ x: this.state.x, y: this.state.y });
      if (tail.length > this.config.tail.historyLength) tail.pop();
      if (tail.length === constants.one) tail.push({ x: this.state.x, y: this.state.y });
    }

    // Rendering only: reads `state`, has no timing, physics, or state mutation.
    render(ctx) {
      const state = this.state;
      const bounds = state.bounds;
      ctx.clearRect(this.config.constants.zero, this.config.constants.zero, bounds.width, bounds.height);
      ctx.save();
      ctx.globalAlpha = state.opacity;
      this._drawTail(ctx, state);
      this._drawGlow(ctx, state);
      this._drawCore(ctx, state);
      this._drawEyes(ctx, state);
      ctx.restore();
    }

    _drawTail(ctx, state) {
      const tail = state.tail; const config = this.config; const constants = config.constants;
      const speed = length(state.vx, state.vy);
      if (speed < config.tail.minSpeed) return;
      tail.forEach((point, index) => {
        const fraction = constants.one - index / tail.length;
        ctx.beginPath(); ctx.fillStyle = config.palette.outer;
        ctx.globalAlpha = state.opacity * Math.pow(fraction, config.tail.alphaFalloff);
        ctx.arc(point.x, point.y, config.size.coreRadius * fraction * config.tail.radiusTaper, constants.zero, constants.tau);
        ctx.fill();
      });
      ctx.globalAlpha = state.opacity;
    }

    _drawGlow(ctx, state) {
      const config = this.config; const constants = config.constants; const visual = this._visualValues(state);
      const illumination = state.illumination;
      const radius = config.size.coreRadius * config.glow.radiusMultiplier * visual.glow;
      const gradient = ctx.createRadialGradient(state.x, state.y, constants.zero, state.x, state.y, radius);
      const falloff = Math.pow(visual.brightness, config.glow.falloffCurve);
      gradient.addColorStop(config.glow.gradientStops[constants.zero], this._alphaColor(visual.palette.core, falloff));
      gradient.addColorStop(config.glow.gradientStops[constants.one], this._alphaColor(visual.palette.mid, falloff * constants.half));
      gradient.addColorStop(config.glow.gradientStops[constants.two], this._alphaColor(visual.palette.outer, falloff * constants.half));
      gradient.addColorStop(config.glow.gradientStops[config.glow.gradientStops.length - constants.one], this._alphaColor(visual.palette.outer, constants.zero));
      ctx.fillStyle = gradient; ctx.beginPath(); ctx.arc(state.x, state.y, radius, constants.zero, constants.tau); ctx.fill();
      if (illumination) {
        const light = ctx.createRadialGradient(state.x, state.y, constants.zero, state.x, state.y, constants.illuminationRadius);
        light.addColorStop(constants.zero, this._alphaColor(visual.palette.mid, illumination.strength));
        light.addColorStop(constants.one, this._alphaColor(visual.palette.outer, constants.zero));
        ctx.fillStyle = light; ctx.fillRect(illumination.rect.x - constants.illuminationRadius, illumination.rect.y - constants.illuminationRadius, illumination.rect.width + constants.illuminationRadius * constants.two, illumination.rect.height + constants.illuminationRadius * constants.two);
      }
    }

    _drawCore(ctx, state) {
      const config = this.config; const constants = config.constants; const referee = config.referee;
      const bodyY = state.y + referee.bodyOffsetY;
      this._drawCardArm(ctx, state);
      this._drawPresentedCard(ctx, state);
      ctx.save(); ctx.translate(state.x, bodyY); ctx.scale(state.squash, constants.two - state.squash); ctx.translate(-state.x, -bodyY);
      ctx.fillStyle = referee.rimLight; ctx.beginPath(); ctx.arc(state.x, bodyY, referee.bodyRadius + referee.rimWidth, constants.zero, constants.tau); ctx.fill();
      ctx.fillStyle = referee.uniformOutline; ctx.beginPath(); ctx.arc(state.x, bodyY, referee.bodyRadius, constants.zero, constants.tau); ctx.fill();
      ctx.save(); ctx.beginPath(); ctx.arc(state.x, bodyY, referee.bodyRadius - referee.bodyOutlineWidth, constants.zero, constants.tau); ctx.clip();
      ctx.fillStyle = referee.uniform; ctx.fillRect(state.x - referee.bodyRadius, bodyY - referee.bodyRadius, referee.bodyRadius * constants.two, referee.bodyRadius * constants.two);
      ctx.fillStyle = referee.uniformStripe;
      referee.stripeOffsets.forEach((offset) => ctx.fillRect(state.x + offset - referee.stripeWidth / constants.two, bodyY - referee.bodyRadius, referee.stripeWidth, referee.bodyRadius * constants.two));
      ctx.restore(); ctx.restore();
      const headY = state.y + referee.headOffsetY;
      const head = ctx.createRadialGradient(state.x - referee.headRadius * constants.half, headY - referee.headRadius * constants.half, constants.zero, state.x, headY, referee.headRadius);
      head.addColorStop(constants.zero, referee.skinLight); head.addColorStop(constants.one, referee.skinMid);
      ctx.fillStyle = referee.rimDark; ctx.beginPath(); ctx.arc(state.x, headY, referee.headRadius + referee.headOutlineWidth, constants.zero, constants.tau); ctx.fill();
      ctx.fillStyle = head; ctx.beginPath(); ctx.arc(state.x, headY, referee.headRadius, constants.zero, constants.tau); ctx.fill();
    }

    _drawCardArm(ctx, state) {
      const card = state.card;
      if (!card) return;
      const config = this.config; const referee = config.referee;
      ctx.save(); ctx.strokeStyle = referee.skinMid; ctx.lineWidth = referee.armWidth; ctx.lineCap = referee.lineCap;
      ctx.beginPath();
      ctx.moveTo(state.x + referee.armStartX, state.y + referee.bodyOffsetY + referee.armStartY);
      ctx.lineTo(state.x + referee.armReachX * card.extension, state.y + referee.armReachY * card.extension);
      ctx.stroke(); ctx.restore();
    }

    _drawPresentedCard(ctx, state) {
      const card = state.card;
      if (!card) return;
      const config = this.config; const constants = config.constants; const design = config.cards.types[card.type];
      const width = config.cards.width * card.extension;
      const height = config.cards.height * card.extension;
      const x = state.x + config.cards.offsetX * card.extension - width / constants.two;
      const y = state.y + config.cards.offsetY * card.extension - height / constants.two;
      const radius = Math.min(config.cards.cornerRadius, width / constants.two, height / constants.two);
      ctx.save();
      ctx.globalAlpha *= card.opacity;
      ctx.fillStyle = design.color;
      ctx.beginPath();
      ctx.roundRect(x, y, width, height, radius);
      ctx.fill();
      if (card.extension > constants.zero) {
        ctx.fillStyle = config.cards.labelColor;
        ctx.font = config.cards.labelFont;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(design.label, x + width / constants.two, y + height / constants.two);
      }
      ctx.restore();
    }

    _drawEyes(ctx, state) {
      const config = this.config; const constants = config.constants; const visual = this._visualValues(state); const face = config.face;
      const blink = state.blink.progress == null ? constants.one : EASING[config.easing.blink](state.blink.progress);
      const eyeHeight = face.eyeRadius * (constants.one - visual.squint) * blink;
      const eyeY = state.y + face.eyeVerticalOffset;
      const positions = [-face.eyeSpacing, face.eyeSpacing];
      positions.forEach((offset) => {
        const x = state.x + offset;
        ctx.fillStyle = face.eyeWhite; ctx.beginPath(); ctx.ellipse(x, eyeY, face.eyeRadius, Math.max(constants.one, eyeHeight), constants.zero, constants.zero, constants.tau); ctx.fill();
        if (blink > constants.zero) {
          ctx.fillStyle = face.pupil; ctx.beginPath(); ctx.arc(x + state.pupil.x, eyeY + state.pupil.y, face.pupilRadius, constants.zero, constants.tau); ctx.fill();
          ctx.fillStyle = face.eyeWhite; ctx.beginPath(); ctx.arc(x + state.pupil.x - face.highlightRadius, eyeY + state.pupil.y - face.highlightRadius, face.highlightRadius, constants.zero, constants.tau); ctx.fill();
        }
      });
      ctx.strokeStyle = face.eyebrow; ctx.lineWidth = face.eyebrowWidth; ctx.lineCap = face.lineCap;
      positions.forEach((offset) => {
        const browY = eyeY - face.eyeRadius - face.eyebrowGap;
        const tilt = visual.squint * face.eyebrowTilt;
        ctx.beginPath(); ctx.moveTo(state.x + offset - face.eyebrowLength / constants.two, browY + tilt); ctx.lineTo(state.x + offset + face.eyebrowLength / constants.two, browY - tilt); ctx.stroke();
      });
      const mouthY = state.y + face.mouthOffsetY;
      const smile = face.smileDepth * (constants.one - visual.squint);
      ctx.strokeStyle = face.mouth; ctx.lineWidth = face.eyebrowWidth; ctx.beginPath();
      ctx.moveTo(state.x - face.mouthWidth, mouthY); ctx.quadraticCurveTo(state.x, mouthY + smile, state.x + face.mouthWidth, mouthY); ctx.stroke();
    }

    _visualValues(state) {
      const constants = this.config.constants; const visual = state.visual;
      const pulse = visual.pulse && Object.assign({}, visual.pulse, { period: this.config.timing.pulsePeriods[state.targetStateName] || visual.pulse.period });
      const reduced = this.reducedMotion ? constants.reducedMotionScale : constants.one;
      const pulseAmount = pulse ? EASING[this.config.easing.pulse](state.pulsePhase / pulse.period) * pulse.depth * reduced : constants.zero;
      const palette = this.config.palette.state[state.targetStateName] || this.config.palette;
      return { brightness: clamp(visual.brightness + pulseAmount + (state.hover ? this.config.behavior.hoverBrightnessBoost : constants.zero), constants.zero, constants.one), glow: visual.glowScale * this.config.glow.stateMultiplier[state.targetStateName] + pulseAmount, squint: visual.squint, palette };
    }

    _alphaColor(hex, alpha) {
      const value = hex.replace('#', '');
      const red = parseInt(value.slice(this.config.constants.zero, this.config.constants.two), this.config.constants.radix);
      const green = parseInt(value.slice(this.config.constants.two, this.config.constants.two + this.config.constants.two), this.config.constants.radix);
      const blue = parseInt(value.slice(this.config.constants.two + this.config.constants.two, this.config.constants.two + this.config.constants.two + this.config.constants.two), this.config.constants.radix);
      return 'rgba(' + red + ',' + green + ',' + blue + ',' + alpha + ')';
    }

    _illuminationPosition(rect, gap) {
      const radius = this.config.size.coreRadius;
      const right = rect.x + rect.width + radius + gap;
      const left = rect.x - radius - gap;
      const x = right + radius < this.state.bounds.width ? right : left;
      return { x: clamp(x, radius, this.state.bounds.width - radius), y: clamp(rect.y + rect.height / this.config.constants.two, radius, this.state.bounds.height - radius) };
    }

    _onPointerDown(event) {
      if (!this.state.hover) return;
      this.cancelActions();
      this.state.drag = { x: event.clientX - this.container.getBoundingClientRect().left, y: event.clientY - this.container.getBoundingClientRect().top };
      if (this.callbacks.onUserDrag) this.callbacks.onUserDrag();
      this.canvas.setPointerCapture(event.pointerId);
      event.preventDefault();
    }

    _onPointerUp() {
      if (!this.state.drag) return;
      this.state.drag = null;
      if (this.state.sequence) this.state.sequence = null;
      this.setState('idle');
    }
  }

  global.EMBER_CONFIG = EMBER_CONFIG;
  global.EMBER_STATES = EMBER_STATES;
  global.EMBER_EASING = EASING;
  global.Ember = Ember;
})(window);
