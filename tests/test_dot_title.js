/* Lifecycle checks: a one-shot title must stop, and preserve native text when motion is reduced. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const physics = fs.readFileSync(path.join(__dirname, '../web/vendor/lattice/dots.js'),'utf8').replace('export class Dots','class Dots');
const source = fs.readFileSync(path.join(__dirname, '../web/dot-title.js'), 'utf8').replace(/^import .*;\r?\n/gm,'').replace('export function mountDotTitle', 'function mountDotTitle');

function setup({reduced = false, contrast = false, canvasAvailable = true} = {}) {
  const frames = new Map(), canvases = [], observers = {intersection: [], resize: [], mutation: []};
  let nextId = 0, time = 0, draws = 0;
  class Classes {
    constructor() {this.values = new Set();}
    add(value) {this.values.add(value);}
    remove(value) {this.values.delete(value);}
    contains(value) {return this.values.has(value);}
  }
  class Canvas extends EventTarget {
    constructor() {super(); this.dataset = {}; this.classList = new Classes(); this.removed = false;}
    setAttribute() {}
    remove() {this.removed = true;}
    getContext() {
      if (!canvasAvailable) return null;
      return {setTransform() {}, scale() {}, clearRect: () => {draws++;this.points=[];}, beginPath() {}, arc: (x,y) => {this.points.push([x,y]);}, fill() {}, fillText() {},
        measureText(text) {return {width: text.length * 36, fontBoundingBoxAscent: 39, fontBoundingBoxDescent: 9};},
        getImageData: () => ({data: new Uint8ClampedArray(this.width * this.height * 4).fill(255)})};
    }
  }
  const title = Object.assign(new EventTarget(), {classList: new Classes(), append(canvas) {this.canvas = canvas;},
    getBoundingClientRect: () => ({left: 0, top: 0, width: 260, height: 60}),
    querySelectorAll: () => [
      {textContent: '想找点什么', classList: {contains: () => false}, getBoundingClientRect: () => ({left: 0, top: 0, width: 220, height: 60})},
      {textContent: '？', classList: {contains: () => true}, getBoundingClientRect: () => ({left: 220, top: 0, width: 40, height: 60})},
    ]});
  const doc = new EventTarget(); doc.hidden = false; doc.documentElement = {}; doc.fonts = {ready: Promise.resolve()};
  doc.createElement = () => {const canvas = new Canvas(); canvases.push(canvas); return canvas;};
  const media = new Map();
  for (const [query, matches] of [['(prefers-reduced-motion: reduce)', reduced], ['(forced-colors: active)', contrast]]) {
    const target = new EventTarget(); target.matches = matches; media.set(query, target);
  }
  const observer = kind => class {
    constructor(callback) {this.callback = callback; observers[kind].push(this);}
    observe() {}
    disconnect() {this.disconnected = true;}
  };
  const context = vm.createContext({document: doc, window: new EventTarget(), AbortController,
    matchMedia: query => media.get(query), ResizeObserver: observer('resize'), MutationObserver: observer('mutation'), IntersectionObserver: observer('intersection'),
    getComputedStyle: () => ({fontSize: '46px', fontWeight: '600', fontFamily: 'sans-serif', letterSpacing: '0px', getPropertyValue: () => '#ddeeff'}),
    devicePixelRatio: 1, requestAnimationFrame: cb => {frames.set(++nextId, cb); return nextId;}, cancelAnimationFrame: id => frames.delete(id),
    Uint8ClampedArray, Math});
  vm.runInContext(physics + '\n' + source + '\nthis.mount = mountDotTitle;', context);
  const api = context.mount(title);
  function advance(ms = 17) {
    time += ms;
    const ready = [...frames.values()]; frames.clear(); ready.forEach(callback => callback(time));
  }
  function show() {observers.intersection[0]?.callback([{isIntersecting: true}]); advance();}
  return {title, api, frames, doc, media, observers, advance, show, get draws() {return draws;}};
}

(async () => {
  const normal = setup(); normal.show(); await Promise.resolve();
  for (let index = 0; index < 100; index++) normal.advance();
  assert.equal(normal.title.canvas.dataset.state, 'settled');
  assert.equal(normal.frames.size, 0, 'settled title must not retain an animation loop');
  const rendered = normal.draws;
  for (let index = 0; index < 20; index++) normal.advance();
  assert.equal(normal.draws, rendered, 'idle title should perform no new drawing');

  function pointer(type,x=100,y=30) {const e=new Event(type);Object.assign(e,{clientX:x,clientY:y,button:0,pointerType:'mouse'});normal.title.dispatchEvent(e);}
  const resting=JSON.stringify(normal.title.canvas.points);
  pointer('pointermove');normal.advance();
  assert.notEqual(JSON.stringify(normal.title.canvas.points),resting,'upstream pointer field must displace the actual glyph dots');
  assert.equal(normal.title.canvas.dataset.state,'interactive','pointer must wake the settled title');
  assert.ok(normal.frames.size>0);
  pointer('pointerdown');normal.advance();
  pointer('pointerleave');for(let i=0;i<240;i++)normal.advance();
  assert.equal(normal.frames.size,0,'spring return must stop after pointer leaves');

  const preferStatic = normal.media.get('(prefers-reduced-motion: reduce)');
  preferStatic.matches = true; preferStatic.dispatchEvent(new Event('change')); normal.advance();
  assert.equal(normal.title.canvas.hidden, true, 'native fallback must hide already-rendered dots');
  assert.equal(normal.title.classList.contains('has-dot-title'), false);
  preferStatic.matches = false; preferStatic.dispatchEvent(new Event('change')); normal.advance();
  assert.equal(normal.title.canvas.hidden, false);
  assert.equal(normal.frames.size, 0, 'motion preference changes must not replay the intro');

  normal.observers.mutation[0].callback(); normal.advance();
  assert.equal(normal.title.canvas.dataset.state, 'settled', 'theme change does not restart introduction');
  assert.equal(normal.frames.size, 0);
  normal.api.destroy();
  assert.equal(normal.title.classList.contains('has-dot-title'), false);
  assert.equal(normal.title.canvas.removed, true);
  assert.ok(Object.values(normal.observers).flat().every(observer => observer.disconnected));

  const hidden = setup(); hidden.show(); hidden.advance(100);
  hidden.doc.hidden = true; hidden.doc.dispatchEvent(new Event('visibilitychange'));
  assert.equal(hidden.frames.size, 0); assert.equal(hidden.title.canvas.dataset.state, 'settled'); hidden.api.destroy();

  const lessMotion = setup({reduced: true}); lessMotion.show();
  assert.equal(lessMotion.title.canvas.dataset.state, 'static-text');
  assert.equal(lessMotion.title.classList.contains('has-dot-title'), false);
  assert.equal(lessMotion.frames.size, 0); lessMotion.api.destroy();

  const highContrast = setup({contrast: true}); highContrast.show();
  assert.equal(highContrast.title.classList.contains('has-dot-title'), false); highContrast.api.destroy();

  const teardown = setup(); teardown.api.destroy(); await Promise.resolve(); teardown.advance();
  assert.equal(teardown.frames.size, 0, 'font readiness after destroy must not recreate rendering');

  const noCanvas = setup({canvasAvailable: false});
  assert.equal(noCanvas.title.classList.contains('has-dot-title'), false); noCanvas.api.destroy();
  console.log('Dot title: intro, pointer repulsion, click burst, spring return, idle, theme, visibility and cleanup passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
