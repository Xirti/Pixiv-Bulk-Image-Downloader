/* Set the global palette before the stylesheet paints; storage is optional. */
(() => {
  const root = document.documentElement;
  let saved;
  try { saved = localStorage.getItem('moku-theme'); } catch { /* Private hosts may deny storage. */ }
  const systemDark = typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = saved === 'light' || saved === 'dark' ? saved : (systemDark ? 'dark' : 'light');

  function bindToggle() {
    const button = document.getElementById('themeToggle');
    if (!button) return;
    const label = () => {
      const dark = root.dataset.theme === 'dark';
      button.textContent = dark ? '☀ 日间' : '☾ 夜间';
      button.setAttribute('aria-label', dark ? '切换为日间界面' : '切换为夜间界面');
      button.setAttribute('title', button.textContent);
    };
    button.onclick = () => {
      root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem('moku-theme', root.dataset.theme); } catch { /* The current page still switches. */ }
      label();
    };
    label();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindToggle, {once: true});
  else bindToggle();
})();
