/* Experimental presentation adapter. Original DOM identities and actions stay owned by app.js. */
import {createComponent, createGlassSystem} from './vendor/lens/index.js';
import {enhanceSelect} from './glass-select.js';
import {mountDotTitle} from './dot-title.js';
import {mountCheckboxFeedback} from './check-feedback.js';
import {mountLatticeBackdrop} from './lattice-backdrop.js';

const root = document.documentElement;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const contrast = matchMedia('(forced-colors: active)');
const searchStage = document.querySelector('.search-panel');
const systems = new Map();
const components = [];
const abort = new AbortController();
const {signal} = abort;
components.push(mountCheckboxFeedback(document));
const backdrop = mountLatticeBackdrop(document.getElementById('home'), {onFrame() {
  systems.get(searchStage)?.refreshArtwork();
}});
components.push(backdrop);
const feedback = new Map();
const materials = {clear:{refraction:.98,blur:.025,dispersion:.8},thick:{refraction:1,blur:.10,dispersion:1},soft:{refraction:.85,blur:.32,dispersion:.35}};
let material = 'clear';
root.dataset.material = material;
let enabled = true;
let disposed = false;

function drawArtwork(ctx, {width, height, dpr}, stage) {
  if (stage === searchStage) {backdrop.paint(ctx, {width, height, dpr}, stage); return;}
  const dark = root.dataset.theme === 'dark';
  ctx.fillStyle = dark ? '#0b111b' : '#f3f7fb';ctx.fillRect(0, 0, width, height);
  const rect = stage.getBoundingClientRect(), step = 24 * dpr;
  ctx.fillStyle = dark ? '#7899b330' : '#7899b330';
  const first = offset => (((12 - offset) % 24 + 24) % 24) * dpr;
  for (let x = first(rect.left); x < width; x += step) for (let y = first(rect.top); y < height; y += step) {
    ctx.beginPath();ctx.arc(x,y,.8*dpr,0,Math.PI*2);ctx.fill();
  }
}

const searchPanel = createComponent('panel', {
  label: '搜索与筛选', motion: 'none', children: [...searchStage.childNodes],
});
searchPanel.element.classList.add('lab-search-body');
searchStage.append(searchPanel.element);
components.push(searchPanel);
for (const id of ['workType', 'safety', 'datePreset', 'quality', 'format']) {
  components.push(enhanceSelect(document.getElementById(id), {material: 'quiet'}));
}
const dotTitle = mountDotTitle(document.querySelector('.home-intro h1'));
components.push(dotTitle);
const selectionStage = document.getElementById('selectionBar');
// Decorative planes supply lens geometry without moving business nodes.
const detailStage = document.getElementById('detail').querySelector('.detail-info');
const basketStage = document.getElementById('basketArtworkDetail').querySelector('.detail-info');
for (const stage of [selectionStage, detailStage, basketStage]) {
  if (stage !== selectionStage) stage.classList.add('lab-detail-pane');
  const plane = document.createElement('div');
  plane.className = 'lab-glass-plane';
  plane.dataset.glass = stage === selectionStage ? 'dock' : 'panel';
  plane.setAttribute('aria-hidden', 'true');
  stage.prepend(plane);
  components.push({destroy: () => plane.remove()});
}
const tagRoots = ['dTags', 'basketDetailTags'].map(id => document.getElementById(id)).filter(Boolean);
function decorateTags() {
  for (const group of tagRoots) for (const tag of group.querySelectorAll('span,button')) {
    tag.classList.add('lab-glass-tag');
    tag.dataset.glass = 'small';
  }
}
decorateTags();
const tagObserver = new MutationObserver(decorateTags);
tagRoots.forEach(group => tagObserver.observe(group, {childList: true}));
const mountPoints = [searchStage, selectionStage, detailStage, basketStage];
function unmount(stage) {
  systems.get(stage)?.destroy();
  systems.delete(stage);
  stage.classList.remove('webgl-ready', 'fallback');
  for (const element of stage.querySelectorAll('[data-jelly]')) {
    delete element.dataset.jelly; delete element.dataset.held;
    element.classList.remove('jelly-moving');
    element.style.removeProperty('--jelly-matrix');
  }
}
function visible(stage, overlayOpen) {
  if (!stage.getClientRects().length || document.hidden) return false;
  // Pause the home layers while a workspace covers them.
  if ((stage === searchStage || stage === selectionStage) && overlayOpen) return false;
  const r = stage.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 1.75);
  // Long collections use the same CSS glass instead of allocating enormous framebuffers.
  if (r.width * r.height * dpr * dpr > 8_000_000 || Math.max(r.width, r.height) * dpr > 8192) return false;
  return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
}

function syncSystems() {
  if (disposed) return;
  const overlayOpen = Boolean(document.querySelector('.workspace-page:not([hidden]),.basket-page:not([hidden]),.all-viewer:not([hidden])'));
  root.dataset.scrollOwner = overlayOpen ? 'workspace' : 'document';
  backdrop.setActive(!overlayOpen);
  dotTitle.setActive(!overlayOpen);
  for (const stage of mountPoints) {
    const active = enabled && !contrast.matches && visible(stage, overlayOpen);
    if (!active && systems.has(stage)) {
      unmount(stage);
    } else if (active && !systems.has(stage)) {
      const system = createGlassSystem(stage, {
        material: materials[material], artworkAligned: true, tintScale: .12, brightness: 1, bevelWidth: 64, bevelPower: 1.05, scene: root.dataset.theme === 'dark' ? 'ocean' : 'aurora',
        artwork: (ctx, size) => drawArtwork(ctx, size, stage), motion: false, elasticity: reduced.matches ? 0 : .42,
        audio: {enabled: false},
      });
      systems.set(stage, system);
    }
  }
}

const observer = new IntersectionObserver(syncSystems);
mountPoints.forEach(stage => observer.observe(stage));
const selectionObserver = new MutationObserver(syncSystems);
for (const page of document.querySelectorAll('.workspace-page,.basket-page,.all-viewer,#selectionBar')) {
  selectionObserver.observe(page, {attributes: true, attributeFilter: ['hidden']});
}
const sizeObserver = new ResizeObserver(syncSystems);
mountPoints.forEach(stage => sizeObserver.observe(stage));
const themeObserver = new MutationObserver(() => {
  for (const system of systems.values()) system.setScene(root.dataset.theme === 'dark' ? 'ocean' : 'aurora');
});
themeObserver.observe(root, {attributes: true, attributeFilter: ['data-theme']});

const materialChoices = createComponent('choices', {
  label: '玻璃材质', value: material,
  options: [{value: 'thick', label: '厚润'}, {value: 'clear', label: '清透'}, {value: 'soft', label: '柔雾'}],
  onChange(value) {
    material = value;
    root.dataset.material = value;
    for (const system of systems.values()) system.setMaterial(materials[value]);
  },
});
materialChoices.element.classList.add('lab-material-choices');
// Controls outside a glass stage use the same semantic component with CSS material.
materialChoices.element.querySelectorAll('button').forEach(button => delete button.dataset.lgDrag);
const effectToggle = createComponent('button', {
  label: '光学效果：开',
  onPress() {
    enabled = !enabled;
    effectToggle.element.textContent = `光学效果：${enabled ? '开' : '关'}`;
    effectToggle.element.setAttribute('aria-pressed', String(enabled));
    root.dataset.glass = enabled ? 'on' : 'off';
    syncSystems();
  },
});
effectToggle.element.setAttribute('aria-pressed', 'true');
delete effectToggle.element.dataset.lgDrag;
const label = document.createElement('span');
label.className = 'lab-control-label'; label.textContent = '材质实验';
document.getElementById('glassLabControls').append(label, materialChoices.element, effectToggle.element);
components.push(materialChoices, effectToggle);
root.dataset.glass = 'on';

function pulse(button, keyboard = false) {
  if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true' || reduced.matches) return;
  if (button.matches('.head-actions button,.dialog-close')) return;
  if (button.closest('.recent-searches,.text-context-menu')) return;
  if (!keyboard && enabled && button.dataset.lgDrag && systems.has(button.closest('.lg-stage'))) return;
  // Ceramic controls keep their native clicks. Decorate only; never dispatch actions.
  feedback.get(button)?.cancel();
  const animation = button.animate([{scale: '1'}, {scale: '.955', offset: .24}, {scale: '1.012', offset: .7}, {scale: '1'}], {duration: 270, easing: 'cubic-bezier(.2,.8,.2,1)'});
  feedback.set(button, animation);
  animation.finished.catch(() => {}).finally(() => {if (feedback.get(button) === animation) feedback.delete(button);});
}
document.addEventListener('pointerdown', event => {
  if (event.button === 0 && event.isPrimary) pulse(event.target.closest?.('button'));
}, {signal, passive: true});
document.addEventListener('keydown', event => {
  if (!event.repeat && (event.key === 'Enter' || event.key === ' ')) pulse(event.target.closest?.('button'), true);
}, {signal});
reduced.addEventListener('change', () => {
  if (reduced.matches) for (const animation of feedback.values()) animation.cancel();
  for (const system of systems.values()) {
    system.jelly.intensity = reduced.matches ? 0 : .42;
    system.jelly.cancel(true);
  }
}, {signal});
document.addEventListener('visibilitychange', syncSystems, {signal});
contrast.addEventListener('change', syncSystems, {signal});
window.addEventListener('resize', syncSystems, {signal, passive: true});
window.addEventListener('pageshow', syncSystems, {signal});
window.addEventListener('pagehide', event => {
  for (const stage of mountPoints) unmount(stage);
  for (const animation of feedback.values()) animation.cancel();
  if (event.persisted) return;
  disposed = true;
  observer.disconnect(); selectionObserver.disconnect(); themeObserver.disconnect(); sizeObserver.disconnect(); tagObserver.disconnect();
  components.forEach(component => component.destroy());
  abort.abort();
}, {signal});
syncSystems();
