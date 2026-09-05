/* Glass-only motion. Surfaces deform; content and card stacking stay stable. */
const GlassMotion = (() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const running = new Set();
  const tracks = new Map();
  const transfers = new WeakMap();
  const enabled = () => document.documentElement.dataset.skin === 'glass'
    && !reduced.matches && typeof Element.prototype.animate === 'function';
  const ease = 'cubic-bezier(.22,.8,.24,1)';
  const SLIDE_MS = 950;
  const mix = (from, to, progress) => from + (to - from) * progress;
  const smooth = (t) => t * t * (3 - 2 * t);
  // 分段线性 + 每段 smoothstep：拐点处不留折角
  const curve = (stops) => (t) => {
    for (let i = 1; i < stops.length; i += 1) {
      const [t0, v0] = stops[i - 1], [t1, v1] = stops[i];
      if (t <= t1) return mix(v0, v1, smooth((t - t0) / (t1 - t0 || 1)));
    }
    return stops.at(-1)[1];
  };
  // 走过的路程。中间那段陡的就是水滴穿越经过的 tab
  const travel = curve([[0, 0], [.3, .05], [.5, .42], [.68, .97], [.8, 1.015], [1, 1]]);
  // 水滴化程度：0 = 满框，1 = 缩到最小的一滴，负值是落地时鼓出去的那一下
  const morph = curve([[0, 0], [.2, .78], [.32, 1], [.66, 1], [.82, .38], [.93, -.05], [1, 0]]);

  // ---- 形状 ----
  // border-radius 画不出水滴：椭圆角在边中点相接时切线是连续的，接出来只会是个蛋；
  // 唯一的尖是 0 半径那个直角，可它两侧的直边长度只有正方形时才相等，一拉长就成了
  // 歪叶子，而正方形的尖又只比圆头多伸出 0.2 倍边长。所以形状交给 clip-path 自己画。
  // 两个形状都摊成**同样多**的极角采样点，polygon 之间才插得动。
  const OUTLINE = 36;
  const ANGLES = Array.from({ length: OUTLINE }, (_, i) => i / OUTLINE * Math.PI * 2);
  // 形状只用"点在不在里面"描述，边界靠二分找：比逐段求交好写，也不挑形状
  const outline = (inside) => ANGLES.map((angle) => {
    const cos = Math.cos(angle), sin = Math.sin(angle);
    let lo = 0, hi = 80;
    for (let i = 0; i < 20; i += 1) {
      const mid = (lo + hi) / 2;
      if (inside(50 + mid * cos, 50 + mid * sin)) lo = mid; else hi = mid;
    }
    return [50 + lo * cos, 50 + lo * sin];
  });
  const roundedRect = (rx, ry) => (x, y) => {
    const px = Math.abs(x - 50), py = Math.abs(y - 50);
    if (px > 50 || py > 50) return false;
    const ox = Math.max(px - (50 - rx), 0) / (rx || 1), oy = Math.max(py - (50 - ry), 0) / (ry || 1);
    return ox * ox + oy * oy <= 1;
  };
  // 一滴水 = 头上那个圆 + 尖点拉出的两条切线围成的三角。尖朝上，用时再转到方向上去。
  // 尾长 41 / 头径 56，比正方形那版长了三倍不止
  const droplet = (() => {
    const R = 28, cy = 70, apex = [50, 1];
    const d = cy - apex[1], cosB = R / d, sinB = Math.sqrt(1 - cosB * cosB);
    const p1 = [50 + R * sinB, cy - R * cosB], p2 = [50 - R * sinB, cy - R * cosB];
    const side = (a, b, x, y) => (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
    return (x, y) => {
      if ((x - 50) ** 2 + (y - cy) ** 2 <= R * R) return true;
      const d1 = side(apex, p1, x, y), d2 = side(p1, p2, x, y), d3 = side(p2, apex, x, y);
      return (d1 >= 0 && d2 >= 0 && d3 >= 0) || (d1 <= 0 && d2 <= 0 && d3 <= 0);
    };
  })();
  const turned = (inside, radians) => (x, y) => {
    const dx = x - 50, dy = y - 50, cos = Math.cos(-radians), sin = Math.sin(-radians);
    return inside(50 + dx * cos - dy * sin, 50 + dx * sin + dy * cos);
  };
  const polygon = (points) =>
    `polygon(${points.map(([x, y]) => `${x.toFixed(2)}% ${y.toFixed(2)}%`).join(',')})`;

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
    // 三段：先整块收成一滴，再让这滴穿过中间那个 tab，落位后重新摊开成框。
    // 只做拉伸（旧写法）看着是"一个被拽长的方块滑过去"，不是一滴水。
    const W = rect.width, H = rect.height;
    // previous 可能是上一轮动画中途的形状，所以中心和尺寸都按它当时的实际值算，
    // 半路改主意时才不会跳一下
    const cdx = previous.left - rect.left + (previous.width - W) / 2;
    const cdy = previous.top - rect.top + (previous.height - H) / 2;
    // 位移小到不值得演一遍（窗口缩放，或者上一轮动画被冻在半路后重入）：直接就位。
    // 顺带守住下面这行——两个分量都接近 0 时，横竖之分是没有意义的
    if (Math.hypot(cdx, cdy) < 4) return;
    const horizontal = Math.abs(cdx) > Math.abs(cdy);
    // 水滴盒子是正方形：形状由 clip-path 在盒子里画，转向不靠转元素，所以外接盒
    // 不会跟着胀大，也就不会探出托盘
    const core = Math.min(W, H);
    const full = parseFloat(getComputedStyle(target).borderTopLeftRadius) || 0;
    // 尖朝运动的反方向；droplet 的标准姿态尖朝上(-90°)，所以要多转 90°
    const aim = horizontal ? (cdx > 0 ? 0 : Math.PI) : (cdy > 0 ? Math.PI / 2 : -Math.PI / 2);
    const from = outline(roundedRect(full / W * 100, full / H * 100));
    const to = outline(turned(droplet, aim + Math.PI / 2));
    // 出尖比收缩晚一步：横条还没缩下去就先长出尖，那一下像被掐了一把
    const shaped = (drop) => smooth(Math.min(1, Math.max(0, (drop - .35) / .65)));
    const box = [], clip = [];
    for (let index = 0; index <= 40; index += 1) {
      const t = index / 40;
      const drop = morph(t), gone = travel(t), form = shaped(Math.max(0, drop));
      // drop=1 那一刻两边都等于水滴尺寸，所以在 t=.5 换锚点不会断
      const w = mix(t < .5 ? previous.width : W, core, drop);
      const h = mix(t < .5 ? previous.height : H, core, drop);
      box.push({ offset: t, width: `${w}px`, height: `${h}px`,
        // left/top 钉在终点，收缩以中心为锚，所以要把半个尺寸差补回来
        transform: `translate(${(W - w) / 2 + cdx * (1 - gone)}px, ${(H - h) / 2 + cdy * (1 - gone)}px)` });
      clip.push({ offset: t,
        clipPath: polygon(from.map(([x, y], i) => [mix(x, to[i][0], form), mix(y, to[i][1], form)])) });
    }
    state.surface.classList.add('is-sliding');
    // clip-path 是在 filter **之后**才应用的，描边和投影画在同一个元素上会被自己裁掉
    // （实测拿 drop-shadow(24px 0 0 red) 验过，影子一点不剩）。所以形状归 ::before，
    // filter 留在外层，描边才跟得住裁完的轮廓。
    const stopClip = play(state.surface, clip, { duration: SLIDE_MS, easing: 'linear', pseudoElement: '::before' });
    // easing 走 linear：快慢全在上面两条曲线里，交给 cubic-bezier 会把三段揉平
    state.stop = play(state.surface, box, { duration: SLIDE_MS, easing: 'linear' }, () => {
      stopClip();
      state.stop = null;
      state.surface.classList.remove('is-sliding');
    });
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
