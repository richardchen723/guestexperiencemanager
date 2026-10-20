/* Guest inquiry spreadsheet and side panel. Source content is always escaped. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  if (!$('inquiryApp')) return;
  const testView = new URLSearchParams(window.location.search).get('record_scope') === 'internal-tests';
  const state = {bootstrap: null, rows: [], page: 1, total: 0, attention: '', sort: 'received_at', direction: 'desc', detail: null, dirty: false, panel: false, loading: false, request: 0, editing: false, returnFocus: null};
  const csrf = document.querySelector('meta[name="inquiries-csrf"]').content;
  const escape = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const option = (value, label, selected) => `<option value="${escape(value)}"${String(value ?? '') === String(selected ?? '') ? ' selected' : ''}>${escape(label)}</option>`;
  const tz = () => state.bootstrap.settings.timezone;
  const formatTime = (value) => value ? new Intl.DateTimeFormat('en-US', {timeZone: tz(), month:'short', day:'numeric', year:'numeric', hour:'numeric', minute:'2-digit', timeZoneName:'short'}).format(new Date(value)) : '—';
  const formatDay = value => value ? new Intl.DateTimeFormat('en-US', {timeZone:'UTC', month:'short', day:'numeric', year:'numeric'}).format(new Date(value + 'T12:00:00Z')) : '—';
  function localParts(value) {
    return Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: tz(), year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).formatToParts(new Date(value)).filter(v => v.type !== 'literal').map(v => [v.type, v.value]));
  }
  function localInput(value) { if (!value) return ''; const p = localParts(value); return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`; }
  function toUTC(value) {
    if (!value) return null;
    let guess = new Date(value + 'Z').getTime();
    const target = guess;
    for (let i = 0; i < 3; i++) {
      const represented = new Date(localInput(guess) + 'Z').getTime();
      guess += target - represented;
    }
    if (localInput(guess) !== value) throw new Error('This local time does not exist because the clocks change. Choose another time.');
    if ([guess - 3600000, guess + 3600000].some(v => localInput(v) === value)) throw new Error('This time occurs twice when the clocks change. Choose a time outside that repeated hour.');
    return new Date(guess).toISOString();
  }
  async function api(path, method = 'GET', data) {
    const response = await fetch('/inquiries/api/' + path, {method, headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, ...(data === undefined ? {} : {body:JSON.stringify(data)})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Could not load inquiries. Try again.');
    return result;
  }
  function error(message, panel = false) { const el = $(panel ? 'iqPanelError' : 'iqError'); el.textContent = message; el.hidden = !message; }
  function notice(message) { $('iqNotice').textContent = message; $('iqNotice').hidden = !message; }
  function dateFilters() {
    const mode = $('iqDateRange').value;
    $('iqCustomDates').hidden = mode !== 'custom';
    if (mode === 'custom') return {from:$('iqFrom').value, to:$('iqTo').value};
    if (mode === 'all') return {};
    const localDay = localInput(new Date()).slice(0,10);
    const day = new Date(localDay + 'T12:00:00Z');
    if (mode === 'yesterday') day.setUTCDate(day.getUTCDate() - 1);
    const end = day.toISOString().slice(0,10);
    if (['7','30'].includes(mode)) day.setUTCDate(day.getUTCDate() - Number(mode) + 1);
    return {from: day.toISOString().slice(0,10), to:end};
  }
  function query() {
    const values = {record_scope:testView ? 'internal-tests' : 'genuine', page:state.page, sort:state.sort, direction:state.direction, search:$('iqSearch').value, status:$('iqStatus').value,
      listing_id:$('iqProperty').value, tag_id:$('iqTag').value,
      assigned_user_id:$('iqAgent').value, attention:state.attention, ...dateFilters()};
    return new URLSearchParams(Object.entries(values).filter(([,v]) => v !== '' && v != null));
  }
  function updatePropertyOptions() {
    const tag = $('iqTag').value, selected = $('iqProperty').value;
    const properties = state.bootstrap.properties.filter(p => !tag || p.tag_ids.some(id => String(id) === tag));
    const selection = properties.some(p => String(p.id) === selected) ? selected : '';
    const placeholder = tag && !properties.length ? 'No properties in this tag' : tag ? 'All properties in tag' : 'All properties';
    $('iqProperty').innerHTML = option('',placeholder,selection) + properties.map(p => option(p.id,p.name,selection)).join('');
    $('iqProperty').disabled = Boolean(tag && !properties.length);
  }
  function renderStats(stats) {
    $('iqStats').innerHTML = [['Total leads',stats.total],['New leads',stats.new],['Follow-up required',stats.follow_up],['Confirmed bookings',stats.booked],['Conversion rate',stats.conversion_rate == null ? '—' : `${stats.conversion_rate}%`]].map(([name,value]) => `<div class="iq-stat"><span>${name}</span><strong>${value}</strong>${name === 'Conversion rate' ? '<small>Of completed leads</small>' : ''}</div>`).join('');
  }
  const agentOptions = selected => option('', 'Unassigned', selected) + state.bootstrap.users.map(u => option(u.id,u.name,selected)).join('');
  const statusOptions = selected => state.bootstrap.statuses.map(s => option(s,s,selected)).join('');
  function badges(lead) {
    return `${lead.is_test ? '<span class="iq-badge">Internal test · excluded from statistics</span>' : ''}${lead.needs_reply ? '<span class="iq-badge reply">New response</span>' : ''}${lead.overdue ? '<span class="iq-badge overdue">Overdue</span>' : ''}${lead.escalated ? '<span class="iq-badge overdue">Escalated to team</span>' : ''}${lead.reminder && !lead.overdue ? '<span class="iq-badge">Follow-up soon</span>' : ''}${lead.unassigned_alert ? '<span class="iq-badge overdue">Assignment overdue</span>' : ''}${lead.review_flags.length ? '<span class="iq-badge">Review needed</span>' : ''}`;
  }
  function renderRows() {
    $('iqRows').innerHTML = state.rows.length ? state.rows.map(l => `<tr data-id="${l.id}">
      <td>${escape(formatTime(l.received_at))}<span class="iq-sub">${escape(l.reference || 'No reference')}</span></td>
      <td><strong>${escape(l.guest_name || 'Name not supplied')}</strong>${badges(l)}${l.unassigned ? `<span class="iq-sub">Unassigned · ${Math.floor(l.age_minutes / 60)}h old</span>` : ''}</td>
      <td>${l.phone ? `<a href="tel:${escape(l.phone.replace(/[^+\d]/g,''))}">${escape(l.phone)}</a>` : '—'}</td>
      <td>${l.email ? `<a href="mailto:${escape(l.email)}">${escape(l.email)}</a>` : '—'}</td>
      <td>${escape(l.property_label)}</td><td>${escape(formatDay(l.check_in))}</td><td>${escape(formatDay(l.check_out))}</td>
      <td><span class="iq-preview" title="Open the full inquiry to read the original message">${escape(l.details || l.original_notes)}</span></td>
      <td><button type="button" class="iq-note iq-preview" data-note="${l.id}" aria-label="Edit internal notes for ${escape(l.guest_name || 'inquiry')}">${escape(l.internal_notes || 'Add a note…')}</button></td>
      <td><select data-inline="assigned_user_id" aria-label="Assign ${escape(l.guest_name || 'inquiry')}">${agentOptions(l.assigned_user_id)}</select></td>
      <td><select data-inline="status" aria-label="Status for ${escape(l.guest_name || 'inquiry')}">${statusOptions(l.status)}</select></td>
      <td><button class="iq-button" type="button" data-open="${l.id}" aria-label="Expand ${escape(l.guest_name || 'inquiry')}">↗</button></td></tr>`).join('') : '<tr><td colspan="12" class="iq-empty">No inquiries match these filters.</td></tr>';
    $('iqCount').textContent = `${state.total} ${state.total === 1 ? 'inquiry' : 'inquiries'}`;
    $('iqPage').textContent = `Page ${state.page} of ${Math.max(1,Math.ceil(state.total / 30))}`;
    $('iqPrevious').disabled = state.page <= 1;
    $('iqNext').disabled = state.page * 30 >= state.total;
  }
  async function load({quiet = false} = {}) {
    const serial = ++state.request;
    try {
      if (!quiet) error('');
      const result = await api('leads?' + query());
      if (serial !== state.request) return;
      state.rows = result.leads; state.total = result.total;
      renderRows(); renderStats(result.stats);
    } catch (e) { error(e.message); if (!state.rows.length) $('iqRows').innerHTML = '<tr><td colspan="12" class="iq-empty">Inquiries could not load. Use Refresh to try again.</td></tr>'; }
  }
  function showPanel(title, reference) {
    state.returnFocus = document.activeElement;
    state.panel = true;
    $('iqPanelTitle').textContent = title;
    $('iqPanelReference').textContent = reference || '';
    $('iqPanel').hidden = $('iqBackdrop').hidden = false;
    $('iqPanel').focus();
    document.body.style.overflow = 'hidden';
    error('', true);
  }
  function closePanel() {
    if (state.dirty && !window.confirm('Discard your unsaved changes?')) return;
    state.panel = false; state.detail = null; state.dirty = false;
    $('iqPanel').hidden = $('iqBackdrop').hidden = true;
    document.body.style.overflow = '';
    state.returnFocus?.focus();
  }
  function field(label, name, value, type = 'text', full = false) {
    const input = type === 'textarea' ? `<textarea name="${name}">${escape(value)}</textarea>` : `<input name="${name}" type="${type}" value="${escape(value)}"${type === 'number' ? ' min="0" max="999"' : ''}>`;
    return `<label${full ? ' class="iq-full"' : ''}>${escape(label)}${input}</label>`;
  }
  function selectField(label, name, options) { return `<label>${label}<select name="${name}">${options}</select></label>`; }
  function renderDetail(l) {
    const q = l.qualification || {};
    const prop = l.listing_id ? `listing:${l.listing_id}` : l.tag_id ? `tag:${l.tag_id}` : '';
    const propOptions = option('', 'Unassigned', prop) + '<optgroup label="Properties">' + state.bootstrap.properties.map(p => option(`listing:${p.id}`,p.name,prop)).join('') + '</optgroup><optgroup label="Tags">' + state.bootstrap.tags.map(t => option(`tag:${t.id}`,t.name,prop)).join('') + '</optgroup>';
    $('iqPanelBody').innerHTML = `${badges(l)}<p class="iq-muted">Received ${escape(formatTime(l.received_at))} · ${escape(l.brand || l.source)}</p>
      <p class="iq-contact-links">${l.email ? `<a href="mailto:${escape(l.email)}">Email guest ↗</a>` : ''}${l.phone ? `<a href="tel:${escape(l.phone.replace(/[^+\d]/g,''))}">Call guest ↗</a>` : ''}</p>
      <form id="iqDetailForm"><h3>Guest information</h3><div class="iq-form-grid">
      ${field('Guest name','guest_name',l.guest_name)}${field('Email address','email',l.email,'email')}${field('Phone number','phone',l.phone,'tel')}${field('Preferred contact method','preferred_contact_method',q.preferred_contact_method)}
      </div><h3>Stay & qualification</h3><div class="iq-form-grid">${selectField('Property / tag','property',propOptions)}${field('Property as requested','property_text',l.property_text)}${field('Check-in','check_in',l.check_in,'date')}${field('Checkout','check_out',l.check_out,'date')}${field('Trip type','trip_type',l.trip_type)}${field('Guest count','guest_count',q.guest_count,'number')}${field('Adults','adults',q.adults,'number')}${field('Children','children',q.children,'number')}${field('Pets','pets',q.pets)}${field('Budget as supplied (include currency)','budget',q.budget)}${field('Flexible dates','date_flexibility',q.date_flexibility)}${field('Alternative dates','alternative_dates',q.alternative_dates)}${field('Stay requirements','stay_requirements',q.stay_requirements,'textarea',true)}${field('Current inquiry details','details',l.details,'textarea',true)}${field('Quote status','quote_status',q.quote_status)}
      </div><h3>Internal management</h3><div class="iq-form-grid">${selectField('Assigned agent','assigned_user_id',agentOptions(l.assigned_user_id))}${selectField('Lead status','status',statusOptions(l.status))}${field('Next action','next_action',l.next_action,'textarea',true)}${field(`Follow-up due · ${tz()}`,'follow_up_at',localInput(l.follow_up_at),'datetime-local')}${field(`Last contact · ${tz()}`,'last_contact_at',localInput(l.last_contact_at),'datetime-local')}${field('Internal notes','internal_notes',l.internal_notes,'textarea',true)}</div>
      ${l.needs_reply ? '<label class="iq-check"><input type="checkbox" name="acknowledge_reply"> I reviewed this new response and the next action</label>' : ''}
      ${l.review_flags.length ? `<h3>Review flags</h3>${l.review_flags.map(f => `<div class="iq-alert">${escape(f.field ? f.field.replaceAll('_',' ') + ': ' : '')}${escape(f.reason)}${'proposed' in f ? `<br>Suggested: ${escape(f.proposed ?? 'Blank')}<br>Retained: ${escape(f.current ?? 'Blank')}` : ''}</div>`).join('')}<label class="iq-check"><input type="checkbox" name="resolve_flags"> I resolved the flagged information</label>` : ''}
      <div class="iq-form-footer"><button class="iq-button iq-primary" type="submit">Save changes</button><span class="iq-muted" id="iqSaveState">Saved changes are visible to the team.</span></div></form>
      <h3>Original inquiry & conversation</h3>${l.messages.map((m,i) => `<details class="iq-message"${i === l.messages.length - 1 ? ' open' : ''}><summary><strong>${escape(m.subject)}</strong><br>${escape(m.sender)} · ${escape(formatTime(m.received_at))}</summary><pre>${escape(m.body)}</pre><a href="${escape(m.source_url)}" target="_blank" rel="noopener noreferrer">Open source email ↗</a></details>`).join('')}
      <h3>Activity history</h3><ol class="iq-history">${l.activities.map(a => `<li><strong>${escape(a.action)}</strong><small>${escape(a.actor)} · ${escape(formatTime(a.created_at))}</small>${Object.entries(a.changes).map(([k,v]) => `<div class="iq-change">${escape(k.replaceAll('_',' '))}: ${escape(v && typeof v === 'object' && 'after' in v ? `${typeof v.before === 'object' ? JSON.stringify(v.before) : v.before ?? 'Blank'} → ${typeof v.after === 'object' ? JSON.stringify(v.after) : v.after ?? 'Blank'}` : v)}</div>`).join('')}</li>`).join('')}</ol>`;
    $('iqDetailForm').addEventListener('input', () => { state.dirty = true; $('iqSaveState').textContent = 'Unsaved changes'; });
    $('iqDetailForm').addEventListener('change', () => { state.dirty = true; });
    $('iqDetailForm').addEventListener('submit', saveDetail);
  }
  async function openDetail(id, initialStatus) {
    if (state.panel && state.dirty && !window.confirm('Discard your unsaved changes?')) return;
    showPanel('Inquiry details', 'Loading…');
    $('iqPanelBody').textContent = 'Loading inquiry…';
    state.dirty = false;
    try {
      const l = await api('leads/' + id);
      state.detail = l;
      $('iqPanelTitle').textContent = l.guest_name || 'Inquiry details';
      $('iqPanelReference').textContent = l.reference || `INQUIRY ${l.id}`;
      renderDetail(l);
      if (initialStatus) { $('iqDetailForm').elements.status.value = initialStatus; state.dirty = true; }
    } catch (e) { error(e.message, true); }
  }
  function formChanges(form, lead) {
    const result = {version:lead.version};
    for (const [key,raw] of new FormData(form)) {
      if (key === 'property') {
        const [type, id] = raw.split(':');
        const listing = type === 'listing' ? Number(id) : null, tag = type === 'tag' ? Number(id) : null;
        if (listing !== lead.listing_id || tag !== lead.tag_id) Object.assign(result,{listing_id:listing,tag_id:tag});
        continue;
      }
      if (['acknowledge_reply','resolve_flags'].includes(key)) { result[key] = true; continue; }
      const numeric = ['assigned_user_id','guest_count','adults','children'].includes(key);
      const datetime = ['follow_up_at','last_contact_at'].includes(key);
      const previous = Object.hasOwn(lead.qualification,key) ? lead.qualification[key] : lead[key];
      // Compare displayed minute precision before converting; do not rewrite seconds.
      if (datetime && raw === localInput(previous)) continue;
      const value = datetime ? toUTC(raw) : raw === '' ? null : numeric ? Number(raw) : raw;
      if ((previous ?? null) !== value) result[key] = value;
    }
    return result;
  }
  async function saveDetail(event) {
    event.preventDefault(); error('',true);
    const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
    try {
      const saved = await api(`leads/${state.detail.id}`,'PATCH',formChanges(event.target,state.detail));
      state.detail = saved; state.dirty = false; renderDetail(saved);
      $('iqSaveState').textContent = 'Changes saved';
      await load({quiet:true});
    } catch (e) { error(e.message,true); } finally { button.disabled = false; }
  }
  async function inlineEdit(select) {
    const id = Number(select.closest('tr').dataset.id), lead = state.rows.find(l => l.id === id), key = select.dataset.inline;
    if (key === 'status' && select.value === 'Follow-up Required' && !(lead.assigned_user_id && lead.next_action && lead.follow_up_at)) {
      select.value = lead.status; await openDetail(id,'Follow-up Required'); return;
    }
    select.disabled = true; state.editing = true;
    try {
      await api(`leads/${id}`,'PATCH',{version:lead.version,[key]:key === 'assigned_user_id' ? (select.value ? Number(select.value) : null) : select.value});
      await load({quiet:true});
      if (state.detail?.id === id && !state.dirty) await openDetail(id);
    } catch (e) { select.value = lead[key] || ''; error(e.message); } finally { state.editing = false; select.disabled = false; }
  }
  async function editNote(button) {
    state.editing = true;
    try {
      const l = await api('leads/' + button.dataset.note);
      const area = document.createElement('textarea'); area.className = 'iq-note-editor'; area.value = l.internal_notes || '';
      area.setAttribute('aria-label','Internal notes'); button.replaceWith(area); area.focus();
      const actions = document.createElement('div'); actions.className='iq-note-actions';
      actions.innerHTML='<button class="iq-button" type="button">Save note</button><button class="iq-button" type="button">Cancel</button>';
      area.after(actions);
      const save = async () => {
        area.disabled = true;
        try { await api(`leads/${l.id}`,'PATCH',{version:l.version,internal_notes:area.value}); state.editing=false;await load({quiet:true}); }
        catch(e) {error(e.message);area.disabled=false;}
      };
      actions.children[0].addEventListener('click',save);
      actions.children[1].addEventListener('click',async()=>{state.editing=false;await load({quiet:true});});
      area.addEventListener('keydown', async event => {
        if (event.key === 'Escape') { state.editing = false; await load({quiet:true}); }
        if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
          event.preventDefault(); await save();
        }
      });
    } catch (e) { error(e.message); state.editing = false; }
  }
  async function adminPanel() {
    if (state.dirty && !window.confirm('Discard your unsaved changes?')) return;
    state.dirty = false; state.detail = null;
    showPanel('Intake & settings', 'GUEST INQUIRIES');
    const s = state.bootstrap.settings;
    $('iqPanelBody').innerHTML = `<p class="iq-muted">Follow-up dates are displayed in ${escape(s.timezone)}. Blank timing leaves scheduled alerts unset; overdue and unassigned inquiries remain visible in the team queue.</p><form id="iqSettingsForm"><h3>Team follow-ups</h3><div class="iq-form-grid">${field('Staff timezone','timezone',s.timezone)}${field('Reminder before due (minutes)','reminder_minutes',s.reminder_minutes,'number')}${field('Escalate after overdue (minutes)','escalation_minutes',s.escalation_minutes,'number')}${field('Flag unassigned after (minutes)','unassigned_minutes',s.unassigned_minutes,'number')}</div><p class="iq-muted" style="margin-top:15px">Alerts appear on the inquiry and in the shared CRM queue.</p><div class="iq-form-footer"><button class="iq-button iq-primary" type="submit">Save settings</button></div></form><h3>Email intake configuration</h3>${state.bootstrap.sources.map(v => `<div class="iq-source"><strong>${escape(v.brand || v.id)}</strong><br>${escape(v.mailbox)}<br>Subject: ${escape(v.subject_prefixes.join(', '))}<br><span class="iq-muted">Configured source. Worker activation and a verified sample import are required before calling it connected.</span></div>`).join('') || '<p>No intake sources configured.</p>'}<h3>Unresolved processing</h3><div id="iqFailures">Loading…</div>`;
    $('iqSettingsForm').addEventListener('input', () => { state.dirty = true; });
    $('iqSettingsForm').querySelectorAll('input[type=number]').forEach(i => i.max = '525600');
    $('iqSettingsForm').addEventListener('submit', async e => {
      e.preventDefault(); error('',true); const data = {version:s.version};
      for (const [key,value] of new FormData(e.target)) data[key] = key === 'timezone' ? value : value === '' ? null : Number(value);
      const button = e.target.querySelector('button'); button.disabled = true;
      try { state.bootstrap.settings = await api('settings','PATCH',data); state.dirty = false; $('iqTimezone').textContent = tz(); await adminPanel(); await load(); }
      catch (err) { error(err.message,true); } finally { button.disabled = false; }
    });
    try {
      const result = await api('failures');
      $('iqFailures').innerHTML = result.failures.length ? result.failures.map(f => `<details class="iq-message"><summary>${escape(f.subject)}<br>${escape(f.error)} · ${f.attempts} attempts</summary><pre>${escape(f.body)}</pre><a href="${escape(f.source_url)}" target="_blank" rel="noopener noreferrer">Open source email ↗</a><button class="iq-button" data-retry="${f.id}">Retry parsing</button></details>`).join('') : '<p class="iq-muted">No unresolved processing failures.</p>';
    } catch (e) { $('iqFailures').textContent = e.message; }
  }
  async function init() {
    try {
      state.bootstrap = await api('bootstrap');
      $('iqTimezone').textContent = tz();
      $('iqStatus').innerHTML += statusOptions('');
      $('iqAgent').innerHTML += state.bootstrap.users.map(u => option(u.id,u.name)).join('');
      $('iqTag').innerHTML += state.bootstrap.tags.map(t => option(t.id,t.name)).join('');
      updatePropertyOptions();
      $('iqAdmin').hidden = !state.bootstrap.can_administer;
      $('iqTestScope').hidden = !state.bootstrap.can_administer;
      $('iqTestScope').href = testView ? '/inquiries/' : '/inquiries/?record_scope=internal-tests';
      $('iqTestScope').textContent = testView ? 'Genuine inquiries' : 'Internal tests';
      $('iqTestBanner').hidden = !testView;
      await load();
      let debounce;
      $('iqSearch').addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(() => {state.page = 1; load();},300); });
      ['iqDateRange','iqStatus','iqProperty','iqAgent','iqFrom','iqTo'].forEach(id => $(id).addEventListener('change', () => {state.page=1;load();}));
      $('iqTag').addEventListener('change', () => {updatePropertyOptions();state.page=1;load();});
      $('iqClear').addEventListener('click', () => { ['iqSearch','iqStatus','iqTag','iqProperty','iqAgent','iqFrom','iqTo'].forEach(id => $(id).value=''); $('iqDateRange').value='all'; updatePropertyOptions(); state.page=1; state.attention=''; document.querySelectorAll('[data-attention]').forEach(b => {b.classList.toggle('active',b.dataset.attention === ''); b.setAttribute('aria-pressed',String(b.dataset.attention === ''));}); load(); });
      document.querySelectorAll('[data-attention]').forEach(b => b.addEventListener('click', () => {state.attention=b.dataset.attention;state.page=1;document.querySelectorAll('[data-attention]').forEach(v=>{v.classList.toggle('active',v===b);v.setAttribute('aria-pressed',String(v===b));});load();}));
      document.querySelectorAll('[data-sort]').forEach(b => b.addEventListener('click', () => { state.direction=state.sort===b.dataset.sort && state.direction==='desc'?'asc':'desc';state.sort=b.dataset.sort;state.page=1;document.querySelectorAll('[data-sort]').forEach(v=>{v.textContent=v.textContent.replace(/ [↑↓]$/,'')+(v===b?(state.direction==='desc'?' ↓':' ↑'):'');});load(); }));
      $('iqPrevious').addEventListener('click',()=>{state.page--;load();}); $('iqNext').addEventListener('click',()=>{state.page++;load();});
      $('iqRows').addEventListener('click', e => { const open=e.target.closest('[data-open]'), note=e.target.closest('[data-note]');if(open)openDetail(open.dataset.open);if(note)editNote(note); });
      $('iqRows').addEventListener('change', e => {if(e.target.matches('[data-inline]'))inlineEdit(e.target);});
      $('iqAdmin').addEventListener('click', adminPanel);
      $('iqRefresh').addEventListener('click',()=>{if(state.editing){notice('Finish editing the internal note before refreshing.');return;}load();});
      $('iqClose').addEventListener('click',closePanel); $('iqBackdrop').addEventListener('click',closePanel);
      $('iqPanelBody').addEventListener('click', async e => {const retry=e.target.closest('[data-retry]');if(!retry)return;retry.disabled=true;try{const result=await api(`failures/${retry.dataset.retry}/retry`,'POST',{});if(result.processing_status==='failed')throw new Error(result.error);await adminPanel();await load();}catch(err){error(err.message,true);retry.disabled=false;}});
      document.addEventListener('keydown', e => {
        if(!state.panel)return;
        if(e.key==='Escape')closePanel();
        if(e.key==='Tab') {const nodes=[...$('iqPanel').querySelectorAll('button:not(:disabled),input,select,textarea,a[href],summary')].filter(v=>v.getClientRects().length);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&(document.activeElement===first||document.activeElement===$('iqPanel'))){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
      });
      window.addEventListener('beforeunload',e=>{if(state.dirty||state.editing){e.preventDefault();e.returnValue='';}});
      setInterval(()=>{if(!document.hidden&&!state.editing&&!state.dirty)load({quiet:true});},30000);
    } catch (e) { error(e.message); $('iqRows').innerHTML='<tr><td colspan="12" class="iq-empty">The workspace could not load. Reload the page to retry.</td></tr>'; }
  }
  init();
})();
