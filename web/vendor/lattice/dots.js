// Lattice Dotkit Dots, unchanged class extracted from src/dom/dotkit.js.
// Copyright (c) 2026 DayDreamInAReverie. MIT; see LICENSE and UPSTREAM.md.
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
export class Dots {
  constructor(n) {
    this.n = n;
    for (const k of ["x", "y", "vx", "vy", "tx", "ty", "a", "ta", "s", "heat", "seed"]) this[k] = new Float32Array(n);
    for (let i = 0; i < n; i++) { this.seed[i] = Math.random(); this.s[i] = 1; }
    this.k = 180;       // spring
    this.damp = 14;     // damping
    this.flow = 0;      // noise strength
    this.delay = new Float32Array(n); // per-dot release delay (seconds)
    this.free = new Uint8Array(n);    // 1 = gas: ignores its home and wanders inside `box`
    this.box = { x0: 0, y0: 0, x1: 100, y1: 20 };
    this.wander = 60;                 // gas agitation (px/s²)
    this.clock = 0;
  }
  home(i, x, y, a = 1) { this.tx[i] = x; this.ty[i] = y; this.ta[i] = a; }
  place(i, x, y) { this.x[i] = x; this.y[i] = y; this.vx[i] = this.vy[i] = 0; }
  snap() { for (let i = 0; i < this.n; i++) { this.x[i] = this.tx[i]; this.y[i] = this.ty[i]; this.a[i] = this.ta[i]; this.vx[i] = this.vy[i] = 0; } }
  /** Stagger the next move: dots leave one by one, left to right (or by any key). */
  stagger(total = 0.25, key = (i) => this.tx[i]) {
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < this.n; i++) { const v = key(i); lo = Math.min(lo, v); hi = Math.max(hi, v); }
    for (let i = 0; i < this.n; i++) this.delay[i] = ((key(i) - lo) / Math.max(1e-6, hi - lo)) * total * 0.7 + this.seed[i] * total * 0.3;
  }
  burst(cx, cy, power = 260) {
    for (let i = 0; i < this.n; i++) {
      const dx = this.x[i] - cx, dy = this.y[i] - cy, d = Math.hypot(dx, dy) + 1;
      this.vx[i] += (dx / d) * power * (0.6 + this.seed[i] * 0.8);
      this.vy[i] += (dy / d) * power * (0.6 + this.seed[i] * 0.8);
      this.heat[i] = 1;
    }
  }
  attract(px, py, r, strength) { this._att = { px, py, r, strength }; }
  step(dt) {
    if (reducedMotion()) { this.snap(); return false; }
    this.clock += dt;
    const { k, damp, flow } = this;
    const att = this._att;
    let moving = false;
    const t = this.clock;
    for (let i = 0; i < this.n; i++) {
      if (this.delay[i] > 0) { this.delay[i] -= dt; if (!this.free[i]) continue; }
      if (this.free[i]) { // gas: a random walk that bounces off the walls
        const b = this.box, w = this.wander;
        this.vx[i] += (Math.random() - 0.5) * w * 2 * dt * 10;
        this.vy[i] += (Math.random() - 0.5) * w * 2 * dt * 10;
        const sp = Math.hypot(this.vx[i], this.vy[i]), max = w * 0.35;
        if (sp > max) { this.vx[i] *= max / sp; this.vy[i] *= max / sp; }
        this.x[i] += this.vx[i] * dt; this.y[i] += this.vy[i] * dt;
        if (this.x[i] < b.x0) { this.x[i] = b.x0; this.vx[i] = Math.abs(this.vx[i]); }
        if (this.x[i] > b.x1) { this.x[i] = b.x1; this.vx[i] = -Math.abs(this.vx[i]); }
        if (this.y[i] < b.y0) { this.y[i] = b.y0; this.vy[i] = Math.abs(this.vy[i]); }
        if (this.y[i] > b.y1) { this.y[i] = b.y1; this.vy[i] = -Math.abs(this.vy[i]); }
        this.a[i] += (this.ta[i] - this.a[i]) * Math.min(1, dt * 8);
        moving = true;
        continue;
      }
      let ax = (this.tx[i] - this.x[i]) * k, ay = (this.ty[i] - this.y[i]) * k;
      if (flow) { const s = this.seed[i] * 40; ax += Math.sin(t * 1.3 + s + this.y[i] * 0.05) * flow; ay += Math.cos(t * 1.1 + s + this.x[i] * 0.05) * flow; }
      if (att) {
        const dx = att.px - this.x[i], dy = att.py - this.y[i], d2 = dx * dx + dy * dy;
        if (d2 < att.r * att.r) { const f = (1 - Math.sqrt(d2) / att.r) * att.strength; ax += dx * f; ay += dy * f; }
      }
      this.vx[i] = (this.vx[i] + ax * dt) * Math.exp(-damp * dt);
      this.vy[i] = (this.vy[i] + ay * dt) * Math.exp(-damp * dt);
      this.x[i] += this.vx[i] * dt;
      this.y[i] += this.vy[i] * dt;
      this.a[i] += (this.ta[i] - this.a[i]) * Math.min(1, dt * 8);
      this.heat[i] = Math.max(0, this.heat[i] - dt * 1.6);
      if (Math.abs(this.vx[i]) + Math.abs(this.vy[i]) > 2 || Math.abs(this.tx[i] - this.x[i]) + Math.abs(this.ty[i] - this.y[i]) > 0.3) moving = true;
    }
    this._att = null;
    return moving || flow > 0;
  }
  draw(g, { ink, hot, r = 1.4, hotOf = () => 0 }) {
    for (let i = 0; i < this.n; i++) {
      const a = this.a[i];
      if (a < 0.02) continue;
      const h = Math.max(this.heat[i], hotOf(i));
      g.globalAlpha = Math.min(1, a);
      g.fillStyle = h > 0.5 ? hot : ink;
      const rr = r * this.s[i] * (1 + h * 0.4);
      g.beginPath();
      g.arc(this.x[i], this.y[i], rr, 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
  }
}

