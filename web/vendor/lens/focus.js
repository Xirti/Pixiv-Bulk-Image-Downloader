// One input-modality listener set per document, shared by independently mounted stages.
const focusDocuments = new WeakMap();
const focusModifierKeys = new Set(['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'AltGraph', 'Process', 'Unidentified', 'Dead']);

export function bindKeyboardFocus(root = document.documentElement) {
  const element = root.nodeType === 9 ? root.documentElement : root;
  const doc = element?.ownerDocument;
  if (!doc?.addEventListener || !element?.setAttribute) throw new TypeError('A document or element is required');
  let record = focusDocuments.get(doc);
  if (!record) {
    const abort = new AbortController();
    record = {mode: 'pointer', roots: new Map(), abort};
    focusDocuments.set(doc, record);
    const setMode = mode => {
      if (record.mode === mode) return;
      record.mode = mode;
      for (const scope of record.roots.keys()) scope.setAttribute('data-lg-input', mode);
    };
    const hide = () => setMode('pointer');
    const options = {capture: true, passive: true, signal: abort.signal};
    doc.addEventListener('keydown', event => {
      if (!event.key || focusModifierKeys.has(event.key) || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      // A held key must not relight a ring after a pointer has taken over.
      if (event.repeat && record.mode !== 'keyboard') return;
      setMode('keyboard');
    }, options);
    for (const event of ['pointermove', 'pointerdown', 'wheel', 'touchstart', 'touchmove']) {
      doc.addEventListener(event, hide, options);
    }
    // Do not capture element blur: moving between controls is still keyboard input.
    doc.defaultView?.addEventListener('blur', hide, {...options, capture: false});
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) hide(); }, options);
  }
  let entry = record.roots.get(element);
  if (!entry) {
    entry = {count: 0, previous: element.getAttribute('data-lg-input')};
    record.roots.set(element, entry);
  }
  entry.count++;
  element.setAttribute('data-lg-input', record.mode);
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    if (--entry.count === 0) {
      if (entry.previous === null) element.removeAttribute('data-lg-input');
      else element.setAttribute('data-lg-input', entry.previous);
      record.roots.delete(element);
    }
    if (!record.roots.size) { record.abort.abort(); focusDocuments.delete(doc); }
  };
}
