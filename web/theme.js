/* The initial preference comes from the local application, across HTTP ports. */
(() => {
  const root = document.documentElement;
  const system = matchMedia('(prefers-color-scheme: dark)');
  let mode = root.dataset.themeMode || 'system';
  let save, button, state;

  function apply() {
    const dark = mode === 'dark' || (mode === 'system' && system.matches);
    root.dataset.theme = dark ? 'dark' : 'light';
    root.dataset.themeMode = mode;
    if (!button) return;
    state.textContent = dark ? '夜' : '日';
    button.setAttribute('aria-pressed', String(dark));
    const target = dark ? '切换为日间界面' : '切换为夜间界面';
    const label = mode === 'system' ? target + '（当前跟随系统）' : target;
    button.setAttribute('aria-label', label);
    button.title = label;
  }

  function bindToggle() {
    button = document.getElementById('themeToggle');
    if (!button) return;
    button.innerHTML = '<span class="theme-orb" aria-hidden="true"><span class="theme-sun">☀</span><span class="theme-moon">☾</span></span><span class="theme-state" aria-hidden="true"></span>';
    state = button.querySelector('.theme-state');
    button.onclick = async () => {
      if (!save || button.disabled) return;
      button.disabled = true;
      try {
        await save(root.dataset.theme === 'dark' ? 'light' : 'dark');
      } finally {
        button.disabled = false;
      }
    };
    apply();
  }

  globalThis.mokuTheme = {
    get mode() { return mode; },
    setMode(next) {
      if (['system', 'light', 'dark'].includes(next)) {
        mode = next;
        apply();
      }
    },
    bindSave(handler) { save = handler; },
  };
  apply();
  system.addEventListener('change', apply);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindToggle, {once: true});
  else bindToggle();
})();
