(() => {
  const select = document.querySelector('#owner-preview-person');
  if (!select) return;
  select.addEventListener('change', () => { window.location.assign(select.value); });
  select.disabled = false;
})();
