/* A small Lattice point field shared by the page backdrop and Lens artwork. */
import {lorenz} from './vendor/lattice/lorenz.js';

export function mountLatticeBackdrop(home) {
  const points = lorenz(760);
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
  let last = 0, painted = 0, phase = .35, pointerX = 0, pointerY = 0, targetX = 0, targetY = 0;
  let width = 0, height = 0, dpr = 1;

  function paint(g, size, stage) {
    if (contrast.matches) return;
    const world = home.getBoundingClientRect(), region = stage.getBoundingClientRect();
    const dark = document.documentElement.dataset.theme === 'dark';
    g.save(); g.scale(size.dpr, size.dpr);
    g.translate(world.left - region.left, world.top - region.top);
    const scale = Math.min(world.width * .33, world.height * .53);
    const cx = world.width * .77 + pointerX, cy = world.height * .5 + pointerY;
    const glow = g.createRadialGradient(cx, cy, 0, cx, cy, scale * 1.7);
    glow.addColorStop(0, dark ? '#70bff81b' : '#6aa9e213');
    glow.addColorStop(.55, dark ? '#a99ce50b' : '#ab9cdb0a');
    glow.addColorStop(1, '#8ecbfa00');
    g.fillStyle = glow; g.fillRect(0, 0, world.width, world.height);
    const c = Math.cos(phase), s = Math.sin(phase);
    for (let i = 0; i < points.length; i += 3) {
      const x = points[i] * c + points[i + 2] * s;
      const depth = points[i + 2] * c - points[i] * s;
      const px = cx + x * scale, py = cy + points[i + 1] * scale * .86;
      g.globalAlpha = .46 + (depth + 1) * .2;
      g.fillStyle = dark ? '#83c5f057' : '#386f9e40';
      g.beginPath(); g.arc(px, py, 1.05 + (depth + 1) * .28, 0, Math.PI * 2); g.fill();
    }
    g.restore();
  }
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    paint(ctx, {dpr}, home);
  }
  function stop() {cancelAnimationFrame(frame); frame = 0; last = 0;}
  function tick(now) {
    frame = 0;
    if (!active || !onScreen || document.hidden || reduced.matches || contrast.matches || destroyed) return;
    const dt = last ? Math.min((now - last) / 1000, .05) : 0;
    last = now; phase += dt * .055;
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
    width = Math.max(1, rect.width); height = Math.max(1, rect.height);
    dpr = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(2_500_000 / (width * height)));
    canvas.width = Math.ceil(width * dpr); canvas.height = Math.ceil(height * dpr);
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
