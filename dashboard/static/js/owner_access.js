(() => {
  'use strict';
  const root = document.querySelector('.owner-setup');
  const q = selector => root.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const csrf = document.querySelector('meta[name="owner-access-csrf"]').content;
  const dialog = q('#oa-dialog');
  let state, selectedId, dialogAccountId, chosen, propertyDraft, busy = false;
  const activeAccount = () => state.accounts.find(a => a.id === selectedId);
  const property = id => state.properties.find(p => p.id === id);
  const forAccount = (rows, id = selectedId) => rows.filter(row => row.account_id === id);
  const activeGrants = id => forAccount(state.grants, id).filter(row => !row.revoked);
  const activePeople = id => forAccount(state.memberships, id).filter(row => !row.revoked);
  const pending = row => !row.accepted && !row.revoked && Date.parse(row.expires_at) > Date.now();
  const initials = name => name.trim().split(/\s+/).slice(0, 2).map(word => Array.from(word)[0]).join('').toUpperCase();
  const plural = (n, singular, multiple = singular + 's') => `${n} ${n === 1 ? singular : multiple}`;
  const dateShift = (value, days) => {
    const date = new Date(value + 'T12:00:00Z');
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  };
  const dateLabel = value => new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'}).format(new Date(value + 'T12:00:00Z'));
  const historyLabel = row => `${dateLabel(row.from)} – ${row.until_exclusive ? dateLabel(dateShift(row.until_exclusive, -1)) : 'ongoing'}`;
  const permissions = row => [row.performance && 'Performance', row.statements && 'Finalized statements'].filter(Boolean).join(' · ');
  const footer = (label, back = false) => `<div class="oa-form-footer">${back ? '<button type="button" class="oa-back" data-back>← Back</button>' : '<button type="button" data-close>Cancel</button>'}<button class="oa-primary" type="submit">${label}</button></div>`;
  const badge = (text, style = '') => `<span class="oa-badge ${style}">${escape(text)}</span>`;

  async function api(path, data) {
    const response = await fetch(`/owner-access/api/${path}`, {credentials:'same-origin',
      ...(data ? {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify(data)} : {})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Could not complete this action. Reload the page and try again.');
    return result;
  }
  function showError(reason, inDialog = false) {
    const target = q(inDialog ? '#oa-dialog-error' : '#oa-error');
    target.textContent = reason.message;
    target.hidden = false;
  }
  function renderDirectory() {
    const search = q('#oa-search').value.trim().toLowerCase();
    const accounts = state.accounts.filter(a => a.name.toLowerCase().includes(search));
    q('#oa-owner-count').textContent = state.accounts.length;
    q('#oa-accounts').innerHTML = accounts.map(a => `<button type="button" class="oa-account" data-account="${a.id}" aria-current="${a.id === selectedId}">
      <span class="oa-avatar" aria-hidden="true">${escape(initials(a.name))}</span><span><strong>${escape(a.name)}</strong><small>${plural(activeGrants(a.id).length, 'home')} · ${plural(activePeople(a.id).length, 'person', 'people')}</small></span></button>`).join('') || '<p class="oa-no-results">No owners match your search.</p>';
  }
  function grantRow(row, historical = false) {
    const home = property(row.property_id);
    return `<div class="oa-row"><span class="oa-row-icon" aria-hidden="true">⌂</span><div class="oa-row-main"><strong>${escape(home?.name || 'Unavailable home')}</strong><p>${escape(home?.portfolio || 'Individual home')} · USD</p><small>Reporting: ${escape(historyLabel(row))}</small></div>
      <div class="oa-row-actions">${historical ? badge('Removed') : `<button type="button" data-edit="${row.id}">Edit dates</button><button type="button" class="oa-text" data-revoke="grant" data-id="${row.id}">Remove</button>`}</div></div>`;
  }
  function personRow(row, invitation = false, historical = false) {
    const status = invitation ? row.accepted ? 'Accepted' : row.revoked ? 'Cancelled' : pending(row) ? 'Invited' : 'Expired' : row.revoked ? 'Removed' : 'Has access';
    return `<div class="oa-row"><span class="oa-avatar" aria-hidden="true">${escape(initials(row.email))}</span><div class="oa-row-main"><strong>${escape(row.email)}</strong><p>${escape(permissions(row))}</p>${invitation && pending(row) ? `<small>Link expires ${escape(dateLabel(row.expires_at.slice(0, 10)))}</small>` : ''}</div>
      <div class="oa-row-actions">${badge(status, historical ? '' : invitation ? 'oa-pending' : 'oa-good')}${historical ? '' : invitation ? `<button type="button" class="oa-text" data-revoke="invitation" data-id="${row.id}">Cancel link</button>` : `<a class="oa-dashboard-link" href="/owner-access/preview/${row.account_id}/person/${row.id}/">View as owner ↗</a><button type="button" data-preview="${row.id}">Review access</button><button type="button" class="oa-text" data-revoke="membership" data-id="${row.id}">Remove</button>`}</div></div>`;
  }
  function renderDetail() {
    const account = activeAccount();
    if (!account) return;
    const grants = activeGrants(), people = activePeople(), invites = forAccount(state.invitations).filter(pending);
    const oldGrants = forAccount(state.grants).filter(row => row.revoked);
    const oldPeople = forAccount(state.memberships).filter(row => row.revoked);
    const oldInvites = forAccount(state.invitations).filter(row => !pending(row));
    const next = !grants.length ? 'Next, connect this owner’s first home.' : !people.length && !invites.length ? state.invitation_ready ? 'Homes are connected. Invite the people who need access.' : 'Homes are connected. You can invite people once sign-in is ready.' : '';
    q('#oa-detail').innerHTML = `<div class="oa-account-header"><span class="oa-avatar" aria-hidden="true">${escape(initials(account.name))}</span><div><h2>${escape(account.name)}</h2><p class="oa-muted">${plural(grants.length, 'home')} · ${plural(people.length, 'person', 'people')} with access${invites.length ? ` · ${invites.length} invited` : ''}</p></div><a class="oa-dashboard-link" href="/owner-access/preview/${account.id}/">View dashboard ↗</a>${badge(people.length ? 'Access enabled' : !grants.length ? 'Needs homes' : 'Ready for people', people.length ? 'oa-good' : '')}</div>
      ${next ? `<div class="oa-next"><strong>${escape(next)}</strong><span>Step ${grants.length ? '2' : '1'} of 2</span></div>` : ''}
      <section class="oa-panel" aria-labelledby="oa-homes-title"><div class="oa-panel-heading"><div><h3 id="oa-homes-title">Homes ${badge(grants.length)}</h3><p>Properties and reporting history shared with this account.</p></div><button type="button" data-open="property">＋ Add home</button></div>
        <div id="oa-grants">${grants.length ? grants.map(row => grantRow(row)).join('') : '<div class="oa-panel-empty"><span class="oa-symbol" aria-hidden="true">⌂</span><div><strong>No homes connected yet</strong><p>Add a home and choose how far back its reports can go.</p></div></div>'}</div>
        ${oldGrants.length ? `<details class="oa-history"><summary>Removed homes (${oldGrants.length})</summary>${oldGrants.map(row => grantRow(row, true)).join('')}</details>` : ''}</section>
      <section class="oa-panel" aria-labelledby="oa-people-title"><div class="oa-panel-heading"><div><h3 id="oa-people-title">People ${badge(people.length + invites.length)}</h3><p>Each person uses their own Google account to sign in.</p></div><button type="button" data-open="invite" ${state.invitation_ready ? '' : 'disabled title="Owner Google sign-in is not ready yet"'}>＋ Invite person</button></div>
        <div id="oa-memberships">${people.map(row => personRow(row)).join('')}</div><div id="oa-invitations">${invites.map(row => personRow(row, true)).join('')}</div>
        ${!people.length && !invites.length ? `<div class="oa-panel-empty"><span class="oa-symbol" aria-hidden="true">＋</span><div><strong>${state.invitation_ready ? 'No one has access yet' : 'Invitations will open when sign-in is ready'}</strong><p>${state.invitation_ready ? 'Invite an owner or their representative, and choose what they can see.' : 'Your setup is saved. No one can sign in until they accept an invitation.'}</p></div></div>` : ''}
        ${oldPeople.length + oldInvites.length ? `<details class="oa-history"><summary>Past access &amp; invitations (${oldPeople.length + oldInvites.length})</summary>${oldPeople.map(row => personRow(row, false, true)).join('')}${oldInvites.map(row => personRow(row, true, true)).join('')}</details>` : ''}</section>
      <p class="oa-privacy"><span aria-hidden="true">✓</span> Guest details and messages are never shared. Portfolio labels organize homes; they don’t grant access.</p>`;
  }
  async function load() {
    state = await api('setup');
    const requested = Number(location.hash.match(/^#owner=(\d+)$/)?.[1]);
    if (!state.accounts.some(a => a.id === selectedId)) selectedId = state.accounts.find(a => a.id === requested)?.id || state.accounts[0]?.id;
    q('#oa-launch').hidden = state.invitation_ready;
    q('#oa-loading').hidden = true;
    q('#oa-empty').hidden = !!state.accounts.length;
    q('#oa-workspace').hidden = !state.accounts.length;
    renderDirectory();
    renderDetail();
  }
  function openDialog(title, content, accountId = selectedId) {
    dialogAccountId = accountId;
    q('#oa-dialog-context').textContent = state.accounts.find(a => a.id === accountId)?.name || 'OWNER SETUP';
    q('#oa-dialog-title').textContent = title;
    q('#oa-dialog-error').hidden = true;
    q('#oa-dialog-body').innerHTML = content;
    if (!dialog.open) dialog.showModal();
    (q('#oa-dialog-body input:not([type=hidden])') || q('#oa-dialog-body button') || q('[data-close]')).focus();
  }
  function closeDialog() {
    if (busy) return;
    dialog.close();
    q('#oa-dialog-body').replaceChildren(); // Invitation tokens never linger in hidden UI.
  }
  function openAccount() {
    openDialog('Add an owner', `<p class="oa-dialog-copy">An account brings together an owner’s homes and the people allowed to see them.</p><form data-action="accounts"><label>Owner account name<input name="name" required maxlength="160" placeholder="e.g. Bennett household" autocomplete="off"><small>Use a household, company, or individual’s name.</small></label>${footer('Create owner')}</form>`, null);
  }
  function availableHomes() {
    const assigned = new Set(activeGrants(dialogAccountId).map(row => row.property_id));
    return [
      ...state.properties.filter(home => !assigned.has(home.id)).map(home => ({...home, key:'property:' + home.id, existing:true})),
      ...state.listings.map(home => ({...home, key:'listing:' + home.id, existing:false}))
    ];
  }
  function renderPicks() {
    const search = q('#oa-home-search').value.toLowerCase().trim();
    const homes = availableHomes().filter(home => `${home.name} ${home.id}`.toLowerCase().includes(search));
    q('#oa-home-picks').innerHTML = homes.map(home => `<label class="oa-pick"><input type="radio" name="home" value="${home.key}" ${chosen?.key === home.key ? 'checked' : ''}><span><strong>${escape(home.name)}</strong><small>${home.existing ? 'Already registered · reuse this home' : `Hostaway listing · ${home.id}`}</small></span></label>`).join('') || '<p class="oa-no-results">No available homes match. Try another name or listing ID.</p>';
    q('#oa-pick-next').disabled = !chosen;
  }
  function openHomePicker(reset = true) {
    if (reset) { chosen = null; propertyDraft = null; }
    openDialog('Choose a home', `<p class="oa-dialog-copy">Connect a registered home or start with a Hostaway listing.</p><form data-action="choose-home"><label>Find a home<input type="search" id="oa-home-search" placeholder="Search by name or listing ID…" autocomplete="off"></label><div class="oa-picks" id="oa-home-picks"></div><div class="oa-form-footer"><button type="button" data-close>Cancel</button><button type="submit" class="oa-primary" id="oa-pick-next" disabled>Continue →</button></div></form>`);
    renderPicks();
  }
  function timezoneOptions(value = '') {
    const common = {'America/New_York':'Eastern · New York', 'America/Chicago':'Central · Chicago', 'America/Denver':'Mountain · Denver', 'America/Phoenix':'Arizona · Phoenix', 'America/Los_Angeles':'Pacific · Los Angeles', 'Pacific/Honolulu':'Hawaii · Honolulu'};
    const option = (zone, name) => `<option value="${escape(zone)}" ${zone === value ? 'selected' : ''}>${escape(name)}</option>`;
    return '<option value="">Choose the home’s local timezone…</option><optgroup label="Common timezones">' + Object.entries(common).filter(([zone]) => state.timezones.includes(zone)).map(([zone, name]) => option(zone, name)).join('') + '</optgroup><optgroup label="All other timezones">' + state.timezones.filter(zone => !common[zone]).map(zone => option(zone, zone.replaceAll('_', ' '))).join('') + '</optgroup>';
  }
  function dateFields(from = '', end = '') {
    return `<div class="oa-fields"><label>Reporting starts on<input type="date" name="reporting_from" value="${escape(from)}" max="9999-12-30" required></label><label><span>Reporting ends on <small>· optional</small></span><input type="date" name="reporting_end" value="${escape(end)}" max="9999-12-30"></label></div><p class="oa-note">These dates control report history, not the invitation date. Include the prior year to show year-over-year comparisons. Start on the first of a month for full monthly reports; leave the end date blank for ongoing access.</p>`;
  }
  function openHomeDetails() {
    const draft = propertyDraft || {};
    const home = chosen;
    const others = state.listings.filter(row => row.id !== home.id);
    const shared = home.existing && state.grants.some(g => g.property_id === home.id && !g.revoked && g.account_id !== dialogAccountId);
    const fields = home.existing ? `<div class="oa-chosen"><span class="oa-row-icon" aria-hidden="true">⌂</span><div><strong>${escape(home.name)}</strong><small>${escape(home.timezone)} · USD</small></div></div>${shared ? '<p class="oa-note">This home is also connected to another owner account. Adding it here gives this account its own reporting access.</p>' : ''}` : `<p class="oa-note">Hostaway: ${escape(home.name)} · ${home.id}</p><label>Home name<input name="name" value="${escape(draft.name ?? home.name)}" required maxlength="160"><small>The name this owner will see.</small></label><label>Property timezone<select name="timezone" required>${timezoneOptions(draft.timezone ?? home.timezone)}</select><small>Confirm the home’s local timezone for accurate reporting dates.</small></label>`;
    const advanced = home.existing ? '' : `<details class="oa-advanced" ${draft.portfolio_label || draft.extra_listings?.length ? 'open' : ''}><summary>Portfolio &amp; additional listings</summary><div><label>Portfolio label <small>Optional · for organization only</small><input name="portfolio_label" value="${escape(draft.portfolio_label || '')}" maxlength="160" placeholder="e.g. Coastal homes"></label>${others.length ? `<p>Other listings for this same physical home only:</p><div class="oa-additional">${others.map(row => `<label class="oa-pick"><input type="checkbox" name="extra_listings" value="${row.id}" ${draft.extra_listings?.includes(String(row.id)) ? 'checked' : ''}><span>${escape(row.name)}<small>Hostaway · ${row.id}</small></span></label>`).join('')}</div>` : ''}</div></details><label class="oa-check"><input type="checkbox" name="single_unit_confirmed" required ${draft.single_unit_confirmed ? 'checked' : ''}><span>I’ve checked that the selected listings represent <strong>one physical home, with no bundles.</strong></span></label>`;
    openDialog('Connect home & set history', `<form data-action="${home.existing ? 'assign-home' : 'add-property'}">${fields}${dateFields(draft.reporting_from, draft.reporting_end)}${advanced}${footer('Connect home', true)}</form>`, dialogAccountId);
  }
  function openDates(id) {
    const grant = state.grants.find(row => row.id === id && row.account_id === selectedId);
    if (!grant) return;
    openDialog('Edit reporting dates', `<p class="oa-dialog-copy">${escape(property(grant.property_id)?.name)} · Changes apply to everyone with access to this account.</p><form data-action="grants" data-property="${grant.property_id}">${dateFields(grant.from, grant.until_exclusive ? dateShift(grant.until_exclusive, -1) : '')}${footer('Save dates')}</form>`);
  }
  function openInvite() {
    if (!state.invitation_ready) return;
    openDialog('Invite a person', `<p class="oa-dialog-copy">They’ll see the homes connected to this owner account. Choose which information they can access.</p><form data-action="invitations"><label>Google account email<input name="email" type="email" required maxlength="320" placeholder="owner@example.com" autocomplete="off"></label><div class="oa-permissions"><label class="oa-check"><input type="checkbox" name="performance" checked><span><strong>Property performance</strong>Occupancy, revenue estimates, and comparisons.</span></label><label class="oa-check"><input type="checkbox" name="statements"><span><strong>Finalized owner statements</strong>Published statements only.</span></label></div><p class="oa-note">You’ll get a link to share. No email is sent automatically. A new link replaces any pending invitation for this email and account.</p>${footer('Create invitation link')}</form>`);
  }
  function openRevoke(kind, id) {
    const rows = kind === 'grant' ? state.grants : kind === 'membership' ? state.memberships : state.invitations;
    const row = rows.find(item => item.id === id && item.account_id === selectedId);
    if (!row) return;
    const title = kind === 'grant' ? 'Remove this home?' : kind === 'membership' ? 'Remove this person?' : 'Cancel this invitation?';
    const subject = kind === 'grant' ? property(row.property_id)?.name : row.email;
    const copy = kind === 'grant' ? 'Everyone in this owner account will lose access to this home. Other owner accounts are unaffected. You can reconnect it later.' : kind === 'membership' ? 'This person will lose access to this owner account immediately. Their active sessions and pending invitations for this account will be revoked.' : 'This link will stop working immediately. You can create a new invitation later.';
    openDialog(title, `<p class="oa-dialog-copy"><strong>${escape(subject)}</strong></p><p>${copy}</p><form data-action="revoke" data-kind="${kind}" data-id="${id}"><div class="oa-form-footer"><button type="button" data-close>Keep ${kind === 'grant' ? 'home' : kind === 'membership' ? 'access' : 'invitation'}</button><button type="submit" class="oa-danger">${kind === 'invitation' ? 'Cancel invitation' : 'Remove access'}</button></div></form>`);
  }
  function formData(form) {
    const fields = new FormData(form), data = Object.fromEntries(fields);
    data.account_id = dialogAccountId;
    if (fields.has('reporting_from')) {
      data.reporting_until_exclusive = data.reporting_end ? dateShift(data.reporting_end, 1) : null;
      if (data.reporting_end && data.reporting_end < data.reporting_from) throw new Error('The end date must be on or after the start date.');
      delete data.reporting_end;
    }
    if (form.dataset.action === 'add-property') {
      data.listing_ids = [chosen.id, ...fields.getAll('extra_listings').map(Number)];
      data.single_unit_confirmed = fields.has('single_unit_confirmed');
      delete data.extra_listings;
    }
    if (form.dataset.action === 'assign-home') data.property_id = chosen.id;
    if (form.dataset.action === 'grants') data.property_id = Number(form.dataset.property);
    if (form.dataset.action === 'invitations') {
      data.performance = fields.has('performance'); data.statements = fields.has('statements');
      if (!data.performance && !data.statements) throw new Error('Choose at least one type of information to share.');
    }
    if (form.dataset.action === 'revoke') return {kind:form.dataset.kind, id:Number(form.dataset.id)};
    return data;
  }
  root.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-action]');
    if (!form) return;
    event.preventDefault();
    if (busy) return;
    if (form.dataset.action === 'choose-home') { if (chosen) openHomeDetails(); return; }
    q('#oa-dialog-error').hidden = true;
    let data;
    try { data = formData(form); } catch (error) { showError(error, true); return; }
    busy = true;
    const buttons = [...dialog.querySelectorAll('button')];
    buttons.forEach(button => {button.disabled = true;});
    dialog.setAttribute('aria-busy', 'true');
    try {
      const action = form.dataset.action === 'assign-home' ? 'grants' : form.dataset.action;
      const result = await api(action, data);
      if (action === 'accounts') { selectedId = result.id; q('#oa-search').value = ''; history.replaceState(null, '', '#owner=' + selectedId); }
      const message = {accounts:'Owner created. Next, connect their first home.', 'add-property':'Home connected. Reporting access is saved.', grants:'Reporting access saved.', invitations:'Invitation created. Copy the link to share it.', revoke:'Access updated.'}[action];
      q('#oa-status').textContent = message;
      q('#oa-error').hidden = true;
      // The write succeeded. A refresh failure must never offer to submit it again.
      busy = false;
      closeDialog();
      try { await load(); } catch (error) { showError(new Error('Your change was saved, but the list could not refresh. Reload this page.')); }
      if (result.url) {
        openDialog('Your invitation link is ready', `<div class="oa-success-icon" aria-hidden="true">✓</div><p class="oa-dialog-copy">Share this link with <strong>${escape(data.email)}</strong>. It expires in 7 days. No email has been sent.</p><label>Invitation link<input id="oa-link" value="${escape(result.url)}" readonly></label><p class="oa-note" style="margin-top:15px">Copy it before closing. For security, this link is shown only once.</p><div class="oa-form-footer"><button type="button" data-close>Done</button><button type="button" class="oa-primary" id="oa-copy">Copy link</button></div>`, data.account_id);
      } else {
        (q(`[data-account="${selectedId}"]`) || q('[data-open="account"]')).focus();
      }
    } catch (error) { showError(error, true); }
    finally { busy = false; buttons.forEach(button => {button.disabled = false;}); dialog.removeAttribute('aria-busy'); }
  });
  root.addEventListener('input', event => {
    if (event.target.id === 'oa-search') renderDirectory();
    if (event.target.id === 'oa-home-search') renderPicks();
  });
  root.addEventListener('change', event => {
    if (event.target.name === 'home') {
      if (chosen?.key !== event.target.value) propertyDraft = null;
      chosen = availableHomes().find(home => home.key === event.target.value);
      q('#oa-pick-next').disabled = !chosen;
    }
  });
  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || button.type === 'submit' || busy) return;
    if (button.hasAttribute('data-close')) { closeDialog(); return; }
    if (button.hasAttribute('data-back')) {
      const fields = new FormData(button.closest('form'));
      propertyDraft = {...Object.fromEntries(fields), extra_listings:fields.getAll('extra_listings'), single_unit_confirmed:fields.has('single_unit_confirmed')};
      openHomePicker(false); return;
    }
    if (button.dataset.account) {
      selectedId = Number(button.dataset.account);
      history.replaceState(null, '', '#owner=' + selectedId);
      q('#oa-status').textContent = '';
      renderDirectory(); renderDetail(); q(`[data-account="${selectedId}"]`).focus(); return;
    }
    if (button.dataset.open === 'account') { if (state) openAccount(); return; }
    if (button.dataset.open === 'property') { openHomePicker(); return; }
    if (button.dataset.open === 'invite') { openInvite(); return; }
    if (button.dataset.edit) { openDates(Number(button.dataset.edit)); return; }
    if (button.dataset.revoke) { openRevoke(button.dataset.revoke, Number(button.dataset.id)); return; }
    if (button.id === 'oa-copy') {
      try { await navigator.clipboard.writeText(q('#oa-link').value); button.textContent = 'Copied ✓'; }
      catch { q('#oa-link').select(); button.textContent = 'Select & copy link'; }
      return;
    }
    if (button.dataset.preview) {
      button.disabled = true;
      try {
        const result = await api('preview', {membership_id:Number(button.dataset.preview)});
        openDialog('Review their access', result.accounts.map(account => `<p class="oa-dialog-copy">${escape(permissions(account.permissions))}</p><div class="oa-review-list">${account.properties.map(home => `<div><strong>${escape(home.name)}</strong><small>${escape(historyLabel({from:home.reporting_from, until_exclusive:home.reporting_until_exclusive}))}</small></div>`).join('') || '<p>No homes connected.</p>'}</div>`).join('') + '<p class="oa-note">This is an access summary. No owner session is created.</p><div class="oa-form-footer"><button type="button" data-close>Done</button></div>');
      } catch (error) { showError(error); }
      finally { button.disabled = false; }
    }
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });
  load().catch(error => { q('#oa-loading').hidden = true; showError(error); });
})();
