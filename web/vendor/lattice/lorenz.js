// Extracted without algorithm changes from Lattice src/gl/sets.js.
// Copyright (c) 2026 DayDreamInAReverie. MIT; see LICENSE and UPSTREAM.md.
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

