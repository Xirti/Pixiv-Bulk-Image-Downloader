import {tokens} from './tokens.js';
import {OpticalDragMotion} from './internal/optical-motion.js';
import {opticClamp, opticRange, opticXY, opticPoint, opticDropTarget, opticColor, setOpticalState, readOpticalState, clearOpticalState, opticalSelection} from './internal/optics.js';

let opticalId = 0;
const opticalNode = (tag, className, text) => {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};
const opticalLabel = value => {
  if (typeof value !== 'string' || !value.trim()) throw new TypeError('A non-empty accessible label is required');
  return value;
};
const opticalSurface = (element, kind) => { element.classList.add('lg-surface'); element.dataset.glass = kind; return element; };
const opticalSignal = (element, cue) => element.dispatchEvent(new CustomEvent('lg:commit', {bubbles: true, detail: {cue}}));
const opticalSync = element => element.dispatchEvent(new CustomEvent('lg:sync', {bubbles: true}));

export function opticalIcon(name = 'focus') {
  const paths = {
    focus: 'M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8 0h3a2 2 0 0 0 2-2v-3M8 12h8m-4-4v8',
    sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
    moon: 'M20.5 14.4A8.5 8.5 0 0 1 9.6 3.5 8.5 8.5 0 1 0 20.5 14.4Z',
    leaf: 'M5 19C1 9 10 3 21 3c0 11-6 20-16 16Zm0 0L16 8m-6 7v-5m0 5h5',
    heart: 'M12 20 3.8 12a5 5 0 0 1 7.1-7.1L12 6l1.1-1.1a5 5 0 0 1 7.1 7.1Z',
    check: 'm5 12 4 4L19 6',
    crosshair: 'M12 4v5m0 6v5M4 12h5m6 0h5',
    plus: 'M12 5v14M5 12h14',
    wifi:'M3 9a14 14 0 0 1 18 0M6 12a9 9 0 0 1 12 0m-9 3a4 4 0 0 1 6 0m-3 3h.01',
    bluetooth:'M12 2v20l6-6L6 6m0 12L18 6l-6-4',
    home:'m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7',
    layers:'m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5',
    adjust:'M4 5h16M4 12h16M4 19h16M8 2v6m8 1v6m-7 1v6',
    more:'M5 12h.01M12 12h.01M19 12h.01',
    reset:'M4 10a8 8 0 1 1 0 5M4 3v7h7',
  };
  if (!(name in paths)) throw new TypeError(`Unknown optical icon: ${name}`);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.classList.add('lg-icon', 'lg-optical-icon');
  for (const className of ['lg-icon-relief', 'lg-icon-face']) {
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', paths[name]); path.setAttribute('class', className); svg.append(path);
  }
  return svg;
}

export function addOpticalRim(element) {
  if (element.querySelector('.lg-optic-rim')) return;
  const rim = opticalNode('span', 'lg-optic-rim'), beam = opticalNode('span', 'lg-optic-beam');
  rim.setAttribute('aria-hidden', 'true'); rim.append(beam); element.append(rim);
}

function opticalPulse(element, color, forceMotion = false) {
  const beam = element.querySelector('.lg-optic-beam');
  beam?.getAnimations().forEach(animation => animation.cancel());
  if (forceMotion || !matchMedia('(prefers-reduced-motion: reduce)').matches) beam?.animate([{transform: 'rotate(-100deg)', opacity: 0}, {opacity: 1, offset: .1}, {transform: 'rotate(260deg)', opacity: 0}], {duration: tokens.optics.flowDuration, easing: 'ease-out'});
  element.style.setProperty('--lg-optical-color', tokens.optics[color]);
}

export function createOpticalProgress({label, value = 0, max = 100, completionColor = 'green', completeLabel = 'Complete', wave = true, motion = 'system', onChange = () => {}, onComplete = () => {}} = {}) {
  opticalLabel(label); opticalLabel(completeLabel); opticColor(completionColor);
  if (!Number.isFinite(max) || max <= 0 || !Number.isFinite(value)) throw new TypeError('Invalid progress range');
  if (!['system','on','off'].includes(motion)) throw new TypeError('Invalid progress motion preference');
  const element = opticalNode('div', 'lg-progress'), head = opticalNode('div', 'lg-progress-heading'), output = opticalNode('output', 'lg-progress-value');
  const track = opticalSurface(opticalNode('div', 'lg-progress-track'), 'optical-progress');
  const fill = opticalNode('span', 'lg-progress-fill'), emitter = opticalNode('span', 'lg-progress-emitter');
  const fiber = document.createElementNS('http://www.w3.org/2000/svg','svg'), fiberPath = document.createElementNS(fiber.namespaceURI,'path');
  fiber.setAttribute('viewBox','0 0 600 46'); fiber.setAttribute('preserveAspectRatio','none'); fiber.setAttribute('aria-hidden','true'); fiber.classList.add('lg-progress-fiber');
  fiberPath.setAttribute('d','M-240 23 Q-180 17 -120 23 T0 23 T120 23 T240 23 T360 23 T480 23 T600 23 T720 23 T840 23'); fiber.append(fiberPath);
  const status = opticalNode('span', 'lg-visually-hidden'); status.setAttribute('role', 'status');
  track.setAttribute('role', 'progressbar'); track.setAttribute('aria-label', label); track.setAttribute('aria-valuemin', '0'); track.setAttribute('aria-valuemax', String(max));
  fill.setAttribute('aria-hidden', 'true'); emitter.setAttribute('aria-hidden', 'true');
  head.append(opticalNode('span', '', label), output); track.append(fill, fiber, emitter); addOpticalRim(track); element.append(head, track, status);
  let current = 0, initialized = false, destroyed = false, color = completionColor;
  const setValue = (next, {emit = false, animate = true} = {}) => {
    if (destroyed) return;
    if (!Number.isFinite(next)) throw new TypeError('Progress value must be finite');
    const previous = current; current = opticClamp(next, 0, max);
    const complete = current === max, crossed = initialized && previous < max && complete;
    element.dataset.complete = String(complete); element.style.setProperty('--lg-progress', String(current / max));
    element.dataset.motion = motion; element.dataset.wave = String(Boolean(wave) && motion !== 'off' && current > 0);
    element.style.setProperty('--lg-optical-color', tokens.optics[color]);
    output.value = `${Math.round(current / max * 100)}%${complete ? ` · ${completeLabel}` : ''}`;
    track.setAttribute('aria-valuenow', String(current)); track.setAttribute('aria-valuetext', output.value);
    status.textContent = complete ? completeLabel : '';
    setOpticalState(track, {kind: 1, fill: current / max, complete: complete ? 1 : 0, color: opticColor(color), wave: Boolean(wave) && motion !== 'off', forceMotion: motion === 'on'}, {animate: initialized && animate && motion !== 'off', pulse: crossed});
    if (crossed && animate && motion !== 'off') opticalPulse(track, color, motion === 'on');
    if (!complete) track.querySelector('.lg-optic-beam').getAnimations().forEach(animation => animation.cancel());
    if (emit && previous !== current) onChange(current);
    if (crossed && emit) { onComplete(current); opticalSignal(element, 'success'); }
    initialized = true;
  };
  setValue(value, {animate: false});
  return {element, get value() {return current;}, setValue, setCompletionColor(next) {opticColor(next); color = next; setValue(current, {animate: false});}, setMotion(next) {if(!['system','on','off'].includes(next)) throw new TypeError('Invalid progress motion preference'); motion=next; setValue(current,{animate:false});}, destroy() {destroyed = true; element.dataset.wave='false'; track.querySelector('.lg-optic-beam').getAnimations().forEach(animation => animation.cancel()); clearOpticalState(track);}};
}

export function createDropSelect({label, options = [], value, variant = 'lens', selectionColor = 'green', disabled = false, dragLabel = 'Drag to a glowing well, or use arrow keys', onChange = () => {}} = {}) {
  opticalLabel(label); opticalLabel(dragLabel); opticColor(selectionColor);
  if (!['lens', 'capsule'].includes(variant) || options.length < 2 || options.length > 6 || new Set(options.map(option => option.value)).size !== options.length || options.some(option => typeof option.value !== 'string')) throw new TypeError('Use 2–6 unique drop options and a lens or capsule');
  options.forEach(option => { opticalLabel(option.label); if (option.icon) opticalIcon(option.icon); });
  const enabled = () => options.filter(option => !option.disabled);
  if (!enabled().length) throw new TypeError('A drop selector needs an enabled option');
  let current = value ?? enabled()[0].value;
  if (!enabled().some(option => option.value === current)) throw new TypeError('Invalid initial drop selection');
  const element = opticalNode('div', 'lg-drop-select'), field = opticalNode('div', 'lg-drop-field'), wells = opticalNode('div', 'lg-drop-wells');
  const selector = opticalSurface(opticalNode('button', 'lg-drop-selector'), 'optical-lens');
  const hint = opticalNode('p', 'lg-drop-hint', dragLabel), status = opticalNode('output', 'lg-drop-status');
  const abort = new AbortController(), signal = abort.signal, records = [];
  let gesture = null, destroyed = false, isDisabled = Boolean(disabled), initialized = false;
  const motionPreference = matchMedia('(prefers-reduced-motion: reduce)');
  const motion = new OpticalDragMotion({intensity: tokens.motion.elasticity, reduced: motionPreference.matches});
  let motionFrame = 0, motionLast = 0, phase='rest', appearanceUntil=0, mergeUntil=0, pendingPulse=false;
  const setPhase=next=>{phase=next;element.dataset.phase=next;selector.dataset.active=String(next!=='rest');};
  const paintMotion = (dt,now=performance.now()) => {
    const pose = motion.step(dt);
    selector.style.left='0';selector.style.top='0';
    selector.style.transform = `translate(${pose.x.toFixed(3)}px,${pose.y.toFixed(3)}px) translate(-50%,-50%) matrix(${pose.matrix.map(value => value.toFixed(5)).join(',')},0,0)`;
    selector.style.setProperty('--lg-drop-visibility',String(readOpticalState(selector,now,motion.reduced)?.opacity??0));
    opticalSync(selector);
  };
  const tickMotion = now => {
    motionFrame = 0;
    if (destroyed) return;
    const dt = motionLast ? Math.min((now - motionLast) / 1000, .05) : 1 / 60; motionLast = now;
    paintMotion(dt,now);
    if(phase==='travelling'&&Math.hypot(motion.x.target-motion.x.value,motion.y.target-motion.y.value)<5&&Math.hypot(motion.x.velocity,motion.y.velocity)<90)merge(now);
    if(phase==='merging'&&now>=mergeUntil){setPhase('rest');selector.style.setProperty('--lg-drop-visibility','0');}
    if (motion.moving||phase==='travelling'||phase==='merging'||now<appearanceUntil) wakeMotion(); else motionLast = 0;
  };
  const wakeMotion = () => {if (!motionFrame && !destroyed) motionFrame = requestAnimationFrame(tickMotion);};
  motionPreference.addEventListener('change', event => {motion.reduced = event.matches; wakeMotion();}, {signal});
  element.dataset.variant = variant; element.setAttribute('role', 'group'); element.setAttribute('aria-label', label);
  element.style.setProperty('--lg-optical-color', tokens.optics[selectionColor]);
  hint.id = `lg-drop-hint-${++opticalId}`; selector.type = 'button'; selector.setAttribute('aria-describedby', hint.id); selector.dataset.opticalDrag = 'true';
  wells.setAttribute('role', 'radiogroup'); wells.setAttribute('aria-label', label); wells.style.setProperty('--lg-option-count', String(options.length));
  for (const option of options) {
    const button = opticalNode('button', 'lg-drop-option'), well = opticalSurface(opticalNode('span', 'lg-drop-well'), 'optical-well');
    button.type = 'button'; button.setAttribute('role', 'radio'); button.dataset.value = option.value; button.setAttribute('aria-label', option.label);
    well.append(opticalIcon(option.icon || 'focus')); addOpticalRim(well);
    const caption = opticalNode('span', 'lg-drop-caption', option.label); caption.append(opticalIcon('check'));
    button.append(well, caption); wells.append(button); records.push({option, button, well});
    setOpticalState(well, {kind: 2, color: opticColor(selectionColor)}, {animate: false});
    button.addEventListener('click', () => {if (!isDisabled && !option.disabled) setValue(option.value, {emit: true});}, {signal});
  }
  addOpticalRim(selector); field.append(wells, selector); status.setAttribute('aria-live', 'polite'); element.append(hint, field, status);
  const targets = () => {
    const root = field.getBoundingClientRect();
    return records.map(({option, well}) => {const rect = well.getBoundingClientRect(); return {value: option.value, disabled: isDisabled || option.disabled, x: rect.left - root.left + rect.width / 2, y: rect.top - root.top + rect.height / 2, rx: rect.width / 2, ry: rect.height / 2};});
  };
  const place = (x, y, snap = false) => {motion.target(x, y, {snap}); if (snap) paintMotion(0); else wakeMotion();};
  const dock = (snap = false) => {
    const target = targets().find(item => item.value === current);
    if (!target) return;
    // Keep the resting and held outlines identical, including narrow layouts.
    selector.style.width = `${target.rx * 2}px`; selector.style.height = `${target.ry * 2}px`;
    place(target.x, target.y, snap);
  };
  const illuminate = (hit,animate=true,pulse=false) => records.forEach(({option, well}) => {
    const active = gesture && !isDisabled && !option.disabled;
    well.dataset.dropActive = String(Boolean(active)); well.dataset.dropHit = String(Boolean(active && option.value === hit));
    const selected=!gesture&&['rest','merging'].includes(phase)&&option.value===current;
    well.dataset.selected = String(selected);
    setOpticalState(well, {hover: active ? option.value === hit ? 1 : .4 : 0, selected: selected ? 1 : 0}, {animate,pulse:pulse&&selected,duration:tokens.optics.settleDuration});
    if(pulse&&selected&&animate)opticalPulse(well,selectionColor);
  });
  const showCursor=(opacity,animate=true)=>{setOpticalState(selector,{kind:4,zoom:1,opacity,selected:1,color:opticColor(selectionColor),deformation:motion.optical},{animate});appearanceUntil=performance.now()+tokens.optics.settleDuration;wakeMotion();};
  const merge=now=>{setPhase('merging');mergeUntil=now+(motion.reduced?80:tokens.optics.settleDuration);showCursor(0);illuminate(null,true,pendingPulse);pendingPulse=false;};
  const travel=(pulse=false)=>{setPhase('travelling');pendingPulse=pulse;showCursor(1);illuminate(null);dock();};
  const setValue = (next, {emit = false, animate = true} = {}) => {
    if (destroyed) return false;
    const choice = enabled().find(option => option.value === next);
    if (!choice) return false;
    const changed = current !== next; current = next;
    for (const {option, button, well} of records) {
      const selected = option.value === current;
      button.setAttribute('aria-checked', String(selected)); button.tabIndex = selected ? 0 : -1; button.disabled = isDisabled || Boolean(option.disabled);
      well.style.setProperty('--lg-optical-color',tokens.optics[selectionColor]);
    }
    selector.disabled = isDisabled; selector.setAttribute('aria-label', `${label}: ${choice.label}`);
    // The cursor is clear glass: the well's icon stays visible underneath it.
    status.value = choice.label;
    if(!initialized||!animate){setPhase('rest');dock(true);showCursor(0,false);illuminate(null,false);}
    else if(changed||phase!=='rest')travel(changed||pendingPulse);
    if (emit && changed) {onChange(current); opticalSignal(element, 'select');}
    initialized = true; return true;
  };
  const end = (event, cancel = false) => {
    if (!gesture || (event?.pointerId !== undefined && gesture.id !== event.pointerId)) return;
    const active = gesture; gesture = null; selector.dataset.dragging = 'false';
    motion.hold(false); wakeMotion();
    if (selector.hasPointerCapture(active.id)) selector.releasePointerCapture(active.id);
    pendingPulse=!cancel&&active.moved;
    const target=!cancel&&active.moved?opticDropTarget({x:motion.x.target,y:motion.y.target},targets()):current;
    setValue(target??current,{emit:!cancel});
  };
  selector.addEventListener('pointerdown', event => {
    if (destroyed || isDisabled || gesture || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault(); selector.focus({preventScroll: true});
    gesture = {id: event.pointerId, startX: event.clientX, startY: event.clientY, x: motion.x.value, y: motion.y.value, moved: false, hit: null};
    motion.hold(true);setPhase('held');pendingPulse=false;wakeMotion();
    selector.setPointerCapture(event.pointerId); selector.dataset.dragging = 'true'; illuminate(null);
    showCursor(1);
  }, {signal});
  const movePointer = event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    event.preventDefault(); const dx = event.clientX - gesture.startX, dy = event.clientY - gesture.startY;
    gesture.moved ||= Math.hypot(dx, dy) > 5;
    const x = opticClamp(gesture.x + dx, selector.offsetWidth / 2, field.clientWidth - selector.offsetWidth / 2), y = opticClamp(gesture.y + dy, selector.offsetHeight / 2, field.clientHeight - selector.offsetHeight / 2);
    place(x, y);
    const hit = opticDropTarget({x, y}, targets());
    if (hit !== gesture.hit) {gesture.hit = hit; illuminate(hit); if (hit) opticalSignal(element, 'tick');}
  };
  selector.addEventListener('pointermove', movePointer, {signal});
  selector.addEventListener('pointerup', event => {movePointer(event);end(event);}, {signal});
  selector.addEventListener('pointercancel', event => end(event, true), {signal});
  selector.addEventListener('lostpointercapture', event => end(event, true), {signal});
  selector.addEventListener('click', event => {event.preventDefault();}, {signal});
  element.addEventListener('keydown', event => {
    if (event.key === 'Escape' && gesture) {event.preventDefault(); end(null, true); return;}
    if (isDisabled || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); end(null, true);
    const available = enabled(), index = available.findIndex(option => option.value === current);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1 : (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + available.length) % available.length;
    setValue(available[next].value, {emit: true});
    if (event.target !== selector) records.find(record => record.option.value === current).button.focus();
  }, {signal});
  window.addEventListener('blur', () => end(null, true), {signal});
  document.addEventListener('visibilitychange', () => {if (document.hidden) {end(null, true);setValue(current,{animate:false});cancelAnimationFrame(motionFrame);motionFrame=motionLast=0;}else wakeMotion();}, {signal});
  const observer = new ResizeObserver(() => {if(gesture)end(null,true);dock(phase==='rest');}); observer.observe(field);
  setValue(current, {animate: false});
  return {element, get value() {return current;}, setValue, setVariant(next){if(!['lens','capsule'].includes(next))throw new TypeError('Invalid drop variant');end(null,true);element.dataset.variant=next;dock(phase==='rest');opticalSync(element);}, setDisabled(next) {isDisabled = Boolean(next); end(null, true); setValue(current, {animate: false});}, destroy() {if (destroyed) return; end(null, true); destroyed = true; cancelAnimationFrame(motionFrame); abort.abort(); observer.disconnect(); selector.querySelector('.lg-optic-beam').getAnimations().forEach(animation => animation.cancel()); clearOpticalState(selector); records.forEach(({well}) => {well.querySelector('.lg-optic-beam').getAnimations().forEach(animation => animation.cancel()); clearOpticalState(well);});}};
}

export function createXYSlider({label, xLabel = 'Warmth', yLabel = 'Tone', min = -100, max = 100, step = 1, value = {x: 0, y: 0}, variant = 'point', magnification = tokens.optics.magnification, disabled = false, instruction = 'Drag in two dimensions. Arrow keys adjust; Home resets.', onChange = () => {}, onCommit = () => {}} = {}) {
  opticalLabel(label); opticalLabel(xLabel); opticalLabel(yLabel); opticalLabel(instruction);
  if (!['point', 'lens'].includes(variant) || !Number.isFinite(magnification) || magnification < 1 || magnification > 3) throw new TypeError('Invalid XY variant or magnification');
  let current = opticXY(value, min, max, step), isDisabled = Boolean(disabled), destroyed = false, gesture = null;
  const initial = {...current}, element = opticalNode('div', 'lg-xy-slider'), pad = opticalSurface(opticalNode('div', 'lg-xy-pad'), 'xy-field');
  const thumb = opticalNode('span', 'lg-xy-thumb'), readout = opticalNode('output', 'lg-xy-readout'), axes = opticalNode('div', 'lg-xy-axes'), hint = opticalNode('p', 'lg-xy-hint', instruction);
  const abort = new AbortController(), signal = abort.signal, inputs = {};
  element.setAttribute('role', 'group'); element.setAttribute('aria-label', label); element.dataset.variant = variant;
  pad.tabIndex = 0; pad.setAttribute('role', 'group'); pad.setAttribute('aria-label', label); hint.id = `lg-xy-hint-${++opticalId}`; pad.setAttribute('aria-describedby', hint.id); pad.dataset.opticalDrag = 'true';
  thumb.setAttribute('aria-hidden', 'true'); thumb.append(opticalIcon('crosshair')); pad.append(thumb); setOpticalState(pad, {kind: 3}, {animate: false});
  const applyVariant = next => {
    if (!['point', 'lens'].includes(next)) throw new TypeError('Invalid XY variant');
    variant = next; element.dataset.variant = next;
    if (next === 'lens') {opticalSurface(thumb, 'optical-lens'); setOpticalState(thumb, {kind: 4, zoom: magnification}, {animate: false});}
    else {thumb.classList.remove('lg-surface'); delete thumb.dataset.glass; clearOpticalState(thumb);}
    opticalSync(pad);
  };
  for (const [axis, name] of [['x', xLabel], ['y', yLabel]]) {
    const row = opticalNode('label', 'lg-xy-axis'), input = opticalNode('input', ''); input.type = 'range'; input.min = min; input.max = max; input.step = step; input.setAttribute('aria-label', name);
    row.append(opticalNode('span', '', name), input); axes.append(row); inputs[axis] = input;
    input.addEventListener('input', () => {if (!isDisabled) setValue({...current, [axis]: Number(input.value)}, {emit: true});}, {signal});
    input.addEventListener('change', () => {if (!isDisabled) commit();}, {signal});
  }
  element.append(pad, readout, axes, hint);
  const setValue = (next, {emit = false} = {}) => {
    if (destroyed) return;
    const updated = opticXY(next, min, max, step), changed = updated.x !== current.x || updated.y !== current.y; current = updated;
    const x = (current.x - min) / (max - min), y = (current.y - min) / (max - min);
    thumb.style.left = `calc(40px + (100% - 80px) * ${x})`; thumb.style.top = `calc(40px + (100% - 80px) * ${1 - y})`;
    inputs.x.value = current.x; inputs.y.value = current.y;
    readout.value = `${xLabel} ${current.x > 0 ? '+' : ''}${current.x} · ${yLabel} ${current.y > 0 ? '+' : ''}${current.y}`;
    pad.setAttribute('aria-description', readout.value); opticalSync(pad);
    if (changed && emit) onChange({...current});
  };
  const commit = () => {onCommit({...current}); opticalSignal(element, 'tick');};
  const updatePointer = event => {
    const point = opticPoint(event.clientX, event.clientY, pad.getBoundingClientRect(), 40);
    setValue({x: min + point.x * (max - min), y: min + point.y * (max - min)}, {emit: true});
  };
  const end = (event, cancelled = false) => {
    if (!gesture || (event?.pointerId !== undefined && gesture.id !== event.pointerId)) return;
    const active = gesture; gesture = null; pad.dataset.dragging = 'false';
    if (pad.hasPointerCapture(active.id)) pad.releasePointerCapture(active.id);
    if (cancelled) setValue(active.start, {emit: true}); else commit();
  };
  pad.addEventListener('pointerdown', event => {
    if (isDisabled || destroyed || gesture || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault(); pad.focus({preventScroll: true}); gesture = {id: event.pointerId, start: {...current}};
    pad.setPointerCapture(event.pointerId); pad.dataset.dragging = 'true'; updatePointer(event);
  }, {signal});
  pad.addEventListener('pointermove', event => {if (gesture?.id === event.pointerId) {event.preventDefault(); updatePointer(event);}}, {signal});
  pad.addEventListener('pointerup', event => {if (gesture?.id === event.pointerId) updatePointer(event); end(event);}, {signal});
  pad.addEventListener('pointercancel', event => end(event, true), {signal}); pad.addEventListener('lostpointercapture', event => end(event, true), {signal});
  pad.addEventListener('keydown', event => {
    if (event.key === 'Escape' && gesture) {event.preventDefault(); end(null, true); return;}
    if (isDisabled) return;
    const delta = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1]}[event.key];
    if (!delta && !['Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const amount = step * (event.shiftKey ? 10 : 1);
    setValue(event.key === 'Home' ? initial : event.key === 'End' ? {x: max, y: max} : {x: current.x + delta[0] * amount, y: current.y + delta[1] * amount}, {emit: true}); commit();
  }, {signal});
  window.addEventListener('blur', () => end(null, true), {signal});
  document.addEventListener('visibilitychange', () => {if (document.hidden) end(null, true);}, {signal});
  const setDisabled = next => {isDisabled = Boolean(next); end(null, true); element.dataset.disabled = String(isDisabled); pad.tabIndex = isDisabled ? -1 : 0; pad.setAttribute('aria-disabled', String(isDisabled)); Object.values(inputs).forEach(input => {input.disabled = isDisabled;});};
  applyVariant(variant); setValue(current); setDisabled(disabled);
  return {element, get value() {return {...current};}, setValue, setDisabled, setVariant(next) {end(null, true); applyVariant(next);}, destroy() {if (destroyed) return; end(null, true); destroyed = true; abort.abort(); clearOpticalState(pad); clearOpticalState(thumb);}};
}
