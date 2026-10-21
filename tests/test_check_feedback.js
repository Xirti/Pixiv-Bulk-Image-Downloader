const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const handlers = {};
const mediaHandlers = {};
const windowHandlers = {};
function bind(target) {
  return (type, listener, options = {}) => {
    target[type] = listener;
    options.signal?.addEventListener('abort', () => {delete target[type];});
  };
}
const doc = {hidden: false, addEventListener: bind(handlers)};
const reduced = {matches: false, addEventListener: bind(mediaHandlers)};
const source = fs.readFileSync(path.join(__dirname, '../web/check-feedback.js'), 'utf8').replace('export function', 'function');
const context = {document: doc, window: {addEventListener: bind(windowHandlers)}, matchMedia: () => reduced, AbortController};
vm.createContext(context);
vm.runInContext(source + '\nglobalThis.controller = mountCheckboxFeedback(document);', context);
const calls = [];
function element() {
  return {
    dataset: {}, isConnected: true,
    animate(frames, options) {
      let resolve, reject;
      const animation = {frames, options, cancelled: false,
        finished: new Promise((yes, no) => {resolve = yes; reject = no;}),
        finish() {resolve();},
        cancel() {this.cancelled = true; reject(new Error('cancelled'));}};
      calls.push(animation);
      return animation;
    },
  };
}
async function run() {
  const box = Object.assign(element(), {checked: true, disabled: false,
    matches: selector => selector === 'input[type=checkbox]', closest: () => null});
  handlers.change({target: box});
  assert.equal(box.checked, true, 'feedback must not alter the committed state');
  assert.equal(box.dataset.checkFeedback, 'check');
  assert.equal(calls[0].options.duration, 420);
  assert.equal(calls[0].frames[1].transform, 'scale(1.14)');
  box.checked = false;
  handlers.change({target: box});
  assert.equal(calls[0].cancelled, true);
  assert.equal(box.dataset.checkFeedback, 'uncheck');
  await Promise.resolve(); await Promise.resolve();
  assert.equal(box.dataset.checkFeedback, 'uncheck', 'old completion must not erase new feedback');
  calls[1].finish();
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(box.dataset.checkFeedback, undefined);
  const visible = element();
  box.closest = () => ({querySelector: () => visible});
  box.checked = true;
  handlers.change({target: box});
  assert.equal(visible.dataset.checkFeedback, 'check', 'basket animates the visible check, not its hidden input');
  reduced.matches = true;
  mediaHandlers.change();
  assert.equal(calls[2].cancelled, true);
  assert.equal(visible.dataset.checkFeedback, undefined);
  handlers.change({target: box});
  assert.equal(calls[3].options.duration, 160);
  assert.equal(calls[3].frames[0].transform, undefined, 'reduced motion avoids spatial movement');
  doc.hidden = true;
  handlers.visibilitychange();
  assert.equal(calls[3].cancelled, true);
  const before = calls.length;
  box.disabled = true; handlers.change({target: box});
  box.disabled = false; box.isConnected = false; handlers.change({target: box});
  assert.equal(calls.length, before, 'disabled or removed controls must stay quiet');
  context.controller.destroy();
  assert.equal(Object.keys(handlers).length, 0);
  assert.equal(Object.keys(windowHandlers).length, 0);
  assert.equal(Object.keys(mediaHandlers).length, 0);
  console.log('Checkbox feedback: committed state, visible 420ms pulse, rapid toggles, basket, reduced motion and cleanup passed.');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
