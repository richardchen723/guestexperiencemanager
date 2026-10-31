(() => {
  'use strict';
  const key = 'cotton-candy.owner.appearance';
  const root = document.documentElement;
  const normalize = value => value === 'light' || value === 'dark' ? value : 'system';

  function savedChoice() {
    try {
      return normalize(localStorage.getItem(key));
    } catch (_) {
      return 'system';
    }
  }

  function apply(value) {
    const choice = normalize(value);
    root.dataset.ownerAppearance = choice;
    const control = document.getElementById('owner-appearance');
    if (control) {
      control.value = choice;
      control.disabled = false;
    }
  }

  // Run before styles load so a saved choice is applied before the first paint.
  apply(savedChoice());

  function connectControl() {
    const control = document.getElementById('owner-appearance');
    if (!control) return;
    apply(root.dataset.ownerAppearance);
    control.addEventListener('change', () => {
      const choice = normalize(control.value);
      apply(choice);
      try {
        localStorage.setItem(key, choice);
      } catch (_) {
        // The current page still works when browser storage is unavailable.
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connectControl, {once: true});
  } else {
    connectControl();
  }

  // Keep other owner tabs, and pages restored with Back, in sync.
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(savedChoice());
  });
  window.addEventListener('pageshow', () => apply(savedChoice()));
})();
