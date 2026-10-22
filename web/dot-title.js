/* Local glyphs with the upstream Lattice spring, pointer field and burst physics. */
import {Dots} from './vendor/lattice/dots.js';
export function mountDotTitle(title) {
  if (!title) return {setActive() {}, destroy() {}};
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return {setActive() {}, destroy() {}};
  canvas.className = 'lab-dot-title'; canvas.setAttribute('aria-hidden', 'true');
  canvas.hidden = true;
  title.append(canvas);
  const abort = new AbortController(), {signal} = abort;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  let frame = 0, started = null, settled = false, destroyed = false, onScreen = false, active = true;
  let points = [], width = 0, height = 0;
  let paintTimer = 0, dots = null, pointer = null, lastTick = 0;
  let paintProgress = 0, baseColor = '', accentColor = '';
  const duration = 1450, bleed = 48;

  function cancelFrame() {cancelAnimationFrame(frame); frame = 0; lastTick = 0;}
  function stopAtRest() {
    cancelFrame(); settled = true;
    dots?.snap(); pointer = null;
    if (points.length) draw(1);
    canvas.dataset.state = 'settled';
  }

  function draw(progress) {
    paintProgress = progress;
    const complete = progress >= 1;
    ctx.clearRect(-bleed, -bleed, width + bleed * 2, height + bleed * 2);
    if (complete) ctx.globalAlpha = 1;
    for (let i = 0; i < points.length; i++) {
      const point = points[i];
      let x = dots.x[i], y = dots.y[i];
      if (!complete) {
        const phase = Math.max(0, Math.min(1, (progress - point.delay) / (1 - point.delay)));
        const eased = phase * phase * phase * (phase * (phase * 6 - 15) + 10);
        ctx.globalAlpha = Math.min(1, phase * 3);
        x += point.dx * (1 - eased); y += point.dy * (1 - eased);
      }
      ctx.fillStyle = point.accent ? accentColor : baseColor;
      ctx.beginPath();
      ctx.arc(x, y, point.radius, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  function tick(now) {
    frame = 0;
    if (destroyed || !active || document.hidden || !onScreen) {stopAtRest(); return;}
    started ??= now;
    const progress = settled ? 1 : Math.min(1, (now - started) / duration);
    const dt = lastTick ? Math.min((now - lastTick) / 1000, 1 / 30) : 1 / 60;
    lastTick = now;
    if (pointer) dots.attract(pointer.x, pointer.y, 140, -135);
    const moving = dots.step(dt);
    draw(progress);
    if (progress >= 1) {settled = true; canvas.dataset.state = moving || pointer ? 'interactive' : 'settled';}
    if (progress < 1 || moving || pointer) frame = requestAnimationFrame(tick);
    else stopAtRest();
  }

  function layout() {
    paintTimer = 0;
    if (destroyed) return;
    cancelFrame();
    if (reduced.matches || contrast.matches) {
      settled = true; canvas.hidden = true; title.classList.remove('has-dot-title'); canvas.dataset.state = 'static-text'; return;
    }
    const bounds = title.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    width = Math.ceil(bounds.width); height = Math.ceil(bounds.height);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.ceil((width + bleed * 2) * dpr); canvas.height = Math.ceil((height + bleed * 2) * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, bleed * dpr, bleed * dpr);
    const raster = document.createElement('canvas');
    raster.width = width * 2; raster.height = height * 2;
    const mask = raster.getContext('2d', {willReadFrequently: true});
    if (!mask) {canvas.hidden = true; title.classList.remove('has-dot-title'); return;}
    mask.scale(2, 2); mask.fillStyle = '#fff'; mask.textBaseline = 'alphabetic';
    const spans = [...title.querySelectorAll('.home-title-text,.home-title-mark')];
    readColors();
    let accentStart = width;
    for (const span of spans) {
      const rect = span.getBoundingClientRect(), css = getComputedStyle(span);
      const fontSize = parseFloat(css.fontSize);
      mask.font = `${css.fontWeight} ${fontSize}px ${css.fontFamily}`;
      const metrics = mask.measureText(span.textContent);
      const ascent = metrics.fontBoundingBoxAscent ?? fontSize * .9;
      const descent = metrics.fontBoundingBoxDescent ?? fontSize * .2;
      const baseline = rect.top - bounds.top + (rect.height - ascent - descent) / 2 + ascent;
      let x = rect.left - bounds.left;
      if (span.classList.contains('home-title-mark')) accentStart = x;
      const spacing = parseFloat(css.letterSpacing) || 0;
      for (const char of span.textContent) {
        mask.fillText(char, x, baseline); x += mask.measureText(char).width + spacing;
      }
    }
    const pixels = mask.getImageData(0, 0, raster.width, raster.height).data;
    const size = parseFloat(getComputedStyle(title).fontSize);
    const pitch = Math.max(2.1, Math.min(3.4, size / 22));
    points = [];
    for (let y = pitch / 2; y < height; y += pitch) {
      for (let x = pitch / 2; x < width; x += pitch) {
        const alpha = pixels[(Math.floor(y * 2) * raster.width + Math.floor(x * 2)) * 4 + 3];
        if (alpha < 110) continue;
        points.push({x, y, accent: x >= accentStart, radius: pitch * .39,
          dx: Math.sin(y / height * Math.PI * 1.5) * 10 + (x / width - .5) * 8,
          dy: 16 + Math.cos(x / width * Math.PI) * 5, delay: x / width * .14 + y / height * .04});
      }
    }
    dots = new Dots(points.length);
    points.forEach((p,i) => {dots.home(i,p.x,p.y); dots.place(i,p.x,p.y);});dots.snap();
    if (!points.length) {canvas.hidden = true; title.classList.remove('has-dot-title'); return;}
    canvas.hidden = false;
    title.classList.add('has-dot-title');
    if (settled || !active || document.hidden) stopAtRest();
    else if (onScreen) {canvas.dataset.state = 'assembling'; frame = requestAnimationFrame(tick);}
    else draw(0);
  }

  function wake() {
    if (!destroyed && active && onScreen && !document.hidden && dots && !reduced.matches && !contrast.matches && !frame) {
      lastTick = 0; frame = requestAnimationFrame(tick);
    }
  }
  function localPointer(event) {
    const r = title.getBoundingClientRect();
    return {x: event.clientX - r.left, y: event.clientY - r.top};
  }
  title.addEventListener('pointermove', event => {
    if (event.pointerType === 'touch') return;
    pointer = localPointer(event); wake();
  }, {signal, passive: true});
  title.addEventListener('pointerleave', () => {pointer = null; wake();}, {signal});
  title.addEventListener('pointerdown', event => {
    if (event.button !== 0 || reduced.matches || contrast.matches || !dots) return;
    const p = localPointer(event);dots.burst(p.x, p.y, 430);wake();
  }, {signal, passive: true});

  function scheduleLayout() {
    if (!paintTimer) paintTimer = requestAnimationFrame(layout);
  }
  const resize = new ResizeObserver(scheduleLayout); resize.observe(title);
  function readColors() {
    const style = getComputedStyle(document.documentElement);
    baseColor = style.getPropertyValue('--ink').trim();
    accentColor = style.getPropertyValue('--accent').trim();
  }
  const theme = new MutationObserver(() => {
    if (destroyed) return;
    readColors();
    // Color changes retain the glyph mask, spring positions and current intro progress.
    if (!canvas.hidden && points.length) draw(paintProgress);
  });
  theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  const intersection = new IntersectionObserver(entries => {
    onScreen = entries[0].isIntersecting;
    if (!onScreen && started !== null) stopAtRest();
    else if (active && onScreen && !settled && points.length && !frame) {
      canvas.dataset.state = 'assembling'; frame = requestAnimationFrame(tick);
    }
  });
  intersection.observe(title);
  reduced.addEventListener('change', scheduleLayout, {signal});
  contrast.addEventListener('change', scheduleLayout, {signal});
  document.addEventListener('visibilitychange', () => {if (document.hidden) stopAtRest();}, {signal});
  window.addEventListener('pagehide', stopAtRest, {signal});
  document.fonts?.ready.then(() => {if (!destroyed) scheduleLayout();});
  scheduleLayout();
  return {setActive(value) {
    if (destroyed || active === value) return;
    active = value;
    if (!active) stopAtRest();
  }, destroy() {
    destroyed = true; cancelFrame(); cancelAnimationFrame(paintTimer); abort.abort();
    resize.disconnect(); theme.disconnect(); intersection.disconnect();
    title.classList.remove('has-dot-title'); canvas.remove();
  }};
}
