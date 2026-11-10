"use strict";

// Reveals belong to the list that displays them, and expire after idle time.
globalThis.createSensitiveImageReveals = function ({root, onHide}) {
  const entries = new Map();
  const idleTime = 10000;

  function schedule(id, entry, delay = idleTime) {
    entry.timer = setTimeout(() => {
      if (entries.get(id) !== entry) return;
      const card = root.querySelector(`[data-sensitive-image="${id}"]`);
      const hovered = card && !document.hidden && !card.closest("[hidden],[inert]") && card.matches(":hover");
      const remaining = idleTime - (Date.now() - entry.touched);
      if (hovered || remaining > 0) {
        schedule(id, entry, hovered ? idleTime : remaining);
        return;
      }
      entries.delete(id);
      onHide(id);
    }, delay);
  }

  function touch(event) {
    const card = event.target.closest("[data-sensitive-image]");
    const entry = card && entries.get(card.dataset.sensitiveImage);
    if (entry) entry.touched = Date.now();
  }
  for (const name of ["pointerover", "pointerout", "pointerdown", "keydown", "focusin", "focusout", "wheel"]) {
    root.addEventListener(name, touch, {capture: true, passive: true});
  }

  function clear() {
    entries.forEach(entry => clearTimeout(entry.timer));
    entries.clear();
  }
  return {
    has: id => entries.has(String(id)),
    ids: () => [...entries.keys()],
    reveal(id) {
      id = String(id);
      const previous = entries.get(id);
      if (previous) clearTimeout(previous.timer);
      const entry = {touched: Date.now(), timer: null};
      entries.set(id, entry);
      schedule(id, entry);
    },
    clear,
  };
};
