(() => {
  const preferenceKey = 'tg-mfg-theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const modes = ['system', 'light', 'dark'];
  let mode = 'system';
  try {
    const saved = localStorage.getItem(preferenceKey);
    if (modes.includes(saved)) mode = saved;
  } catch { /* A blocked preference store does not prevent using the app. */ }

  function apply() {
    document.documentElement.dataset.theme = mode === 'system' ? (system.matches ? 'dark' : 'light') : mode;
    document.documentElement.dataset.themeMode = mode;
    const selector = document.getElementById('themeSelect');
    if (selector) selector.value = mode;
  }
  apply();
  system.addEventListener('change', () => {if (mode === 'system') apply();});
  window.addEventListener('storage', event => {
    if (event.key === preferenceKey || event.key === null) {
      mode = modes.includes(event.newValue) ? event.newValue : 'system'; apply();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.getElementById('themeSelect').addEventListener('change', event => {
      mode = modes.includes(event.target.value) ? event.target.value : 'system';
      try {localStorage.setItem(preferenceKey, mode);} catch { /* Preference is optional. */ }
      apply();
    });
  });
})();
