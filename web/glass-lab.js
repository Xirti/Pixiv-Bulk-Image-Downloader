/* Experimental presentation adapter. Original DOM identities and actions stay owned by app.js. */
import {createComponent, createGlassSystem} from './vendor/lens/index.js';
import {enhanceSelect} from './glass-select.js';

const root = document.documentElement;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const systems = new Map();
const components = [];
const abort = new AbortController();
const {signal} = abort;
const feedback = new Map();
let material = 'thick';
let enabled = true;
let disposed = false;

function drawArtwork(ctx, {width, height, dpr}) {
  const dark = root.dataset.theme === 'dark';
  ctx.fillStyle = dark ? '#101d2c' : '#e5eff8';
  ctx.fillRect(0, 0, width, height);
  const glow = ctx.createRadialGradient(width * .18, height * .05, 0, width * .18, height * .05, Math.max(width, height) * .85);
  glow.addColorStop(0, dark ? '#26566c' : '#cee8f8');
  glow.addColorStop(.65, dark ? '#142739' : '#eaf0fa');
  glow.addColorStop(1, dark ? '#101d2c' : '#e5eff8');
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, width, height);
  const step = 24 * dpr;
  ctx.fillStyle = dark ? '#acd8e332' : '#54758a36';
  for (let x = step / 2; x < width; x += step) {
    for (let y = step / 2; y < height; y += step) {
      ctx.beginPath(); ctx.arc(x, y, .85 * dpr, 0, Math.PI * 2); ctx.fill();
    }
  }
}

function enhanceButton(button) {
  button.classList.add('lg-surface');
  button.dataset.glass = 'button';
  // Press mode leaves native clicks, input focus and touch scrolling in control.
  button.dataset.lgDrag = 'press';
}

const searchStage = document.querySelector('.search-panel');
const searchPanel = createComponent('panel', {
  label: '搜索与筛选', motion: 'none', children: [...searchStage.childNodes],
});
searchPanel.element.classList.add('lab-search-body');
searchStage.append(searchPanel.element);
components.push(searchPanel);
for (const id of ['workType', 'safety', 'datePreset', 'quality', 'format']) {
  components.push(enhanceSelect(document.getElementById(id)));
}
searchStage.querySelectorAll('.ai-choice,.fuzzy-choice').forEach(tile => {
  tile.classList.add('lg-surface', 'lab-check-tile'); tile.dataset.glass = 'chip';
});
searchStage.querySelectorAll('button').forEach(enhanceButton);

const selectionStage = document.getElementById('selectionBar');
// The app owns this node's hidden state and text; only its existing controls are enhanced.
selectionStage.querySelectorAll('button').forEach(enhanceButton);

const mountPoints = [searchStage, selectionStage];
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
function visible(stage) {
  if (!stage.getClientRects().length || document.hidden) return false;
  const r = stage.getBoundingClientRect();
  return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
}

function syncSystems() {
  if (disposed) return;
  for (const stage of mountPoints) {
    const active = enabled && visible(stage);
    if (!active && systems.has(stage)) {
      unmount(stage);
    } else if (active && !systems.has(stage)) {
      const system = createGlassSystem(stage, {
        material, scene: root.dataset.theme === 'dark' ? 'ocean' : 'aurora',
        artwork: drawArtwork, motion: false, elasticity: reduced.matches ? 0 : .42,
        audio: {enabled: false},
      });
      systems.set(stage, system);
    }
  }
}

const observer = new IntersectionObserver(syncSystems);
mountPoints.forEach(stage => observer.observe(stage));
const selectionObserver = new MutationObserver(syncSystems);
selectionObserver.observe(selectionStage, {attributes: true, attributeFilter: ['hidden']});
const themeObserver = new MutationObserver(() => {
  for (const system of systems.values()) system.setScene(root.dataset.theme === 'dark' ? 'ocean' : 'aurora');
});
themeObserver.observe(root, {attributes: true, attributeFilter: ['data-theme']});

const materialChoices = createComponent('choices', {
  label: '玻璃材质', value: material,
  options: [{value: 'thick', label: '厚润'}, {value: 'clear', label: '清透'}, {value: 'soft', label: '柔雾'}],
  onChange(value) {
    material = value;
    for (const system of systems.values()) system.setMaterial(value);
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
  // Lens supplies the optical press inside its stages. Elsewhere only decorate; never dispatch actions.
  if (button.closest('.lg-stage') && enabled && !keyboard) return;
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
window.addEventListener('resize', syncSystems, {signal, passive: true});
window.addEventListener('pageshow', syncSystems, {signal});
window.addEventListener('pagehide', event => {
  for (const stage of mountPoints) unmount(stage);
  for (const animation of feedback.values()) animation.cancel();
  if (event.persisted) return;
  disposed = true;
  observer.disconnect(); selectionObserver.disconnect(); themeObserver.disconnect();
  components.forEach(component => component.destroy());
  abort.abort();
}, {signal});
syncSystems();
