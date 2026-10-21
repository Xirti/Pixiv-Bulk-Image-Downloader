/* A small Lattice point field shared by the page backdrop and Lens artwork. */
import {torusKnot, crystal, klein, sphere} from './vendor/lattice/point-sets.js';

export function mountLatticeBackdrop(home, {onFrame = () => {}} = {}) {
  const formations = [torusKnot(1600), crystal(1600), klein(1600), sphere(1600)];
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return {paint() {}, setActive() {}, destroy() {}};
  canvas.className = 'lab-ambient';
  canvas.setAttribute('aria-hidden', 'true');
  home.prepend(canvas);
  const abort = new AbortController(), {signal} = abort;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  let active = true, onScreen = false, destroyed = false, frame = 0;
  let last = 0, painted = 0, phase = .35, shapeTime = 0, pointerX = 0, pointerY = 0, targetX = 0, targetY = 0;
  let width = 0, height = 0, dpr = 1, grid = null;

  function paintField(g) {
    if (contrast.matches) return;
    const world = home.getBoundingClientRect();
    const dark = document.documentElement.dataset.theme === 'dark';
    g.save();
    g.fillStyle = dark ? '#0b111b' : '#f3f7fb';
    g.fillRect(0, 0, width, height);
    const wash = g.createRadialGradient(width * .18, height * .12, 0, width * .18, height * .12, Math.max(width, height));
    wash.addColorStop(0, dark ? '#173041' : '#e2f1fc'); wash.addColorStop(1, dark ? '#0b111b' : '#f3f7fb');
    g.fillStyle = wash; g.fillRect(0, 0, width, height);
    g.fillStyle = dark ? '#b7d9ef38' : '#446a8b30';
    if (grid) g.fill(grid);
    const scale = Math.min(world.width * .32, world.height * .48);
    const cx = world.width * .67 + pointerX, cy = world.height * .57 + pointerY;
    const glow = g.createRadialGradient(cx, cy, 0, cx, cy, scale * 1.6);
    glow.addColorStop(0, dark ? '#527eaf24' : '#9bbfdf24');
    glow.addColorStop(1, '#8ecbfa00');
    g.fillStyle = glow; g.fillRect(0, 0, world.width, world.height);
    const cycle = shapeTime / 10, index = Math.floor(cycle) % formations.length;
    const from = formations[index], to = formations[(index + 1) % formations.length];
    const progress = Math.max(0, (cycle % 1 - .65) / .35);
    const mix = progress * progress * (3 - 2 * progress);
    const c = Math.cos(phase), s = Math.sin(phase);
    const colors = dark ? ['#7acfe7','#a4adee','#dfbba2'] : ['#37869e','#7e70b0','#ae7950'];
    for (let i = 0; i < from.length; i += 3) {
      const x0 = from[i] + (to[i] - from[i]) * mix;
      const y0 = from[i+1] + (to[i+1] - from[i+1]) * mix;
      const z0 = from[i+2] + (to[i+2] - from[i+2]) * mix;
      const x = x0 * c + z0 * s, depth = z0 * c - x0 * s;
      const perspective = 1 / (1.2 - depth * .16);
      const px = cx + x * scale * perspective, py = cy + y0 * scale * perspective;
      g.globalAlpha = (dark ? .40 : .32) + (depth + 1) * .22;
      g.fillStyle = colors[Math.max(0, Math.min(2, Math.floor((y0 + 1) * 1.5)))];
      g.beginPath(); g.arc(px, py, 1.15 + (depth + 1) * .35, 0, Math.PI * 2); g.fill();
    }
    g.restore();
  }
  // Lens samples this very canvas after each paint; no separately baked background.
  function paint(g, size, stage) {
    const world = home.getBoundingClientRect(), region = stage.getBoundingClientRect();
    g.save(); g.scale(size.dpr, size.dpr);
    g.fillStyle = document.documentElement.dataset.theme === 'dark' ? '#0b111b' : '#f3f7fb';
    g.fillRect(0, 0, size.width / size.dpr, size.height / size.dpr);
    g.drawImage(canvas, world.left - region.left, world.top - region.top, width, height);
    g.restore();
  }
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    paintField(ctx);
    onFrame();
  }
  function stop() {cancelAnimationFrame(frame); frame = 0; last = 0;}
  function tick(now) {
    frame = 0;
    if (!active || !onScreen || document.hidden || reduced.matches || contrast.matches || destroyed) return;
    const dt = last ? Math.min((now - last) / 1000, .05) : 0;
    last = now; phase += dt * .14; shapeTime += dt;
    pointerX += (targetX - pointerX) * (1 - Math.exp(-dt * 4));
    pointerY += (targetY - pointerY) * (1 - Math.exp(-dt * 4));
    if (now - painted >= 40) {draw(); painted = now;}
    frame = requestAnimationFrame(tick);
  }
  function sync() {
    stop();
    if (destroyed) return;
    draw();
    if (active && onScreen && !document.hidden && !reduced.matches && !contrast.matches) frame = requestAnimationFrame(tick);
  }
  function resize() {
    if (destroyed) return;
    const rect = home.getBoundingClientRect();
    const nextWidth = Math.max(1, rect.width), nextHeight = Math.max(1, rect.height);
    const nextDpr = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(2_500_000 / (nextWidth * nextHeight)));
    if (nextWidth === width && nextHeight === height && nextDpr === dpr) return;
    width = nextWidth; height = nextHeight; dpr = nextDpr;
    canvas.width = Math.ceil(width * dpr); canvas.height = Math.ceil(height * dpr);
    grid = new Path2D();
    for (let x = 12; x < width; x += 24) for (let y = 12; y < height; y += 24) {
      grid.moveTo(x + .7, y); grid.arc(x, y, .7, 0, Math.PI * 2);
    }
    sync();
  }
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(home);
  const intersection = new IntersectionObserver(entries => {onScreen = entries[0].isIntersecting; sync();});
  intersection.observe(home);
  const theme = new MutationObserver(sync);
  theme.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  home.addEventListener('pointermove', event => {
    const r = home.getBoundingClientRect();
    targetX = ((event.clientX - r.left) / Math.max(r.width, 1) - .5) * 18;
    targetY = ((event.clientY - r.top) / Math.max(r.height, 1) - .5) * 10;
  }, {signal, passive: true});
  home.addEventListener('pointerleave', () => {targetX = 0; targetY = 0;}, {signal});
  document.addEventListener('visibilitychange', sync, {signal});
  window.addEventListener('pagehide', stop, {signal});
  window.addEventListener('pageshow', sync, {signal});
  reduced.addEventListener('change', sync, {signal});
  contrast.addEventListener('change', sync, {signal});
  resize();
  return {
    paint, setActive(value) {if (active !== value) {active = value; sync();}},
    destroy() {destroyed = true; stop(); abort.abort(); resizeObserver.disconnect(); intersection.disconnect(); theme.disconnect(); canvas.remove();},
  };
}
