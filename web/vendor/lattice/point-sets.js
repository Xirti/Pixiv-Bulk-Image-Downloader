// Lattice point sets and seeded RNG, extracted without algorithm changes.
// Copyright (c) 2026 DayDreamInAReverie. MIT; see LICENSE and UPSTREAM.md.
function rng(seed = 1) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function normalise(arr, radius) {
  let cx = 0, cy = 0, cz = 0; const n = arr.length / 3;
  for (let i = 0; i < n; i++) { cx += arr[i * 3]; cy += arr[i * 3 + 1]; cz += arr[i * 3 + 2]; }
  cx /= n; cy /= n; cz /= n;
  let m = 0;
  for (let i = 0; i < n; i++) { arr[i * 3] -= cx; arr[i * 3 + 1] -= cy; arr[i * 3 + 2] -= cz; m = Math.max(m, Math.hypot(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2])); }
  for (let i = 0; i < arr.length; i++) arr[i] *= radius / m;
  return arr;
}

/** Lorenz attractor, σ = 10, ρ = 28, β = 8/3, integrated with RK4 and sampled evenly in time. */
export function lorenz(n, radius = 1) {
  const out = new Float32Array(n * 3);
  let x = 0.1, y = 0, z = 0;
  const s = 10, r = 28, b = 8 / 3, h = 0.004;
  const f = (x, y, z) => [s * (y - x), x * (r - z) - y, x * y - b * z];
  for (let i = 0; i < 3000; i++) step();
  function step() {
    const k1 = f(x, y, z);
    const k2 = f(x + (h / 2) * k1[0], y + (h / 2) * k1[1], z + (h / 2) * k1[2]);
    const k3 = f(x + (h / 2) * k2[0], y + (h / 2) * k2[1], z + (h / 2) * k2[2]);
    const k4 = f(x + h * k3[0], y + h * k3[1], z + h * k3[2]);
    x += (h / 6) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]);
    y += (h / 6) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]);
    z += (h / 6) * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]);
  }
  for (let i = 0; i < n; i++) { step(); step(); out.set([x, z - 25, y], i * 3); }
  return normalise(out, radius);
}

/** (p, q) torus knot drawn as a thick tube of points. */
export function torusKnot(n, p = 2, q = 3, radius = 1, seed = 9) {
  const out = new Float32Array(n * 3);
  const rand = rng(seed);
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    const r = 2 + Math.cos(q * t);
    const c = [r * Math.cos(p * t), r * Math.sin(p * t), -Math.sin(q * t)];
    const a = rand() * Math.PI * 2, rr = 0.28 * Math.sqrt(rand());
    out.set([c[0] + Math.cos(a) * rr, c[1] + Math.sin(a) * rr * 0.8, c[2] + Math.sin(a) * rr], i * 3);
  }
  return normalise(out, radius);
}

/** Face-centred cubic crystal: 7³ unit cells, atoms as small clouds. */
export function crystal(n, radius = 1, seed = 4) {
  const rand = rng(seed);
  const sites = [];
  const m = 5;
  for (let a = 0; a <= m; a++) for (let b = 0; b <= m; b++) for (let c = 0; c <= m; c++) {
    sites.push([a, b, c]);
    if (a < m && b < m) sites.push([a + 0.5, b + 0.5, c]);
    if (a < m && c < m) sites.push([a + 0.5, b, c + 0.5]);
    if (b < m && c < m) sites.push([a, b + 0.5, c + 0.5]);
  }
  const out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const s = sites[i % sites.length];
    const rr = 0.07 * Math.cbrt(rand()), th = rand() * 6.283, ph = Math.acos(2 * rand() - 1);
    out.set([s[0] + rr * Math.sin(ph) * Math.cos(th), s[1] + rr * Math.sin(ph) * Math.sin(th), s[2] + rr * Math.cos(ph)], i * 3);
  }
  return normalise(out, radius);
}

/** Figure-8 immersion of the Klein bottle. */
export function klein(n, radius = 1, seed = 2) {
  const rand = rng(seed);
  const out = new Float32Array(n * 3);
  const R = 2.2;
  for (let i = 0; i < n; i++) {
    const u = rand() * Math.PI * 2, v = rand() * Math.PI * 2;
    const c = Math.cos(u / 2), s = Math.sin(u / 2);
    const w = R + c * Math.sin(v) - s * Math.sin(2 * v);
    out.set([w * Math.cos(u), w * Math.sin(u), s * Math.sin(v) + c * Math.sin(2 * v)], i * 3);
  }
  return normalise(out, radius);
}

/** Fibonacci sphere: the most even set of n points on S². */
export function sphere(n, radius = 1) {
  const out = new Float32Array(n * 3);
  const g = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), t = g * i;
    out.set([Math.cos(t) * r * radius, y * radius, Math.sin(t) * r * radius], i * 3);
  }
  return out;
}
