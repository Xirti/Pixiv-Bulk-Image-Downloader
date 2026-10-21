import {tokens} from './tokens.js';
import {opticColor, setOpticalState, clearOpticalState} from './internal/optics.js';

let cardListId = 0;
const cardListNode = (tag, className, text) => {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const cardListLabel = text => {
  if (typeof text !== 'string' || !text.trim()) throw new TypeError('Card lists require non-empty labels and IDs');
  return text;
};

export function registerCardList(defineComponent, createComponent) {
  defineComponent('card-list', ({label, items = [], previousLabel = 'Previous cards', nextLabel = 'Next cards', emptyLabel = 'No cards', autoScroll = false, pauseLabel = 'Pause scrolling', playLabel = 'Play scrolling'} = {}) => {
    cardListLabel(label); cardListLabel(previousLabel); cardListLabel(nextLabel); cardListLabel(emptyLabel);
    cardListLabel(pauseLabel); cardListLabel(playLabel);
    if (typeof autoScroll !== 'boolean' && !(typeof autoScroll === 'number' && Number.isFinite(autoScroll) && autoScroll > 0)) throw new TypeError('autoScroll must be a boolean or a positive speed');
    if (!Array.isArray(items)) throw new TypeError('Card list items must be an array');
    const ids = new Set(), nodes = new Set();
    // Validate before moving caller content or assuming ownership of handles.
    const claimNode = value => {
      if (!(value instanceof Node) || nodes.has(value)) throw new TypeError('Each card content node must have a single owner');
      nodes.add(value);
    };
    for (const item of items) {
      if (!item || typeof item !== 'object') throw new TypeError('Invalid card item');
      cardListLabel(item.id); cardListLabel(item.label);
      if (ids.has(item.id)) throw new TypeError('Card IDs must be unique');
      ids.add(item.id);
      if (item.description !== undefined && typeof item.description !== 'string') throw new TypeError('Card description must be text');
      if (item.title !== undefined && typeof item.title !== 'string') throw new TypeError('Card title must be text');
      if (item.icon !== undefined) claimNode(item.icon);
      if (item.children !== undefined && !Array.isArray(item.children)) throw new TypeError('Card children must be an array');
      for (const child of item.children || []) {
        if (typeof child === 'string') continue;
        if (child?.element instanceof HTMLElement && typeof child.destroy === 'function') claimNode(child.element);
        else claimNode(child);
      }
    }

    const abort = new AbortController(), {signal} = abort, owned = [], cards = new Map();
    let destroyed = false, frame = 0, motionFrame = 0, wakeTimer = 0, lastTime = 0, coast = 0, remainder = 0, drag = null, interaction = false;
    let wanted = !!autoScroll, motionOptIn = false, visible = false, hovered = false, focused = false, holdUntil = 0, stride = 0, loopEdge = 0, looping = false;
    const speed = typeof autoScroll === 'number' ? autoScroll : 24;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    if (reduced.matches) wanted = false;
    const element = cardListNode('section', 'lg-card-list');
    element.setAttribute('aria-label', label);
    const viewport = cardListNode('div', 'lg-card-list-viewport');
    viewport.id = `lg-card-list-${++cardListId}`;
    viewport.dataset.lgClip = 'true';
    viewport.setAttribute('role', 'region'); viewport.setAttribute('aria-label', label); viewport.tabIndex = 0;
    const list = cardListNode('ul', 'lg-card-list-items');
    list.setAttribute('role', 'list'); viewport.append(list);

    for (const item of items) {
      const slot = cardListNode('li', 'lg-card-list-item');
      slot.setAttribute('aria-posinset', String(cards.size + 1)); slot.setAttribute('aria-setsize', String(items.length));
      const header = cardListNode('div', 'lg-lens-card-header');
      if (item.icon) {
        const icon = cardListNode('span', 'lg-lens-card-icon');
        icon.setAttribute('aria-hidden', 'true'); icon.append(item.icon); header.append(icon);
      }
      const copy = cardListNode('div', 'lg-lens-card-copy');
      const title = cardListNode('h3', 'lg-lens-card-title', item.title ?? item.label);
      title.hidden = !title.textContent.trim();
      title.id = `${viewport.id}-title-${cards.size}`;
      copy.append(title);
      if (item.description) copy.append(cardListNode('p', 'lg-lens-card-description', item.description));
      header.append(copy);
      header.hidden = !item.icon && title.hidden && !item.description;
      const content = cardListNode('div', 'lg-lens-card-content');
      for (const child of item.children || []) {
        if (child?.element instanceof HTMLElement && typeof child.destroy === 'function') {
          owned.push(child); content.append(child.element);
        } else content.append(typeof child === 'string' ? cardListNode('p', 'lg-lens-card-text', child) : child);
      }
      const card = createComponent('panel', {label:item.label, motion:'press', children:[header, ...(content.childNodes.length ? [content] : [])]});
      owned.push(card); card.element.classList.add('lg-lens-card');
      if (!title.hidden) card.element.setAttribute('aria-labelledby', title.id);
      card.element.dataset.cardId = item.id;
      card.element.style.setProperty('--lg-optical-color', tokens.optics.gold);
      const reflect = () => {
        if (destroyed) return;
        const active = card.element.matches(':hover,:focus-within');
        card.element.dataset.lgReflect = String(active);
        setOpticalState(card.element, {selected:active ? .65 : 0, color:opticColor('gold')});
      };
      card.element.addEventListener('pointerenter', reflect, {signal});
      card.element.addEventListener('pointerleave', reflect, {signal});
      card.element.addEventListener('focusin', reflect, {signal});
      card.element.addEventListener('focusout', () => queueMicrotask(reflect), {signal});
      slot.append(card.element); list.append(slot); cards.set(item.id, card.element);
    }

    const empty = cardListNode('p', 'lg-card-list-empty', emptyLabel); empty.hidden = items.length > 0;
    viewport.hidden = !items.length;
    const navigation = cardListNode('div', 'lg-card-list-navigation');
    const previous = createComponent('button', {label:previousLabel, onPress:() => step(-1)});
    const next = createComponent('button', {label:nextLabel, onPress:() => step(1)});
    owned.push(previous, next);
    for (const button of [previous, next]) button.element.setAttribute('aria-controls', viewport.id);
    navigation.append(previous.element, next.element); element.append(viewport, empty, navigation);
    const playback = createComponent('button', {label:pauseLabel, onPress:() => setPlaying(!wanted)});
    playback.element.classList.add('lg-card-list-playback'); playback.element.setAttribute('aria-controls', viewport.id);
    navigation.prepend(playback.element); owned.push(playback);

    const stopMotion = () => {cancelAnimationFrame(motionFrame); clearTimeout(wakeTimer); wakeTimer = 0; motionFrame = 0; lastTime = 0;};
    const allowsMotion = () => !reduced.matches || motionOptIn;
    const canAuto = () => wanted && looping && allowsMotion() && visible && !document.hidden && !hovered && !focused && !interaction && !drag;
    const wake = () => {
      if (destroyed || motionFrame || wakeTimer || !visible || document.hidden || !allowsMotion() || (!coast && !canAuto())) return;
      const delay = holdUntil - performance.now();
      if (!coast && delay > 0) {wakeTimer = setTimeout(() => {wakeTimer = 0; wake();}, delay); return;}
      motionFrame = requestAnimationFrame(animate);
    };
    function setPlaying(value) {
      if (destroyed) return;
      wanted = value; if (wanted && reduced.matches) motionOptIn = true;
      playback.element.firstChild.nodeValue = wanted ? pauseLabel : playLabel;
      element.dataset.autoScroll = wanted ? 'playing' : 'paused';
      if (!wanted) stopMotion(); else wake();
    }
    // Recycle the live card only after its glass edge has left the clipping region.
    // No duplicate controls, IDs, canvases or component handles are created.
    const moveBy = delta => {
      let left = viewport.scrollLeft + delta + remainder;
      if (looping && !viewport.querySelector(':focus')) {
        while (left >= loopEdge) {list.append(list.firstElementChild); left -= stride;}
        while (left < 0) {list.prepend(list.lastElementChild); left += stride;}
      }
      left = Math.max(0, Math.min(viewport.scrollWidth - viewport.clientWidth, left));
      viewport.scrollLeft = left; remainder = left - viewport.scrollLeft;
    };
    function animate(now) {
      motionFrame = 0;
      const dt = lastTime ? Math.min(now - lastTime, 40) : 0; lastTime = now;
      if (!visible || document.hidden || !allowsMotion()) {coast = 0; lastTime = 0; return;}
      if (coast) {
        moveBy(coast * dt); coast *= Math.exp(-dt / 170);
        if (Math.abs(coast) < .015) coast = 0;
      } else if (canAuto() && now >= holdUntil) moveBy(speed * dt / 1000);
      if (coast || canAuto()) wake(); else lastTime = 0;
    }
    const interrupt = () => {coast = 0; remainder = 0; holdUntil = performance.now() + 1800; stopMotion();};

    const behavior = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    const scrollTo = (id, options = {}) => {
      const card = cards.get(id);
      if (destroyed || !card) return false;
      interrupt();
      const bounds = viewport.getBoundingClientRect(), rect = card.parentElement.getBoundingClientRect();
      const padding = parseFloat(getComputedStyle(viewport).paddingLeft) || 0;
      viewport.scrollTo({left:viewport.scrollLeft + rect.left - bounds.left - padding, behavior:behavior() === 'auto' ? 'auto' : options.behavior || 'smooth'});
      wake();
      return true;
    };
    function step(direction) {
      interrupt();
      if (looping && direction < 0 && viewport.scrollLeft <= 2) moveBy(-1);
      if (looping && direction > 0 && viewport.scrollLeft >= viewport.scrollWidth - viewport.clientWidth - 2) moveBy(1);
      const all = [...list.children].map(slot => slot.firstElementChild);
      if (!all.length) return;
      const edge = viewport.getBoundingClientRect().left + (parseFloat(getComputedStyle(viewport).paddingLeft) || 0);
      const positions = all.map(card => card.parentElement.getBoundingClientRect().left - edge);
      const index = direction > 0 ? positions.findIndex(x => x > 2) : positions.findLastIndex(x => x < -2);
      scrollTo(all[index < 0 ? direction > 0 ? all.length - 1 : 0 : index].dataset.cardId);
    }
    const sync = () => {
      if (destroyed) return;
      const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
      navigation.hidden = !items.length || max <= 2;
      previous.setDisabled(!looping && viewport.scrollLeft <= 2);
      next.setDisabled(!looping && viewport.scrollLeft >= max - 2);
      // The shared renderer must track native scrolling, including keyboard focus reveal.
      element.dispatchEvent(new CustomEvent('lg:sync', {bubbles:true}));
    };
    viewport.addEventListener('scroll', () => {
      if (!frame) frame = requestAnimationFrame(() => {frame = 0; sync();});
    }, {signal, passive:true});
    viewport.addEventListener('keydown', event => {
      if (event.target !== viewport || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home' || event.key === 'End') scrollTo(items[event.key === 'Home' ? 0 : items.length - 1]?.id);
      else step(event.key === 'ArrowRight' ? 1 : -1);
    }, {signal});
    element.addEventListener('pointerenter', event => {if (event.pointerType !== 'touch') {hovered = true; stopMotion();}}, {signal});
    element.addEventListener('pointerleave', event => {if (event.pointerType !== 'touch') {hovered = false; wake();}}, {signal});
    element.addEventListener('focusin', () => {focused = true; interrupt();}, {signal});
    element.addEventListener('focusout', () => queueMicrotask(() => {if (!destroyed) {focused = element.contains(document.activeElement); wake();}}), {signal});
    viewport.addEventListener('wheel', () => {interrupt(); wake();}, {signal, passive:true});
    viewport.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary) return;
      interrupt(); interaction = true;
      // Touch keeps native horizontal pan and vertical page scrolling.
      if (event.pointerType === 'touch') return;
      const card = event.target.closest('.lg-lens-card');
      const control = event.target.closest('button,input,select,textarea,a,summary,[role=button],[role=slider],[contenteditable]:not([contenteditable=false]),[data-jelly],[data-optical-drag]');
      if (!card || (control && control !== card)) return;
      drag = {id:event.pointerId, startX:event.clientX, startY:event.clientY, x:event.clientX, time:performance.now(), velocity:0, moved:false};
      // Capture from the press so releasing outside an iframe cannot strand playback.
      try {viewport.setPointerCapture(event.pointerId);} catch { /* Synthetic pointers have no active capture. */ }
    }, {signal});
    window.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      if (!drag.moved && Math.abs(dx) < 5) return;
      if (!drag.moved && Math.abs(dy) > Math.abs(dx)) {release(event, true); return;}
      if (!drag.moved) {
        drag.moved = true; element.dataset.dragging = 'true';
      }
      event.preventDefault();
      const now = performance.now(), delta = drag.x - event.clientX;
      drag.velocity = Math.max(-2, Math.min(2, delta / Math.max(8, now - drag.time)));
      moveBy(delta); drag.x = event.clientX; drag.time = now;
    }, {signal, passive:false});
    const release = (event, cancelled = false) => {
      if (drag && event && event.pointerId !== drag.id) return;
      const previousDrag = drag; drag = null; interaction = false; delete element.dataset.dragging;
      if (previousDrag && viewport.hasPointerCapture?.(previousDrag.id)) viewport.releasePointerCapture(previousDrag.id);
      if (previousDrag?.moved && !cancelled && !reduced.matches && performance.now() - previousDrag.time < 100) coast = previousDrag.velocity;
      holdUntil = performance.now() + 1800; wake();
    };
    window.addEventListener('pointerup', event => release(event), {signal});
    window.addEventListener('pointercancel', event => release(event, true), {signal});
    viewport.addEventListener('lostpointercapture', event => {if (drag?.id === event.pointerId) release(event, true);}, {signal});
    const suspend = () => {release(null, true); coast = 0; stopMotion();};
    window.addEventListener('blur', suspend, {signal});
    document.addEventListener('visibilitychange', () => {if (document.hidden) suspend(); else wake();}, {signal});
    const measure = () => {
      const first = list.firstElementChild, second = first?.nextElementSibling;
      stride = second ? second.offsetLeft - first.offsetLeft : 0;
      loopEdge = stride + (parseFloat(getComputedStyle(viewport).paddingLeft) || 0) + 24;
      looping = !!autoScroll && stride > 0 && viewport.scrollWidth - viewport.clientWidth > loopEdge + 2;
      element.dataset.loop = String(looping);
      playback.element.hidden = !looping;
      sync(); wake();
    };
    reduced.addEventListener('change', () => {suspend(); motionOptIn = false; if (reduced.matches) setPlaying(false); measure();}, {signal});
    const resize = new ResizeObserver(measure); resize.observe(viewport); resize.observe(list);
    const intersection = new IntersectionObserver(entries => {visible = entries[0].isIntersecting; if (!visible) suspend(); else wake();}); intersection.observe(element);
    setPlaying(wanted); measure();
    return {element, scrollTo, pause:() => setPlaying(false), play:() => {autoScroll ||= true; measure(); setPlaying(true);}, get playing() {return wanted;}, destroy() {
      if (destroyed) return;
      suspend(); destroyed = true; abort.abort(); resize.disconnect(); intersection.disconnect(); cancelAnimationFrame(frame);
      cards.forEach(card => clearOpticalState(card)); owned.forEach(handle => handle.destroy());
    }};
  });
}
