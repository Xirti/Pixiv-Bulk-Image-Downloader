/* A visual bridge: native selects retain their values, options, disabled state and business events. */
import {createComponent} from './vendor/lens/index.js';

let sequence = 0;
let activeControl = null;

export function enhanceSelect(select, {material = 'glass'} = {}) {
  const owner = select.ownerDocument;
  const captionNode = select.labels?.[0];
  const caption = select.getAttribute('aria-label') || captionNode?.querySelector(':scope > span')?.textContent.trim()
    || [...(captionNode?.childNodes || [])].filter(node => node.nodeType === 3).map(node => node.textContent).join('').trim() || '选项';
  const id = `glass-select-${++sequence}`;
  const abort = new AbortController();
  const {signal} = abort;
  const original = {tabIndex: select.getAttribute('tabindex'), ariaHidden: select.getAttribute('aria-hidden'), labelFor: captionNode?.getAttribute('for')};
  const component = createComponent('button', {label: caption, onPress: () => opened ? close() : open()});
  const trigger = component.element;
  trigger.id = `${id}-trigger`;
  trigger.classList.add('lab-select-trigger');
  if (material === 'quiet') {
    trigger.classList.add('lab-select-quiet');
    trigger.classList.remove('lg-surface');
    delete trigger.dataset.glass; delete trigger.dataset.lgLens;
  }
  trigger.setAttribute('role', 'combobox');
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', `${id}-popup`);
  if (material === 'quiet') delete trigger.dataset.lgDrag;
  else trigger.dataset.lgDrag = 'press';
  const valueText = owner.createElement('span');
  valueText.className = 'lab-select-value';
  const arrow = owner.createElement('span');
  arrow.className = 'lab-select-chevron'; arrow.setAttribute('aria-hidden', 'true');
  trigger.replaceChildren(valueText, arrow);

  const popup = owner.createElement('div');
  popup.id = `${id}-popup`;
  popup.className = 'lab-select-popup';
  if (material === 'quiet') popup.classList.add('lab-select-popup-quiet');
  popup.setAttribute('role', 'listbox'); popup.setAttribute('aria-label', caption);
  const supportsPopover = typeof popup.showPopover === 'function';
  if (supportsPopover) popup.setAttribute('popover', 'auto');
  else popup.hidden = true;
  owner.body.append(popup);
  select.after(trigger);
  select.classList.add('lab-native-select');
  select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
  if (captionNode) captionNode.htmlFor = trigger.id;

  let opened = false;
  let destroyed = false;
  let records = [];
  let activeIndex = -1;
  let typeBuffer = '';
  let typeTime = 0;
  let queued = false;

  const available = () => records.filter(row => !row.disabled);
  function paintActive(index) {
    activeIndex = index;
    for (const row of records) row.element.classList.toggle('is-active', row.index === index);
    const row = records.find(item => item.index === index);
    if (row) {
      trigger.setAttribute('aria-activedescendant', row.element.id);
      // scrollIntoView can move the page as well as this fixed popup, closing it.
      const bounds = row.element.getBoundingClientRect();
      const top = popup.getBoundingClientRect().top + popup.clientTop;
      const bottom = top + popup.clientHeight;
      if (bounds.top < top) popup.scrollTop -= top - bounds.top;
      else if (bounds.bottom > bottom) popup.scrollTop += bounds.bottom - bottom;
    } else trigger.removeAttribute('aria-activedescendant');
  }

  function position() {
    if (!opened) return;
    const bounds = trigger.getBoundingClientRect();
    if (!bounds.width || !bounds.height || bounds.bottom < 0 || bounds.top > innerHeight) {close(); return;}
    const gap = 8, edge = 10;
    const width = Math.min(Math.max(bounds.width, 230), innerWidth - edge * 2);
    popup.style.width = `${width}px`;
    popup.style.left = `${Math.max(edge, Math.min(bounds.left, innerWidth - edge - width))}px`;
    const below = innerHeight - bounds.bottom - gap - edge;
    const above = bounds.top - gap - edge;
    const upward = below < Math.min(popup.scrollHeight, 260) && above > below;
    popup.style.maxHeight = `${Math.max(50, Math.min(300, upward ? above : below))}px`;
    popup.style.top = `${upward ? Math.max(edge, bounds.top - popup.offsetHeight - gap) : bounds.bottom + gap}px`;
  }

  function sync() {
    if (destroyed) return;
    const previousActive = records.find(row => row.index === activeIndex)?.option;
    const options = [...select.options].map((option, index) => ({
      index, option, label: option.label, selected: option.selected,
      disabled: option.disabled || Boolean(option.closest('optgroup')?.disabled),
    }));
    const changed = options.length !== records.length || options.some((row, index) => {
      const old = records[index];
      return row.option !== old.option || row.label !== old.label
        || row.selected !== old.selected || row.disabled !== old.disabled;
    });
    if (changed) {
      records = options.map(row => {
        const element = owner.createElement('div');
        element.id = `${id}-option-${row.index}`; element.className = 'lab-select-option';
        element.setAttribute('role', 'option'); element.setAttribute('aria-selected', String(row.selected));
        element.setAttribute('aria-disabled', String(row.disabled));
        element.dataset.index = String(row.index); element.textContent = row.label;
        return {...row, element};
      });
      popup.replaceChildren(...records.map(row => row.element));
    }
    const value = select.selectedOptions[0]?.label || '请选择';
    if (valueText.textContent !== value) valueText.textContent = value;
    if (trigger.title !== value) trigger.title = value;
    const label = `${caption}：${value}`;
    if (trigger.getAttribute('aria-label') !== label) trigger.setAttribute('aria-label', label);
    const disabled = select.disabled || !available().length;
    if (trigger.disabled !== disabled) trigger.disabled = disabled;
    if (trigger.hidden !== select.hidden) trigger.hidden = select.hidden;
    if (trigger.disabled || trigger.hidden) close();
    if (opened) {
      const next = records.find(row => row.option === previousActive && !row.disabled) || records.find(row => row.option.selected && !row.disabled) || available()[0];
      position(); paintActive(next?.index ?? -1);
    }
  }

  function scheduleSync() {
    if (queued || destroyed) return;
    queued = true;
    queueMicrotask(() => {queued = false; sync();});
  }

  function open(edge) {
    sync();
    if (destroyed || trigger.disabled || opened) return;
    activeControl?.close();
    activeControl = api;
    opened = true;
    if (supportsPopover) popup.showPopover();
    else popup.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    position();
    const rows = available();
    const next = edge === 'first' ? rows[0] : edge === 'last' ? rows.at(-1) : rows.find(row => row.option.selected) || rows[0];
    paintActive(next?.index ?? -1);
  }

  function finishClose() {
    opened = false;
    typeBuffer = ''; typeTime = 0;
    trigger.setAttribute('aria-expanded', 'false');
    trigger.removeAttribute('aria-activedescendant');
    if (activeControl === api) activeControl = null;
  }

  function close(returnFocus = false) {
    if (!opened) return;
    finishClose();
    if (supportsPopover) {if (popup.matches(':popover-open')) popup.hidePopover();}
    else popup.hidden = true;
    if (returnFocus && !trigger.disabled) trigger.focus({preventScroll: true});
  }

  function commit(index) {
    const row = records.find(item => item.index === index);
    // Re-read current native state: authorization or task state may change while the popup is open.
    if (!row || select.disabled || row.option.disabled || row.option.closest('optgroup')?.disabled || !select.contains(row.option)) return;
    const changed = select.selectedIndex !== row.option.index;
    select.selectedIndex = row.option.index;
    close(true);
    if (changed) {
      select.dispatchEvent(new Event('input', {bubbles: true}));
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    sync();
  }

  function keydown(event) {
    if (event.isComposing || trigger.disabled) return;
    const key = event.key;
    if (key === 'Tab') {close(); return;}
    if (key === 'Escape' && opened) {event.preventDefault(); event.stopPropagation(); close(true); return;}
    if (['Enter', ' ', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key)) {
      event.preventDefault();
      if (event.altKey && key === 'ArrowUp') {close(true); return;}
      if (!opened) {open(key === 'Home' ? 'first' : key === 'End' ? 'last' : undefined); return;}
      if (key === 'Enter' || key === ' ') {commit(activeIndex); return;}
      const rows = available(), current = rows.findIndex(row => row.index === activeIndex);
      const next = key === 'Home' ? 0 : key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, current + (key === 'ArrowDown' ? 1 : -1)));
      paintActive(rows[next]?.index ?? -1);
    } else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      const now = performance.now();
      typeBuffer = now - typeTime < 650 ? typeBuffer + key : key; typeTime = now;
      if (!opened) open();
      const row = available().find(item => item.option.label.toLocaleLowerCase().startsWith(typeBuffer.toLocaleLowerCase()));
      if (row) paintActive(row.index);
    }
  }

  trigger.addEventListener('keydown', keydown, {signal});
  popup.addEventListener('keydown', keydown, {signal});
  popup.addEventListener('click', event => {
    const option = event.target.closest('[data-index]');
    if (option && popup.contains(option)) commit(Number(option.dataset.index));
  }, {signal});
  popup.addEventListener('pointermove', event => {
    const option = event.target.closest('[data-index]');
    const row = records.find(item => item.element === option);
    if (row && !row.disabled && row.index !== activeIndex) paintActive(row.index);
  }, {signal, passive: true});
  popup.addEventListener('toggle', event => {if (event.newState === 'closed') finishClose();}, {signal});
  owner.addEventListener('pointerdown', event => {
    if (opened && !popup.contains(event.target) && !trigger.contains(event.target)) close();
  }, {signal, capture: true, passive: true});
  owner.addEventListener('click', scheduleSync, {signal});
  owner.addEventListener('change', scheduleSync, {signal});
  owner.addEventListener('reset', scheduleSync, {signal});
  owner.addEventListener('visibilitychange', () => {if (owner.hidden) close();}, {signal});
  // A wheel begun before opening can deliver its scroll notification afterwards.
  // Keep that notification aligned; new wheel input outside the popup dismisses it.
  owner.addEventListener('scroll', event => {if (opened && !popup.contains(event.target)) position();}, {signal, capture: true, passive: true});
  owner.addEventListener('wheel', event => {if (opened && !popup.contains(event.target)) close();}, {signal, capture: true, passive: true});
  window.addEventListener('resize', () => close(), {signal, passive: true});
  select.addEventListener('change', scheduleSync, {signal});
  const observer = new MutationObserver(scheduleSync);
  observer.observe(select, {childList: true, subtree: true, characterData: true, attributes: true,
    attributeFilter: ['disabled', 'hidden', 'label', 'value', 'selected']});
  const api = {trigger, popup, sync, close, destroy() {
    if (destroyed) return;
    close(); destroyed = true; abort.abort(); observer.disconnect(); component.destroy();
    trigger.remove(); popup.remove(); select.classList.remove('lab-native-select');
    for (const [name, value] of [['tabindex', original.tabIndex], ['aria-hidden', original.ariaHidden]]) {
      if (value === null) select.removeAttribute(name); else select.setAttribute(name, value);
    }
    if (captionNode) {if (original.labelFor === null) captionNode.removeAttribute('for'); else captionNode.setAttribute('for', original.labelFor);}
  }};
  sync();
  return api;
}
