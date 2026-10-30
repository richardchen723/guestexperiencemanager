(() => {
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (!token) return;
  // Remove the bearer token before navigation, telemetry, or Google sign-in.
  history.replaceState(null, '', location.pathname);
  const button = document.querySelector('#google-button');
  if (!button) return;
  button.disabled = true;
  const error = document.querySelector('#login-error');
  fetch(document.querySelector('meta[name="invitation-endpoint"]').content, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content},
    body: JSON.stringify({token})})
    .then(async response => {
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'This invitation is unavailable.');
      const status = document.querySelector('#invite-status');
      status.textContent = 'Your invitation is ready. Continue to sign in.';
      status.hidden = false;
      button.disabled = false;
    }).catch(reason => {error.textContent = reason.message; error.hidden = false;});
})();
