(() => {
  const root = document.querySelector('.owner-setup');
  const q = s => root.querySelector(s);
  const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const csrf = document.querySelector('meta[name="owner-access-csrf"]').content;
  let state;
  const account = id => state.accounts.find(a => a.id === id)?.name || `Account ${id}`;
  const property = id => state.properties.find(p => p.id === id)?.name || `Property ${id}`;
  const permissions = row => [row.performance && 'Performance', row.statements && 'Statements'].filter(Boolean).join(' · ');
  const revoke = (kind, row) => row.revoked ? 'Revoked' : `<button type="button" data-revoke="${kind}" data-id="${row.id}">Revoke</button>`;
  function table(target, heads, rows) {
    q(target).innerHTML = rows.length ? `<table><thead><tr>${heads.map(h => `<th>${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<p>None yet.</p>';
  }
  async function api(path, data) {
    const response = await fetch(`/owner-access/api/${path}`, {credentials:'same-origin',
      ...(data ? {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)} : {})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not complete this action.');
    return result;
  }
  async function load() {
    state = await api('setup');
    q('#oa-login-pending').hidden=state.invitation_ready;
    q('form[data-action="invitations"] button').disabled=!state.invitation_ready;
    const options = rows => '<option value="">Select…</option>' + rows.map(row => `<option value="${row.id}">${escape(row.name)}</option>`).join('');
    root.querySelectorAll('.oa-accounts').forEach(select => {const value=select.value; select.innerHTML=options(state.accounts); select.value=value;});
    q('#oa-properties').innerHTML=options(state.properties);
    q('#oa-listings').innerHTML=state.listings.map(l => `<option value="${l.id}">${escape(l.name)} (${l.id})</option>`).join('');
    table('#oa-grants',['Account','Property','History','Access'],state.grants.map(row => [escape(account(row.account_id)),escape(property(row.property_id)),`${escape(row.from)} → ${escape(row.until_exclusive || 'ongoing')}`,revoke('grant',row)]));
    table('#oa-memberships',['Account','Person','Permissions','Access'],state.memberships.map(row => [escape(account(row.account_id)),escape(row.email),escape(permissions(row)),`${revoke('membership',row)}${row.revoked?'':`<button type="button" data-preview="${row.id}">Preview</button>`}`]));
    table('#oa-invitations',['Account','Email','Permissions','Status'],state.invitations.map(row => [escape(account(row.account_id)),escape(row.email),escape(permissions(row)),row.accepted?'Accepted':row.revoked?'Revoked':Date.parse(row.expires_at)<Date.now()?'Expired':revoke('invitation',row)]));
  }
  function error(reason) {q('#oa-error').textContent=reason.message; q('#oa-error').hidden=false;}
  root.addEventListener('submit',async event => {
    const form = event.target.closest('form[data-action]');
    if (!form) return;
    event.preventDefault(); q('#oa-error').hidden=true;
    const button=form.querySelector('button'); button.disabled=true;
    q('#oa-invitation').hidden=true; q('#oa-link').value=''; q('#oa-preview').hidden=true;
    const fields = new FormData(form), data=Object.fromEntries(fields);
    ['account_id','property_id'].forEach(key => {if (key in data) data[key]=Number(data[key]);});
    if (form.dataset.action==='properties') {data.listing_ids=fields.getAll('listing_ids').map(Number); data.single_unit_confirmed=fields.has('single_unit_confirmed');}
    if (form.dataset.action==='invitations') {data.performance=fields.has('performance'); data.statements=fields.has('statements');}
    try {
      const result=await api(form.dataset.action,data);
      if(result.url){q('#oa-link').value=result.url;q('#oa-invitation').hidden=false;}
      q('#oa-status').textContent='Saved.';form.reset();await load();
    } catch(reason){error(reason);} finally{button.disabled=form.dataset.action==='invitations' && !state?.invitation_ready;}
  });
  root.addEventListener('click', async event => {
    const button=event.target.closest('button');if(!button || button.type==='submit')return;
    if(button.id==='oa-copy'){try{await navigator.clipboard.writeText(q('#oa-link').value);q('#oa-status').textContent='Invitation copied.';}catch{q('#oa-link').select();q('#oa-status').textContent='Select and copy the invitation link.';}return;}
    if(!button.dataset.revoke && !button.dataset.preview)return;
    if(button.dataset.revoke && !window.confirm('Revoke this access now?'))return;
    button.disabled=true;q('#oa-error').hidden=true;q('#oa-preview').hidden=true;
    try{
      if(button.dataset.revoke){await api('revoke',{kind:button.dataset.revoke,id:Number(button.dataset.id)});q('#oa-status').textContent='Access revoked.';await load();}
      else {const result=await api('preview',{membership_id:Number(button.dataset.preview)});
        q('#oa-preview-content').innerHTML=result.accounts.map(a=>`<h3>${escape(a.name)}</h3><p>${escape(permissions(a.permissions))}</p><ul>${a.properties.map(p=>`<li>${escape(p.name)} · ${escape(p.reporting_from)} → ${escape(p.reporting_until_exclusive || 'ongoing')}</li>`).join('')}</ul>`).join('') || '<p>No active account access.</p>';
        q('#oa-preview').hidden=false;q('#oa-preview').scrollIntoView({block:'nearest'});}
    }catch(reason){error(reason);}finally{button.disabled=false;}
  });
  load().catch(error);
})();
