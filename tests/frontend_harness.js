// Minimal DOM for executing the application state transitions without a network.
class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  toggle(name, enabled) { enabled ? this.values.add(name) : this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}
class FakeElement {
  constructor() {
    this.classList = new FakeClassList();
    this.dataset = {};
    this.children = [];
    this.options = [];
    this.selectedOptions = [];
    this.queryCache = new Map();
    this.listeners = new Map();
    this._innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.open = false;
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = String(value); this.queryCache.clear(); }
  addEventListener(type, handler) { this.listeners.set(type, handler); }
  removeEventListener() {}
  appendChild(child) { return child; }
  querySelectorAll(selector) {
    if (this.queryCache.has(selector)) return this.queryCache.get(selector);
    const nodes = [];
    if (selector === "[data-collection-page]") {
      const inputs = /<input\b([^>]*\bdata-collection-page="([^"]+)"[^>]*)>/g;
      let inputMatch;
      while ((inputMatch = inputs.exec(this._innerHTML))) {
        const node = new FakeElement();
        node.dataset.collectionPage = inputMatch[2];
        node.checked = /\bchecked\b/.test(inputMatch[1]);
        node.disabled = /\bdisabled\b/.test(inputMatch[1]);
        nodes.push(node);
      }
      this.queryCache.set(selector, nodes);
      return nodes;
    }
    const patterns = {
      "[data-viewer-window]:not([disabled])": {
        regex: /<button\b([^>]*\bdata-viewer-window="([^"]+)"[^>]*)>/g,
        key: "viewerWindow",
      },
      "[data-open-collection]": {
        regex: /<button\b([^>]*\bdata-open-collection="([^"]+)"[^>]*)>/g,
        key: "openCollection",
      },
    };
    const definition = patterns[selector];
    if (!definition) return nodes;
    const pattern = definition.regex;
    let match;
    while ((match = pattern.exec(this._innerHTML))) {
      if (/\bdisabled\b/.test(match[1])) continue;
      const node = new FakeElement();
      node.dataset[definition.key] = match[2];
      nodes.push(node);
    }
    this.queryCache.set(selector, nodes);
    return nodes;
  }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, bottom: 1 }; }
  setAttribute(name, value) { this[name] = String(value); }
  getAttribute(name) { return this[name]; }
  removeAttribute(name) { delete this[name]; }
  setCustomValidity(message) { this.validationMessage = message; }
  closest() { return null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const fakeElements = new Map();
const detailImages = [];
const documentListeners = new Map();
const windowListeners = new Map();
const fakeElement = (selector) => {
  if (!fakeElements.has(selector)) fakeElements.set(selector, new FakeElement());
  return fakeElements.get(selector);
};
globalThis.document = {
  documentElement: new FakeElement(),
  body: new FakeElement(),
  querySelector: fakeElement,
  querySelectorAll: (selector) => selector === "[data-detail-artwork]" ? detailImages : [],
  addEventListener: (type, handler) => documentListeners.set(type, handler),
};
globalThis.window = globalThis;
globalThis.matchMedia = () => ({matches: false});
globalThis.addEventListener = (type, handler) => windowListeners.set(type, handler);
globalThis.requestIdleCallback = () => {};
fakeElement("#safety").value = "safe";
fakeElement("#workType").value = "all";
fakeElement("#quality").value = "regular";
fakeElement("#format").value = "source";
fakeElement("#tag").value = "old";
for (const id of ["allViewer", "basketPage", "downloadPage", "historyPage", "favoritesPage"]) fakeElement(`#${id}`).hidden = true;
