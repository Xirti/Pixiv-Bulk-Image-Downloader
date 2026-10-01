const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const themeSource = fs.readFileSync('web/theme.js', 'utf8');

function page(storage, systemDark) {
  const root = { dataset: {} };
  const button = { setAttribute(name, value) { this[name] = value; } };
  const context = {
    document: { documentElement: root, readyState: 'complete', getElementById: () => button },
    localStorage: storage,
    matchMedia: () => ({matches: systemDark}),
  };
  vm.runInNewContext(themeSource, context);
  return {root, button};
}

const blocked = page({getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }}, true);
assert.equal(blocked.root.dataset.theme, 'dark');
blocked.button.onclick();
assert.equal(blocked.root.dataset.theme, 'light');
assert.match(blocked.button['aria-label'], /夜间/);
blocked.button.onclick();
assert.equal(blocked.root.dataset.theme, 'dark');
assert.match(blocked.button['aria-label'], /日间/);

let saved = 'light';
const stored = page({getItem: () => saved, setItem: (_key, value) => { saved = value; }}, true);
assert.equal(stored.root.dataset.theme, 'light');
stored.button.onclick();
assert.equal(saved, 'dark');
assert.equal(page({getItem: () => saved}, false).root.dataset.theme, 'dark');
