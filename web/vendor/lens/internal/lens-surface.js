/** Shared lens component body. The stage supplies one renderer and spring controller. */
export function createLensSurface({tag = 'div', className = '', kind = 'panel', motion = 'none'} = {}) {
  const element = document.createElement(tag);
  element.className = `lg-surface ${className}`.trim();
  element.dataset.glass = kind;
  element.dataset.lgLens = kind;
  if (motion !== 'none') element.dataset.lgDrag = motion;
  return {element, destroy() {}};
}
