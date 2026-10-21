/* Lattice-inspired Chinese lettering: sample local glyphs, assemble once, then stop drawing. */
export function mountDotTitle(title) {
  if (!title) return {destroy() {}};
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return {destroy() {}};
  canvas.className = 'lab-dot-title'; canvas.setAttribute('aria-hidden', 'true');
  title.append(canvas);
  const abort = new AbortController(), {signal} = abort;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  let frame = 0, started = null, settled = false, destroyed = false, onScreen = false;
  let points = [], width = 0, height = 0;
  let paintTimer = 0;
  const duration = 1150;

  function cancelFrame() {cancelAnimationFrame(frame); frame = 0;}
  function stopAtRest() {
    cancelFrame(); settled = true;
    if (points.length) draw(1);
    canvas.dataset.state = 'settled';
  }

  function draw(progress) {
    ctx.clearRect(0, 0, width, height);
    for (const point of points) {
      const phase = Math.max(0, Math.min(1, (progress - point.delay) / (1 - point.delay)));
      const eased = 1 - (1 - phase) ** 3;
      ctx.globalAlpha = Math.min(1, phase * 3);
      ctx.fillStyle = point.color;
      ctx.beginPath();
      ctx.arc(point.x + point.dx * (1 - eased), point.y + point.dy * (1 - eased), point.radius, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  function tick(now) {
    frame = 0;
    if (destroyed || document.hidden || !onScreen) {stopAtRest(); return;}
    started ??= now;
    const progress = Math.min(1, (now - started) / duration);
    draw(progress);
    if (progress < 1) frame = requestAnimationFrame(tick);
    else stopAtRest();
  }

  function layout() {
    paintTimer = 0;
    if (destroyed) return;
    cancelFrame();
    if (reduced.matches || contrast.matches) {
      settled = true; title.classList.remove('has-dot-title'); canvas.dataset.state = 'static-text'; return;
    }
    const bounds = title.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    width = Math.ceil(bounds.width); height = Math.ceil(bounds.height);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = Math.ceil(width * dpr); canvas.height = Math.ceil(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const raster = document.createElement('canvas');
    raster.width = width * 2; raster.height = height * 2;
    const mask = raster.getContext('2d', {willReadFrequently: true});
    if (!mask) {title.classList.remove('has-dot-title'); return;}
    mask.scale(2, 2); mask.fillStyle = '#fff'; mask.textBaseline = 'alphabetic';
    const spans = [...title.querySelectorAll('.home-title-text,.home-title-mark')];
    const baseColor = getComputedStyle(document.documentElement).getPropertyValue('--ink').trim();
    const accentColor = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
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
    const pitch = Math.max(1.8, Math.min(2.5, size / 19));
    points = [];
    for (let y = pitch / 2; y < height; y += pitch) {
      for (let x = pitch / 2; x < width; x += pitch) {
        const alpha = pixels[(Math.floor(y * 2) * raster.width + Math.floor(x * 2)) * 4 + 3];
        if (alpha < 110) continue;
        const index = points.length;
        points.push({x, y, color: x >= accentStart ? accentColor : baseColor, radius: pitch * .39,
          dx: Math.sin(index * 2.399) * 8, dy: 10 + (index % 9) * 1.8, delay: x / width * .19});
      }
    }
    if (!points.length) {title.classList.remove('has-dot-title'); return;}
    title.classList.add('has-dot-title');
    if (settled || document.hidden) stopAtRest();
    else if (onScreen) {canvas.dataset.state = 'assembling'; frame = requestAnimationFrame(tick);}
    else draw(0);
  }

  function scheduleLayout() {
    if (!paintTimer) paintTimer = requestAnimationFrame(layout);
  }
  const resize = new ResizeObserver(scheduleLayout); resize.observe(title);
  const theme = new MutationObserver(scheduleLayout);
  theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  const intersection = new IntersectionObserver(entries => {
    onScreen = entries[0].isIntersecting;
    if (!onScreen && started !== null) stopAtRest();
    else if (onScreen && !settled && points.length && !frame) {
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
  return {destroy() {
    destroyed = true; cancelFrame(); cancelAnimationFrame(paintTimer); abort.abort();
    resize.disconnect(); theme.disconnect(); intersection.disconnect();
    title.classList.remove('has-dot-title'); canvas.remove();
  }};
}
