import {Spring, clamp, jellyMatrix} from './jelly-physics.js';

/** The original lens spring solver, driven by a drop target instead of free drag. */
export class OpticalDragMotion {
  constructor({intensity = .7, reduced = false} = {}) {
    this.intensity = intensity; this.reduced = reduced; this.held = false; this.angle = 0;
    for (const key of ['x', 'y', 'press', 'stretch', 'bendX', 'bendY']) this[key] = new Spring();
    this.optical = {bend: [0, 0], pressure: 0, energy: 0};
  }

  target(x, y, {snap = false} = {}) {
    this.x.target = x; this.y.target = y;
    if (snap) {
      this.x.snap(); this.y.snap(); this.press.snap(this.held ? 1 : 0);
      this.stretch.snap(0); this.bendX.snap(0); this.bendY.snap(0);
    }
  }

  hold(held) { this.held = held; this.press.target = held ? 1 : 0; }

  step(dt) {
    const amount = this.intensity * (this.reduced ? .65 : 1);
    const damping = this.reduced ? 34 : this.held ? 22 : 17;
    this.x.step(dt, 260, damping); this.y.step(dt, 260, damping);
    this.press.step(dt, 310, this.reduced ? 32 : 18);
    const speed = Math.hypot(this.x.velocity, this.y.velocity);
    if (speed > 16) this.angle = Math.atan2(this.y.velocity, this.x.velocity);
    this.stretch.target = Math.min(speed / 2400, .25) * amount;
    this.stretch.step(dt, 230, this.reduced ? 30 : 15);
    this.bendX.target = clamp((this.x.target - this.x.value) * .12, -11, 11) * amount;
    this.bendY.target = clamp((this.y.target - this.y.value) * .12, -11, 11) * amount;
    this.bendX.step(dt, 190, this.reduced ? 28 : 14);
    this.bendY.step(dt, 190, this.reduced ? 28 : 14);
    const pressure = clamp(this.press.value, 0, 1.16);
    this.optical.bend[0] = this.bendX.value; this.optical.bend[1] = this.bendY.value;
    this.optical.pressure = pressure;
    this.optical.energy = Math.abs(this.stretch.value) + Math.hypot(this.bendX.value, this.bendY.value) * .015;
    return {x: this.x.value, y: this.y.value, matrix: jellyMatrix(this.stretch.value, this.angle, 1 + pressure * amount * .035)};
  }

  get moving() { return ['x', 'y', 'press', 'stretch', 'bendX', 'bendY'].some(key => this[key].moving); }
}
