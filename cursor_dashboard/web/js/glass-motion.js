/* Glass-only motion. Surfaces deform; content and card stacking stay stable. */
const GlassMotion = (() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const running = new Set();
  const tracks = new Map();
  const transfers = new WeakMap();
  const enabled = () => document.documentElement.dataset.skin === 'glass'
    && !reduced.matches && typeof Element.prototype.animate === 'function';
  const ease = 'cubic-bezier(.22,.8,.24,1)';

  function play(element, frames, options, complete = () => {}) {
    const animation = element.animate(frames, { easing: ease, fill: 'both', ...options });
    let finished = false;
    const stop = () => {
      if (finished) return;
      finished = true;
      running.delete(stop);
      animation.cancel();
      complete();
    };
    running.add(stop);
    animation.finished.then(stop, stop);
    return stop;
  }

  function selection(container, target, animate = true) {
    if (!container || !target) return;
    let state = tracks.get(container);
    if (!enabled()) {
      state?.stop?.();
      state?.surface.remove();
      container.classList.remove('glass-motion-track');
      tracks.delete(container);
      return;
    }
    if (!state) {
      const surface = document.createElement('span');
      surface.className = 'glass-selection-surface';
      surface.setAttribute('aria-hidden', 'true');
      state = { surface, target: null, stop: null };
      tracks.set(container, state);
    }
    const previous = state.surface.isConnected ? state.surface.getBoundingClientRect() : null;
    const changed = state.target !== target;
    const parent = container.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    const x = rect.left - parent.left - container.clientLeft + container.scrollLeft;
    const y = rect.top - parent.top - container.clientTop + container.scrollTop;
    const bounds = `${x}:${y}:${rect.width}:${rect.height}`;
    if (!changed && state.surface.isConnected && state.bounds === bounds) return;
    state.stop?.();
    state.bounds = bounds;
    container.classList.add('glass-motion-track');
    container.append(state.surface);
    Object.assign(state.surface.style, {
      left: `${x}px`, top: `${y}px`, width: `${rect.width}px`, height: `${rect.height}px`,
      borderRadius: getComputedStyle(target).borderRadius,
    });
    state.target = target;
    if (!animate || !changed || !previous?.width) return;
    const dx = previous.left - rect.left, dy = previous.top - rect.top;
    const horizontal = Math.abs(dx) > Math.abs(dy);
    const stretch = 1 + Math.min(.75, Math.hypot(dx, dy) / (horizontal ? rect.width : rect.height) * .32);
    state.stop = play(state.surface, [
      { transform: `translate(${dx}px, ${dy}px) scale(1, 1)`, borderRadius: '18px' },
      { transform: `translate(${dx * .36}px, ${dy * .36}px) scale(${horizontal ? stretch : .9}, ${horizontal ? .88 : stretch})`, borderRadius: '42% 58% 56% 44% / 50% 42% 58% 50%', offset: .46 },
      { transform: 'translate(0, 0) scale(1.025, .97)', offset: .8 },
      { transform: 'translate(0, 0) scale(1, 1)', borderRadius: getComputedStyle(target).borderRadius },
    ], { duration: 420 }, () => { state.stop = null; });
  }

  function syncSelection(animate = false) {
    selection(document.querySelector('.theme-switch'), document.querySelector('.theme-btn.active'), animate);
    selection(document.querySelector('#department-tabs'), document.querySelector('.dept-nav-item[aria-selected="true"]'), animate);
  }

  function sourceRect(source) {
    if (!source?.isConnected && source?.dataset.detail) {
      source = document.querySelector(`[data-detail="${CSS.escape(source.dataset.detail)}"][data-group="${CSS.escape(source.dataset.group)}"]`);
    }
    if (!source?.isConnected) return null;
    const rect = source.getBoundingClientRect();
    if (!rect.width || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) return null;
    const left = Math.max(8, rect.left), top = Math.max(8, rect.top);
    return { left, top, width: Math.min(innerWidth - 8, rect.right) - left,
      height: Math.max(24, Math.min(innerHeight - 8, rect.bottom) - top) };
  }

  const mix = (from, to, progress) => from + (to - from) * progress;
  const smooth = (t) => t * t * (3 - 2 * t);

  function transfer(dialog, source, closing, complete) {
    const previous = transfers.get(dialog);
    const interrupted = previous ? dialog.getBoundingClientRect() : null;
    const interruptedRadius = previous ? getComputedStyle(dialog).borderRadius : null;
    const content = [...dialog.querySelector('.detail-content').children, dialog.querySelector('.dialog-close')];
    const opacity = content.map((element) => getComputedStyle(element).opacity);
    previous?.stop();
    if (!enabled()) { complete(); return; }

    const target = dialog.getBoundingClientRect();
    const style = getComputedStyle(dialog);
    const padX = parseFloat(style.paddingLeft), padY = parseFloat(style.paddingTop);
    const border = parseFloat(style.borderLeftWidth);
    const origin = sourceRect(source);
    const fallback = { left: target.left + 6, top: target.top + 12,
      width: target.width - 12, height: target.height - 12 };
    const from = interrupted || (closing ? target : origin || fallback);
    const to = closing ? origin || fallback : target;
    const centerX = target.left + target.width / 2, centerY = target.top + target.height / 2;
    const startX = from.left + from.width / 2, startY = from.top + from.height / 2;
    const endX = to.left + to.width / 2, endY = to.top + to.height / 2;
    const panelRadius = parseFloat(style.borderTopLeftRadius);
    const rowRadius = Math.min(12, (origin?.height || 24) / 2);
    const startRadius = interruptedRadius ? parseFloat(interruptedRadius) : closing ? panelRadius : rowRadius;
    const endRadius = closing ? rowRadius : panelRadius;
    const progress = Math.min(1, Math.max(0, (from.height - (origin?.height || 0)) / Math.max(1, target.height - (origin?.height || 0))));
    const duration = closing ? (previous ? Math.max(140, 300 * Math.sqrt(progress)) : 300) : 440;

    // Pin content at its final layout size while the real dialog edges move around it.
    // This avoids both text scaling and the handoff from an empty overlay to a dialog.
    dialog.style.setProperty('--detail-pad-x', `${padX}px`);
    dialog.style.setProperty('--detail-pad-y', `${padY}px`);
    dialog.style.setProperty('--detail-content-width', `${target.width - padX * 2 - border * 2}px`);
    dialog.style.setProperty('--detail-content-height', `${target.height - padY * 2 - border * 2}px`);
    dialog.style.setProperty('--detail-duration', `${duration}ms`);
    dialog.classList.add('glass-transferring');
    dialog.dataset.motion = closing ? 'closing' : 'opening';

    const frames = Array.from({ length: 33 }, (_, index) => {
      const t = index / 32;
      const horizontal = closing ? smooth(t ** 1.25) : 1 - (1 - t) ** 4.5;
      const vertical = closing ? smooth(Math.min(1, t * 1.06)) : 1 - (1 - t) ** 2.8;
      const travelX = closing ? smooth(t) : 1 - (1 - t) ** 4;
      const travelY = closing ? smooth(t) : 1 - (1 - t) ** 3.2;
      const radius = mix(startRadius, endRadius, closing ? vertical : horizontal);
      const tension = Math.sin(Math.PI * t) * (closing ? 5 : 10) * (1 - t);
      const near = radius + tension, far = Math.max(6, radius - tension * .4);
      return {
        offset: t,
        width: `${mix(from.width, to.width, horizontal)}px`,
        height: `${mix(from.height, to.height, vertical)}px`,
        transform: `translate(${mix(startX, endX, travelX) - centerX}px, ${mix(startY, endY, travelY) - centerY}px)`,
        borderRadius: startX < centerX ? `${far}px ${near}px ${radius}px ${near}px` : `${near}px ${far}px ${near}px ${radius}px`,
        opacity: closing ? 1 - smooth(Math.max(0, (t - .68) / .32)) : mix(.85, 1, Math.min(1, t * 4)),
      };
    });
    const contentStops = content.map((element, index) => {
      const header = index < 2 || element.classList.contains('dialog-close');
      return play(element, closing ? [
        { opacity: opacity[index], transform: 'translateY(0)' },
        { opacity: 0, transform: 'translateY(-4px)', offset: header ? .58 : .48 },
        { opacity: 0, transform: 'translateY(-4px)' },
      ] : [
        { opacity: 0, transform: 'translateY(8px)' },
        { opacity: 0, transform: 'translateY(8px)', offset: header ? .1 : .2 },
        { opacity: 1, transform: 'translateY(0)', offset: header ? .58 : .84 },
        { opacity: 1, transform: 'translateY(0)' },
      ], { duration, easing: 'linear' });
    });
    const stop = play(dialog, frames, { duration, easing: 'linear' }, () => {
      transfers.delete(dialog);
      contentStops.forEach((stopContent) => stopContent());
      dialog.classList.remove('glass-transferring');
      delete dialog.dataset.motion;
      for (const key of ['--detail-pad-x', '--detail-pad-y', '--detail-content-width', '--detail-content-height', '--detail-duration']) {
        dialog.style.removeProperty(key);
      }
      complete();
    });
    transfers.set(dialog, { stop });
  }

  const detail = {
    open(dialog, source) { transfer(dialog, source, false, () => {}); },
    close(dialog, source, complete) { transfer(dialog, source, true, complete); },
  };

  function arrive(cards, { added = false } = {}) {
    if (!enabled()) return;
    [...cards].filter((card) => {
      const rect = card.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < innerHeight;
    }).slice(0, 12).forEach((card, index) => {
      const surface = document.createElement('span');
      surface.className = 'glass-card-surface';
      surface.setAttribute('aria-hidden', 'true');
      const children = [...card.children];
      card.classList.add('glass-arriving');
      card.append(surface);
      const delay = Math.min(index * 28, 140);
      const duration = added ? 460 : 340;
      const contentStops = children.map((child) => play(child,
        [{ opacity: 0 }, { opacity: 0, offset: added ? .58 : .32 }, { opacity: 1 }],
        { duration, delay, easing: added ? 'linear' : ease }));
      play(surface, [
        { transform: added ? 'scale(.12, .1)' : 'scale(.96, .92)', borderRadius: added ? '46% 54% 62% 38%' : '32px', opacity: 0 },
        { transform: added ? 'scale(.7, .52)' : 'scale(.985, .97)', borderRadius: added ? '38% 62% 44% 56% / 54% 42% 58% 46%' : '26px', opacity: 1, offset: .4 },
        { transform: 'scale(1.008, .992)', borderRadius: getComputedStyle(card).borderRadius, opacity: 1, offset: .82 },
        { transform: 'scale(1, 1)', borderRadius: getComputedStyle(card).borderRadius, opacity: 1 },
      ], { duration, delay, easing: added ? 'linear' : ease }, () => {
        contentStops.forEach((stop) => stop());
        surface.remove();
        card.classList.remove('glass-arriving');
      });
    });
  }

  function refreshed(card, previousWidths = []) {
    if (!enabled() || !card) return;
    const rect = card.getBoundingClientRect();
    if (rect.top >= innerHeight || rect.bottom <= 0) return;
    card.querySelectorAll('.fill').forEach((fill, index) => {
      if (previousWidths[index] == null) return;
      play(fill, [{ width: previousWidths[index] }, { width: fill.style.width }], { duration: 420 });
    });
    const wave = document.createElement('span');
    wave.className = 'glass-refresh-wave';
    wave.setAttribute('aria-hidden', 'true');
    card.append(wave);
    play(wave, [
      { clipPath: 'circle(0% at calc(100% - 57px) 27px)', opacity: .9 },
      { clipPath: 'circle(65% at calc(100% - 57px) 27px)', opacity: .45, offset: .55 },
      { clipPath: 'circle(145% at calc(100% - 57px) 27px)', opacity: 0 },
    ], { duration: 550 }, () => wave.remove());
  }

  function reset() {
    [...running].forEach((stop) => stop());
    syncSelection();
  }
  reduced.addEventListener('change', reset);
  new MutationObserver(reset).observe(document.documentElement, { attributes: true, attributeFilter: ['data-skin'] });
  const resize = new ResizeObserver(() => syncSelection());
  for (const selector of ['.theme-switch', '#department-tabs']) {
    const container = document.querySelector(selector);
    if (container) resize.observe(container);
  }
  window.addEventListener('resize', () => {
    [...running].forEach((stop) => stop());
    syncSelection();
  });

  return { enabled, selection, syncSelection, detail, arrive, refreshed };
})();
