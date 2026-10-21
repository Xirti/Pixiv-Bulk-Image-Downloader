/* Set the global palette before the stylesheet paints; storage is optional. */
(() => {
  const root = document.documentElement;
  let saved;
  try { saved = localStorage.getItem('moku-glass-lab-theme'); } catch { /* Private hosts may deny storage. */ }
  const systemDark = typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = saved === 'light' || saved === 'dark' ? saved : (systemDark ? 'dark' : 'light');

  function bindToggle() {
    const button = document.getElementById('themeToggle');
    if (!button) return;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const contrast = matchMedia('(forced-colors: active)');
    let busy = false, transition = null, sweep = null, sequence = 0;
    const label = () => {
      const dark = root.dataset.theme === 'dark';
      button.textContent = dark ? '☀ 日间' : '☾ 夜间';
      button.setAttribute('aria-label', dark ? '切换为日间界面' : '切换为夜间界面');
      button.setAttribute('title', button.textContent);
    };
    function finish(id) {
      if (id !== sequence) return;
      busy = false; transition = null; sweep = null;
      delete root.dataset.themeSweep;
    }
    function cancel() {
      sequence++;
      sweep?.cancel();
      transition?.skipTransition();
      busy = false; transition = null; sweep = null;
      delete root.dataset.themeSweep;
    }
    button.onclick = async () => {
      if (busy) return;
      const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      let committed = false;
      const commit = () => {
        if (committed) return;
        committed = true;
        root.dataset.theme = next;
        try { localStorage.setItem('moku-glass-lab-theme', next); } catch { /* Storage is optional. */ }
        label();
      };
      if (reduced.matches || contrast.matches || typeof document.startViewTransition !== 'function') {
        commit(); return;
      }
      const bounds = button.getBoundingClientRect();
      const x = bounds.left + bounds.width / 2, y = bounds.top + bounds.height / 2;
      const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      const center = 'px at ' + x + 'px ' + y + 'px)';
      const id = ++sequence;
      busy = true; root.dataset.themeSweep = 'active';
      try {
        transition = document.startViewTransition(commit);
        const current = transition;
        await current.ready;
        if (id !== sequence) return;
        sweep = root.animate([
          {clipPath: 'circle(0' + center},
          {clipPath: 'circle(' + radius + center},
        ], {duration: 720, easing: 'cubic-bezier(.3,0,.15,1)', pseudoElement: '::view-transition-new(root)'});
        sweep.finished.catch(() => {});
        await current.finished;
      } catch {
        commit();
        if (id === sequence) {sweep?.cancel(); transition?.skipTransition();}
      } finally {
        finish(id);
      }
    };
    document.addEventListener('visibilitychange', () => {if (document.hidden) cancel();});
    window.addEventListener('pagehide', cancel);
    reduced.addEventListener('change', cancel);
    contrast.addEventListener('change', cancel);
    label();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindToggle, {once: true});
  else bindToggle();
})();
