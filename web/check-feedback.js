/* Checkbox feedback decorates the committed native state; it never changes selection. */
export function mountCheckboxFeedback(doc = document) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const abort = new AbortController();
  const {signal} = abort;
  const active = new Map();

  function clear() {
    for (const [element, animation] of active) {
      animation.cancel();
      delete element.dataset.checkFeedback;
    }
    active.clear();
  }

  doc.addEventListener('change', event => {
    const box = event.target;
    if (!box?.matches?.('input[type=checkbox]') || box.disabled || !box.isConnected) return;
    // The basket's native input is transparent; animate its existing visible check.
    const element = box.closest('.batch-card-select')?.querySelector('span') || box;
    if (!element.isConnected || typeof element.animate !== 'function') return;
    active.get(element)?.cancel();
    element.dataset.checkFeedback = box.indeterminate ? 'mixed' : box.checked ? 'check' : 'uncheck';
    const frames = reduced.matches
      ? [{opacity: .45}, {opacity: 1}]
      : [{transform: 'scale(.82)'}, {transform: 'scale(1.14)', offset: .45},
         {transform: 'scale(.98)', offset: .75}, {transform: 'scale(1)'}];
    const animation = element.animate(frames, {
      duration: reduced.matches ? 160 : 420,
      easing: 'cubic-bezier(.18,.7,.24,1)',
    });
    active.set(element, animation);
    animation.finished.catch(() => {}).finally(() => {
      if (active.get(element) !== animation) return;
      active.delete(element);
      delete element.dataset.checkFeedback;
    });
  }, {signal});
  doc.addEventListener('visibilitychange', () => {if (doc.hidden) clear();}, {signal});
  window.addEventListener('pagehide', clear, {signal});
  reduced.addEventListener('change', clear, {signal});
  return {destroy() {clear(); abort.abort();}};
}
