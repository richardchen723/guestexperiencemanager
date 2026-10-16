/* Procurement workspace. Server-derived quantities/statuses remain authoritative. */
(() => {
    'use strict';
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const state = { ref: null, requests: [], orders: [], status: 'active', view: 'requests', detail: null, report: null };
    const money = (cents, currency = 'USD') => new Intl.NumberFormat(undefined, { style: 'currency', currency }).format((cents || 0) / 100);
    const num = v => v == null ? 'Unknown' : new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(v);
    const date = (value, tz) => {
        if (!value) return 'Not specified';
        const opts = {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'};
        try { return new Intl.DateTimeFormat(undefined,{...opts,...(tz ? {timeZone:tz,timeZoneName:'short'} : {})}).format(new Date(value)); }
        catch (_) { return new Intl.DateTimeFormat(undefined,{...opts,timeZone:'UTC',timeZoneName:'short'}).format(new Date(value)); }
    };
    const localInput = value => value ? new Date(new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
    const utc = value => value ? new Date(value).toISOString() : null;
    const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const opt = (value, text, selected = false) => `<option value="${esc(value)}"${selected ? ' selected' : ''}>${esc(text)}</option>`;
    const options = (rows, selected, placeholder = 'Choose…', label = 'name') => opt('', placeholder, !selected) + rows.map(x => opt(x.id, x[label], String(x.id) === String(selected))).join('');
    const field = (label, input, hint = '', cls = '') => `<label class="pc-field ${cls}"><span>${esc(label)}</span>${input}${hint ? `<small>${esc(hint)}</small>` : ''}</label>`;
    const input = (name, value = '', attrs = '') => `<input name="${name}" value="${esc(value)}" ${attrs}>`;
    const select = (name, opts, attrs = '') => `<select name="${name}" ${attrs}>${opts}</select>`;
    const note = (name = 'note', placeholder = 'Add a note…', required = true) => `<textarea name="${name}" rows="3" maxlength="4000" placeholder="${esc(placeholder)}" ${required ? 'required' : ''}></textarea>`;
    const badge = (status, label) => `<span class="pc-badge ${esc(status)}">${esc(label || state.ref.statuses[status] || status)}</span>`;
    const button = (action, label, attrs = '', cls = 'pc-secondary') => `<button type="button" class="pc-button pc-button-small ${cls}" data-action="${action}" ${attrs}>${esc(label)}</button>`;
    const empty = (title, copy, action = '') => `<div class="pc-empty"><div class="pc-empty-symbol" aria-hidden="true">▧</div><h3>${esc(title)}</h3><p>${esc(copy)}</p>${action}</div>`;
    const activeStaff = propertyId => state.ref.staff.filter(s => s.active && (!propertyId || s.listing_ids.includes(Number(propertyId))));
    const activeLocations = () => state.ref.locations.filter(l => l.active && (!l.staff_id || state.ref.staff.some(s => s.id === l.staff_id && s.active)));
    let formCallback = null, formKey = null, formSignature = null, busy = false, noticeTimer, formReload = null;

    async function api(path, method = 'GET', body = null, requestKey = null) {
        const headers = { Accept: 'application/json' };
        const opts = { method, headers, credentials: 'same-origin' };
        if (method !== 'GET') {
            headers['X-CSRF-Token'] = document.querySelector('meta[name="procurement-csrf"]').content;
            headers['Idempotency-Key'] = requestKey || crypto.randomUUID();
            if (body instanceof FormData) opts.body = body;
            else { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body || {}); }
        }
        let response;
        try { response = await fetch(`/procurement/api/${path}`, opts); }
        catch (_) { throw new Error('Could not reach Cotton Candy. Your entries are still here; check your connection and retry.'); }
        const result = await response.json().catch(() => ({ error: 'The server returned an unexpected response. Reload and try again.' }));
        if (!response.ok) { const error = new Error(result.error || 'This action could not be completed.'); error.status = response.status; throw error; }
        return result;
    }

    function notify(message) {
        $('pcNotice').textContent = message; $('pcNotice').hidden = false;
        clearTimeout(noticeTimer); noticeTimer = setTimeout(() => { $('pcNotice').hidden = true; }, 5500);
    }
    function showError(message) { $('pcError').textContent = message; $('pcError').hidden = false; }

    async function load() {
        const ref = await api('bootstrap');
        const [requests, orders] = await Promise.all([api('requests'), ref.can_purchase ? api('orders') : Promise.resolve({orders: []})]);
        $('pcError').hidden = true;
        state.ref = ref; state.requests = requests.requests; state.orders = orders.orders;
        document.querySelectorAll('[data-purchaser]').forEach(el => { el.hidden = !ref.can_purchase; });
        const previousProperty = $('pcProperty').value, previousTag = $('pcTag').value;
        $('pcProperty').innerHTML = options(ref.properties, previousProperty, 'All properties');
        $('pcTag').innerHTML = opt('', 'All tags') + [...new Set(ref.properties.flatMap(p => p.tags))].sort().map(t => opt(t, t, t === previousTag)).join('');
        const previousHousekeeperProperty = $('pcHousekeeperProperty').value;
        $('pcHousekeeperProperty').innerHTML = options(ref.properties, previousHousekeeperProperty, 'All properties');
        render();
        switchView(state.view);
    }

    function render() {
        const open = state.requests.filter(t => !['completed', 'cancelled'].includes(t.status));
        const attention = open.filter(t => t.flags.length || t.priority === 'critical');
        const awaiting = open.filter(t => t.status === 'awaiting_confirmation');
        const waitingApproval = state.orders.filter(o => o.state === 'draft');
        const stats = [
            ['Open requests', open.length, 'Tracked through property confirmation', '▧', ''],
            ['Need attention', attention.length, 'Missing information, urgency, or delays', '!', attention.length ? 'warning' : ''],
            ['Confirm at property', awaiting.length, 'Housekeeper verification outstanding', '⌂', ''],
            state.ref.can_purchase ? ['Awaiting approval', waitingApproval.length, 'Draft purchases ready for review', '✓', ''] : ['Completed requests', state.requests.filter(t => t.status === 'completed').length, 'Confirmed at the correct property', '✓', '']
        ];
        if (!state.ref.can_purchase) { stats.splice(1, 1); stats.pop(); }
        $('pcStats').classList.toggle('pc-stats-compact', !state.ref.can_purchase);
        $('pcStats').innerHTML = stats.map(([title, value, caption, icon, cls]) => `<div class="pc-stat ${cls}"><span>${esc(title)}</span><div class="pc-stat-icon" aria-hidden="true">${icon}</div><strong>${value}</strong><small>${esc(caption)}</small></div>`).join('');
        $('pcOpenCount').textContent = open.length;
        $('pcSetup').hidden = !state.ref.can_purchase || activeLocations().length > 0;
        $('pcSetup').innerHTML = `Add a verified delivery destination before drafting your first purchase. ${state.ref.can_approve ? button('directory-edit','Add delivery destination','data-kind="locations"') : 'Ask an administrator to set up destinations in the procurement directory.'}`;
        renderRequests(); renderOrders(); renderHousekeepers(); renderDirectory();
    }

    function renderRequests() {
        const term = $('pcSearch').value.trim().toLowerCase(), property = $('pcProperty').value, priority = $('pcPriority').value, tag = $('pcTag').value;
        const filtered = state.requests.filter(t => {
            if (state.status === 'active' && ['completed', 'cancelled'].includes(t.status)) return false;
            if (state.status === 'attention' && (!t.flags.length && t.priority !== 'critical' || ['completed', 'cancelled'].includes(t.status))) return false;
            if (!['active', 'attention', 'all'].includes(state.status) && t.status !== state.status) return false;
            if (property && String(t.listing_id) !== property || priority && t.priority !== priority || tag && !t.tags.includes(tag)) return false;
            if ($('pcMine').checked && t.owner_user_id !== state.ref.user_id) return false;
            return !term || [t.code, t.title, t.property, t.requester_name, t.owner_name, ...t.items.map(i => i.name)].join(' ').toLowerCase().includes(term);
        }).sort((a, b) => {
            const rank = {critical: 0, high: 1, normal: 2, planned: 3};
            return rank[a.priority] - rank[b.priority] || (a.required_by || '9999').localeCompare(b.required_by || '9999');
        });
        $('pcQueueCount').textContent = `${filtered.length} request${filtered.length === 1 ? '' : 's'}`;
        if (!filtered.length) {
            $('pcRequestList').innerHTML = empty(state.requests.length ? 'No requests match these filters' : 'Your supply requests start here', state.requests.length ? 'Adjust your search or choose All requests to see more.' : 'Capture a request with its items, property, staff owner, and deadline.', button('new-request', 'Create a request', '', 'pc-primary'));
            return;
        }
        $('pcRequestList').innerHTML = `<div class="pc-table-wrap"><table class="pc-table pc-request-table"><thead><tr><th>Request / Property</th><th>Supplies</th><th>Status</th><th>Priority</th><th>Needed by</th><th>Staff owner</th><th></th></tr></thead><tbody>${filtered.map(t => `<tr data-action="request-detail" data-id="${t.id}" tabindex="0" role="button" aria-label="Open ${esc(t.code)}: ${esc(t.title)}"><td><div class="pc-code">${esc(t.code)}</div><strong>${esc(t.property)}</strong><small>${esc(t.title)}</small>${t.tags.slice(0, 2).map(tag => `<span class="pc-tag">${esc(tag)}</span>`).join('')}</td><td><strong>${t.items.length} item${t.items.length === 1 ? '' : 's'}</strong><small>${t.fulfilled_items} fulfilled · ${esc(t.items.slice(0, 2).map(i => i.name).join(', '))}${t.items.length > 2 ? '…' : ''}</small><div class="pc-progress"><span style="width:${t.items.length ? Math.round(t.fulfilled_items / t.items.length * 100) : 0}%"></span></div></td><td>${badge(t.status, t.status_label)}<div>${t.flags.map(f => `<span class="pc-flag">${esc(f)}</span>`).join('')}</div></td><td><span class="pc-priority ${esc(t.priority)}">${esc(t.priority[0].toUpperCase() + t.priority.slice(1))}</span></td><td class="${t.flags.includes('Overdue') ? 'pc-date-overdue' : ''}">${esc(date(t.required_by, t.timezone))}</td><td>${esc(t.owner_name)}<small>From ${esc(t.requester_name)}</small></td><td class="pc-subtle">↗</td></tr>`).join('')}</tbody></table></div>`;
    }

    function renderOrders() {
        $('pcOrderList').innerHTML = !state.orders.length ? empty('No purchases yet', 'Draft a purchase from a request, or buy shared stock and allocate it later.', button('new-order', 'Draft a purchase', '', 'pc-primary')) : `<div class="pc-table-wrap"><table class="pc-table"><thead><tr><th>Purchase</th><th>Delivery destination</th><th>Progress</th><th>Total</th><th>Financial records</th></tr></thead><tbody>${state.orders.map(o => `<tr data-action="order-detail" data-id="${o.id}" tabindex="0" role="button" aria-label="Open ${esc(o.code)}"><td><div class="pc-code">${esc(o.code)}</div><strong>${esc(o.vendor)}</strong><small>${esc(o.reference || 'Retailer order not recorded')}</small></td><td>${esc(o.destination.label)}<small>${o.lines.length} purchase line${o.lines.length === 1 ? '' : 's'}</small></td><td>${badge(o.state)}<small>${o.shipments.filter(s => s.delivered_at).length} / ${o.shipments.filter(s => !s.cancelled_reason).length} shipments delivered</small></td><td><strong>${esc(money(o.total_cents, o.currency))}</strong>${o.refund_cents ? `<small>${esc(money(o.refund_cents, o.currency))} refunded</small>` : ''}</td><td>${o.reconciled_at ? '✓ Reconciled' : 'Not reconciled'}<small>${o.attachments.length} receipt${o.attachments.length === 1 ? '' : 's'}</small></td></tr>`).join('')}</tbody></table></div>`;
    }

    const actionLabels = {request_created:'Request captured',request_updated:'Request updated',order_created:'Purchase drafted',order_revised:'Purchase revised',purchase_approve:'Purchase approved',order_approve:'Purchase approved',purchase_place:'Purchase recorded',order_place:'Purchase recorded',purchase_cancel:'Draft purchase cancelled',order_cancel:'Draft purchase cancelled',shipment_updated:'Tracking updated',shipment_cancelled:'Shipment cancelled',shipment_created:'Shipment added',shipment_delivered:'Delivery recorded',delivery_reported:'Delivery reported · property confirmation needed',property_confirmed:'Housekeeper confirmed at property',property_return:'Supplies returned from property',fulfillment_issue:'Fulfillment problem reported',item_cancelled:'Unpurchased quantity cancelled',request_cancelled:'Request cancelled',refund_recorded:'Refund received',receipt_attached:'Receipt attached',purchase_reconcile:'Receipt reconciled',receipt_costs_approved:'Final receipt costs corrected',purchase_adjust_costs:'Cost correction approved',shared_stock_allocated:'Shared stock allocated'};
    function snapshotDetails(snapshot) {
        return `<div class="pc-approval-box"><strong>${esc(snapshot.vendor)} · ${esc(money(snapshot.total_cents,snapshot.currency))}</strong><p>${esc(snapshot.destination?.label || '')}</p><p class="pc-address">${esc(snapshot.destination?.address || '')}</p>${(snapshot.lines || []).map(line => `<p>${esc(line.name)} · ${num(line.quantity)} ${esc(line.unit)} · ${esc(money(line.unit_price_cents,snapshot.currency))} per ${num(line.pack_size || 1)} ${esc(line.unit)}</p>`).join('')}</div>`;
    }
    function auditDetails(detail) {
        const rows = [], labels = {title:'Request',source:'Source',reason:'Reason',note:'Note',quantity:'Quantity',housekeeper:'Housekeeper',reference:'Retailer reference',filename:'Receipt',tracking:'Tracking number',carrier:'Carrier',expected_at:'Expected arrival',amount_cents:'Amount (minor currency units)',kind:'Problem / record type'};
        for (const [key,label] of Object.entries(labels)) if (detail[key] != null && typeof detail[key] !== 'object') rows.push([label,String(detail[key])]);
        if (detail.order_id) rows.push(['Purchase',`PO-${String(detail.order_id).padStart(4,'0')}`]);
        if (detail.evidence?.reference) rows.push(['Housekeeper evidence',detail.evidence.reference]);
        if (detail.before && detail.after) {
            const before = detail.before, after = detail.after;
            for (const [key,label] of Object.entries({title:'Title',property:'Property',requester_name:'Requester',owner_name:'Staff owner',priority:'Priority',required_by:'Needed by',response_due_at:'Staff response due'})) {
                if (before[key] !== after[key] && (before[key] != null || after[key] != null)) rows.push([label,`${before[key] || 'Not specified'} → ${after[key] || 'Not specified'}`]);
            }
            if (before.total_cents != null && before.total_cents !== after.total_cents) rows.push(['Order total',`${money(before.total_cents,before.currency)} → ${money(after.total_cents,after.currency)}`]);
            for (const item of after.items || []) { const old = (before.items || []).find(i => i.id === item.id); if (!old) rows.push(['Item added',`${item.name} · ${num(item.quantity)} ${item.unit}`]); else if (old.quantity !== item.quantity || old.name !== item.name || old.unit !== item.unit) rows.push(['Item changed',`${old.name}: ${num(old.quantity)} ${old.unit} → ${item.name}: ${num(item.quantity)} ${item.unit}`]); }
        }
        const snapshot = detail.snapshot || detail.approval?.snapshot;
        return `${rows.length ? `<dl class="pc-audit-record">${rows.map(([label,value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>` : ''}${snapshot ? snapshotDetails(snapshot) : ''}${!rows.length && !snapshot ? '<p class="pc-detail-note">Action recorded with the actor and timestamp shown above.</p>' : ''}`;
    }
    function activity(events) {
        return `<ul class="pc-timeline">${events.map(e => `<li><strong>${esc(actionLabels[e.action] || e.action.replace(/_/g, ' '))}</strong><small>${esc(e.actor)} · ${esc(date(e.at))}</small>${e.detail.note || e.detail.reason ? `<p>${esc(e.detail.note || e.detail.reason)}</p>` : ''}<details><summary class="pc-text-button">View record</summary>${auditDetails(e.detail)}</details></li>`).join('')}</ul>`;
    }

    async function detail(kind, id) {
        const data = await api(`${kind === 'request' ? 'requests' : 'orders'}/${id}`);
        state.detail = {kind, data};
        $('pcDetailCode').textContent = data.code;
        $('pcDetailTitle').textContent = kind === 'request' ? data.title : `${data.vendor} purchase`;
        $('pcDetailContent').innerHTML = kind === 'request' ? requestDetail(data) : orderDetail(data);
        if (!$('pcDetailDialog').open) $('pcDetailDialog').showModal();
        $('pcDetailDialog').scrollTop = 0;
    }

    function requestDetail(t) {
        const canPurchase = state.ref.can_purchase, cancelled = t.status === 'cancelled';
        const canOrder = canPurchase && !cancelled && t.items.some(i => i.to_order > 0);
        return `<div class="pc-detail-summary"><div class="pc-actions">${badge(t.status, t.status_label)}<span class="pc-priority ${esc(t.priority)}">${esc(t.priority)}</span></div><div class="pc-actions">${canPurchase && !cancelled ? button('edit-request', 'Edit request') + (!t.reviewed_at ? button('review-request', 'Mark reviewed') : '') : ''}${canOrder ? button('purchase-request', 'Prepare purchase', '', 'pc-primary') : ''}</div></div>
            ${t.flags.length ? `<div class="pc-alert">${t.flags.map(esc).join(' · ')}</div>` : ''}
            ${t.possible_duplicates.length && !['completed','cancelled'].includes(t.status) ? `<div class="pc-alert">Similar open requests: ${t.possible_duplicates.map(d => `<button type="button" class="pc-text-button" data-action="request-detail" data-id="${d.id}">${esc(d.code)} — ${esc(d.title)}</button>`).join(', ')}. Check whether this is a follow-up or an additional need before purchasing.</div>` : ''}
            <div class="pc-stepper">${['Captured', 'Purchasing', 'Delivered', 'At property'].map((label, i) => `<div class="${i === 0 || i === 1 && t.status !== 'new' || i === 2 && ['awaiting_confirmation', 'completed'].includes(t.status) || i === 3 && t.status === 'completed' ? 'done' : ''}">${label}</div>`).join('')}</div>
            <dl class="pc-info-grid"><div><dt>Property</dt><dd>${esc(t.property)}</dd></div><div><dt>Requested by</dt><dd>${esc(t.requester_name)}</dd></div><div><dt>Staff owner</dt><dd>${esc(t.owner_name)}</dd></div><div><dt>Needed by · property time</dt><dd>${esc(date(t.required_by, t.timezone))}</dd></div><div><dt>Staff response due</dt><dd>${esc(date(t.response_due_at, t.timezone))}</dd></div><div><dt>Captured</dt><dd>${esc(date(t.created_at, t.timezone))}</dd></div></dl>
            <h3>Requested supplies <span class="pc-subtle">${t.fulfilled_items} of ${t.items.length} fulfilled</span></h3>
            ${t.items.map(i => `<article class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(i.name)}</h4><small>${esc(i.unit)} · ${esc(i.category)}</small></div>${i.requested != null && i.remaining === 0 ? badge('completed', 'Fulfilled') : `<span class="pc-muted">${num(i.remaining)} remaining</span>`}</div><div class="pc-quantity-grid"><div><span>Requested</span><strong>${num(i.requested)}</strong></div><div><span>Ordered</span><strong>${num(i.ordered)}</strong></div><div><span>Delivered</span><strong>${num(i.delivered)}</strong></div><div><span>At property</span><strong>${num(i.confirmed)}</strong></div></div>${i.cancelled ? `<p class="pc-detail-note">${num(i.cancelled)} cancelled: ${esc(i.cancellation_reason)}</p>` : ''}
            ${i.allocations.map(a => `<div class="pc-allocation"><div>${canPurchase ? `<button type="button" class="pc-text-button" data-action="order-detail" data-id="${a.order_id}">PO-${String(a.order_id).padStart(4, '0')}</button>` : `PO-${String(a.order_id).padStart(4, '0')}`} · ${esc(a.vendor)} <span class="pc-subtle">${num(a.quantity)} ${esc(i.unit)} · ${esc(a.order_state)}</span></div><div class="pc-actions">${a.property_confirmable > 0 ? button('confirm', 'Confirm at property', `data-id="${a.id}"`, 'pc-primary') : ''}${a.confirmed > 0 ? button('return', 'Record return', `data-id="${a.id}"`) : ''}${a.reportable > 0 && a.order_state === 'ordered' ? button('problem', 'Report problem', `data-id="${a.id}"`) : ''}</div></div>${a.problem_quantity ? `<p class="pc-detail-note pc-danger-text">${num(a.problem_quantity)} ${esc(i.unit)} reported lost, damaged, incorrect, or cancelled. Replacement need remains tracked.</p>` : ''}${a.confirmations.map(c => `<p class="pc-detail-note">${c.quantity < 0 ? '↩ Returned' : '✓ Confirmed'} ${num(Math.abs(c.quantity))} at ${esc(c.property)} · ${esc(c.housekeeper)} · ${esc(date(c.at))}<br>${esc(c.note)}</p>`).join('')}`).join('')}
            ${canPurchase && i.to_order > 0 && !cancelled ? `<div style="margin-top:12px">${button('cancel-item', 'Cancel unpurchased quantity', `data-id="${i.id}"`)}</div>` : ''}</article>`).join('')}
            <p class="pc-detail-note">Delivered means arrival at the shipping destination. At property means the assigned housekeeper has verified the supplies reached the rental.</p>
            <div class="pc-detail-section"><div class="pc-form-row-heading"><h3>Messages & notes</h3>${!cancelled ? button('add-note', 'Add note') : ''}</div>${t.messages.length ? t.messages.map(m => `<div class="pc-message"><div class="pc-source-label">${esc(m.source)} · ${esc(date(m.at))}${m.message_id ? ` · ${esc(m.message_id)}` : ''}</div>${esc(m.body)}${m.url ? `<br><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">Original message ↗</a>` : ''}</div>`).join('') : '<p class="pc-muted">No messages attached.</p>'}</div>
            <div class="pc-detail-section"><h3>Activity history</h3>${activity(t.activity)}</div>${canPurchase && !cancelled && !t.items.some(i => i.reserved > 0) ? `<div class="pc-detail-section">${button('cancel-request', 'Cancel request', '', 'pc-destructive')}</div>` : ''}${t.cancelled_reason ? `<div class="pc-alert">Cancelled: ${esc(t.cancelled_reason)}</div>` : ''}`;
    }

    function orderDetail(o) {
        return `<div class="pc-detail-summary"><div class="pc-actions">${badge(o.state)}<strong>${esc(money(o.total_cents, o.currency))}</strong></div><div class="pc-actions">${['draft', 'approved'].includes(o.state) ? button('revise-order', 'Revise purchase') + button('cancel-order', 'Cancel draft', '', 'pc-destructive') : ''}${o.state === 'draft' && state.ref.can_approve ? button('approve-order', 'Review & approve', '', 'pc-primary') : ''}${o.state === 'approved' ? button('place-order', 'Record purchase', '', 'pc-primary') : ''}</div></div>
            <dl class="pc-info-grid"><div><dt>Retailer order number</dt><dd>${esc(o.reference || 'Not recorded')}</dd></div><div><dt>Delivery destination</dt><dd>${esc(o.destination.label)}<br><span class="pc-address">${esc(o.destination.address)}</span></dd></div><div><dt>Expense responsibility</dt><dd>${esc(o.responsibility)}</dd></div><div><dt>Purchase date</dt><dd>${esc(o.purchased_on || 'Not purchased')}</dd></div><div><dt>Approval</dt><dd>${esc(o.approval.actor || 'Awaiting administrator')}<br>${esc(date(o.approval.approved_at))}</dd></div><div><dt>Financial reconciliation</dt><dd>${o.reconciled_at ? `✓ ${esc(date(o.reconciled_at))}` : 'Not reconciled'}</dd></div></dl>
            <h3>Purchase lines</h3>${o.lines.map(l => `<article class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(l.name)}</h4><small>${num(l.quantity)} ${esc(l.unit)} · ${esc(money(l.unit_price_cents, o.currency))} per ${num(l.pack_size || 1)} ${esc(l.unit)}</small></div><strong>${esc(money(l.landed_cents, o.currency))}</strong></div>${l.substitution_reason ? `<p class="pc-detail-note">Substitution: ${esc(l.substitution_reason)}</p>` : ''}${l.allocations.map(a => `<p class="pc-detail-note"><button type="button" class="pc-text-button" data-action="request-detail" data-id="${a.ticket_id}">${esc(a.request)}</button> · ${esc(a.property)} · ${num(a.quantity)} ${esc(l.unit)}</p>`).join('')}${l.unallocated > 0 ? `<p class="pc-detail-note">${num(l.unallocated)} ${esc(l.unit)} not allocated to a property request.</p>` : ''}${l.refunds.map(r => `<p class="pc-detail-note">Refund ${esc(money(r.amount_cents, o.currency))} · ${esc(r.date)} · ${esc(r.reason)}</p>`).join('')}${o.state === 'ordered' ? `<div class="pc-actions" style="margin-top:12px">${l.unallocated > 0 ? button('allocate-stock', 'Allocate shared stock', `data-id="${l.id}"`) : ''}${button('refund', 'Record refund', `data-id="${l.id}"`)}</div>` : ''}</article>`).join('')}
            <div class="pc-order-total"><span>Subtotal ${esc(money(o.subtotal_cents, o.currency))}<br><span class="pc-muted">Tax ${esc(money(o.tax_cents, o.currency))} · Shipping ${esc(money(o.shipping_cents, o.currency))} · Discount −${esc(money(o.discount_cents, o.currency))}</span></span><strong>${esc(money(o.total_cents, o.currency))}</strong></div>${o.refund_cents ? `<p class="pc-detail-note">Refunds received: ${esc(money(o.refund_cents, o.currency))} · Net cost: ${esc(money(o.total_cents - o.refund_cents, o.currency))}</p>` : ''}
            ${o.state === 'ordered' ? `<div class="pc-detail-section"><div class="pc-form-row-heading"><h3>Shipments & delivery</h3>${o.lines.some(l => l.unshipped > 0) ? button('new-shipment', 'Add shipment') : ''}</div>${!o.shipments.length ? '<p class="pc-muted">No shipment recorded. Add a delivery, including local purchases.</p>' : o.shipments.map(s => `<div class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(s.carrier || 'Local / untracked delivery')}</h4><small>${esc(s.tracking || 'No tracking number')} · ${s.delivered_at ? `Delivered ${esc(date(s.delivered_at))}` : `Expected ${esc(date(s.expected_at))}`}</small></div>${s.cancelled_reason ? badge('cancelled','Cancelled') : !s.delivered_at ? `<div class="pc-actions">${button('edit-shipment','Edit tracking',`data-id="${s.id}"`)}${button('cancel-shipment','Cancel shipment',`data-id="${s.id}"`)}${button('deliver','Record delivery',`data-id="${s.id}"`)}</div>` : badge('completed', 'Delivered')}</div><p class="pc-detail-note">${s.lines.map(sl => `${esc(o.lines.find(l => l.id === sl.line_id)?.name)} × ${num(sl.quantity)}`).join(' · ')}${s.cancelled_reason ? `<br>Cancelled: ${esc(s.cancelled_reason)}` : ''}</p></div>`).join('')}<p class="pc-detail-note">Recording delivery keeps requests open until the housekeeper confirms at the property.</p></div>` : ''}
            <div class="pc-detail-section"><div class="pc-form-row-heading"><h3>Receipts & reconciliation</h3>${o.state !== 'cancelled' ? button('receipt', 'Attach receipt') : ''}</div><div class="pc-receipts">${o.attachments.map(a => `<a href="/procurement/api/receipts/${a.id}">${esc(a.name)} ↓</a>`).join('') || '<span class="pc-muted">No receipt attached.</span>'}</div>${o.state === 'ordered' && !o.reconciled_at ? `<div style="margin-top:15px">${button('reconcile', 'Reconcile charged amount')}${state.ref.can_approve ? button('adjust-costs', 'Correct receipt costs') : ''}</div>` : ''}</div>${o.notes ? `<div class="pc-detail-section"><h3>Purchasing notes</h3><p class="pc-message">${esc(o.notes)}</p></div>` : ''}<div class="pc-detail-section"><h3>Activity history</h3>${activity(o.activity)}</div>${o.approved_snapshot ? `<details class="pc-detail-section"><summary class="pc-text-button">View approved purchase snapshot</summary>${snapshotDetails(o.approved_snapshot)}</details>` : ''}`;
    }

    function openForm(title, html, callback, submit = 'Save') {
        if (busy) return;
        $('pcFormTitle').textContent = title; $('pcFormFields').innerHTML = html;
        $('pcFormError').hidden = true; $('pcFormSubmit').textContent = submit;
        formReload = null;
        formCallback = callback; formKey = crypto.randomUUID(); formSignature = null;
        $('pcFormDialog').showModal();
        $('pcFormDialog').scrollTop = 0;
        $('pcFormFields').querySelector('input:not([type=hidden]),select,textarea')?.focus({preventScroll:true});
    }

    function requestRow(row = {}) {
        return `<div class="pc-supply-row" data-request-row data-id="${row.id || ''}"><label class="pc-field pc-catalog-field"><span>Standard product (optional)</span>${select('catalog_id', options(state.ref.catalog.filter(c => c.active), row.catalog_id, 'Custom supply'), 'data-catalog-select')}</label><div class="pc-field-grid">${field('Item', input('name', row.name, `required maxlength="180" ${row.catalog_id ? 'readonly' : ''}`))}${field('Quantity', input('quantity', row.quantity ?? '', 'type="number" min="0.001" max="99999999" step="0.001" placeholder="Unknown"'))}${field('Unit', input('unit', row.unit || 'packs', `required maxlength="40" ${row.catalog_id ? 'readonly' : ''}`))}<button type="button" class="pc-mini-remove" data-remove-row aria-label="Remove item">×</button></div><input type="hidden" name="category" value="${esc(row.category || 'supplies')}"></div>`;
    }
    function readRequestRows(form) {
        return [...form.querySelectorAll('[data-request-row]')].map(row => {
            const v = Object.fromEntries(FormDataProxy(row));
            return { ...(row.dataset.id ? { id: Number(row.dataset.id) } : {}), catalog_id: v.catalog_id || null, name: v.name, unit: v.unit, category: v.category, quantity: v.quantity || null };
        });
    }
    function* FormDataProxy(root) { for (const el of root.querySelectorAll('[name]')) yield [el.name, el.value]; }

    function requestForm(existing = null) {
        const t = existing || {}, editable = (t.items || [{}]).filter(i => !i.allocations?.length);
        openForm(existing ? 'Edit supply request' : 'New supply request', `<p class="pc-form-intro">Capture the need now. Unknown quantities or dates stay visible as “Needs information” until clarified.</p><div class="pc-field-grid">${field('Request title', input('title', t.title || 'Supply restock', 'required maxlength="240"'), '', 'pc-span-2')}${field('Property', select('listing_id', options(state.ref.properties, t.listing_id, 'Property not yet known')))}${!existing && state.ref.can_purchase ? field('Or shared storage', select('storage_location_id', options(activeLocations().filter(l => l.kind === 'storage'), null, 'No shared storage', 'label'))) : ''}${field('Housekeeper', select('requester_staff_id', options(activeStaff(t.listing_id), t.requester_staff_id || state.ref.staff_id, 'Choose housekeeper')), 'Select an assigned housekeeper, or enter the requester below.')}${field('Requester name', input('requester_name', t.requester_name || '', 'maxlength="160"'))}${state.ref.can_purchase ? field('Staff owner', select('owner_user_id', options(state.ref.users, t.owner_user_id || state.ref.user_id, 'Unassigned'))) : ''}${field('Priority', select('priority', state.ref.priorities.map(p => opt(p, p[0].toUpperCase() + p.slice(1), p === (t.priority || 'normal'))).join('')))}${field('Needed by (your local time)', input('required_by', localInput(t.required_by), 'type="datetime-local"'), Intl.DateTimeFormat().resolvedOptions().timeZone)}${existing ? field('Staff response due (your local time)', input('response_due_at', localInput(t.response_due_at), 'type="datetime-local"')) : ''}</div><div class="pc-form-section"><div class="pc-form-row-heading"><h3>${existing ? 'Unpurchased items & additions' : 'Requested supplies'}</h3>${button('add-request-row', '＋ Add item')}</div>${existing ? '<p class="pc-form-intro">Reserved and purchased items retain their original details. Add another item for a new need.</p>' : ''}<div id="pcRequestRows">${editable.map(requestRow).join('')}</div></div>${!existing ? field('Original request / notes', note('body', 'Paste the original request or describe the need.', false)) : ''}`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const staff = state.ref.staff.find(s => s.id === Number(f.requester_staff_id));
            const body = { title: f.title, requester_name: staff?.name || f.requester_name,
                requester_staff_id: f.requester_staff_id || undefined, owner_user_id: f.owner_user_id || null,
                priority: f.priority, required_by: utc(f.required_by), items: readRequestRows(form) };
            if (f.listing_id && (!existing || Number(f.listing_id) !== t.listing_id)) body.listing_id = Number(f.listing_id);
            if (!existing && f.storage_location_id) body.storage_location_id = Number(f.storage_location_id);
            if (existing) { body.version = t.version; body.response_due_at = utc(f.response_due_at); }
            else if (f.body) body.source = {channel: 'manual', body: f.body};
            const result = await mutate(existing ? `requests/${t.id}` : 'requests', existing ? 'PATCH' : 'POST', body);
            return {kind: 'request', id: result.id, message: existing ? 'Request updated.' : 'Request captured and assigned.'};
        }, existing ? 'Save changes' : 'Create request');
    }

    function eligibleItems(includeReserved = false) {
        return state.requests.filter(t => !['cancelled'].includes(t.status)).flatMap(t => t.items.filter(i => i.requested != null && (i.to_order > 0 || includeReserved && (state.editingOrderItemIds || []).includes(i.id))).map(i => ({...i, ticket_id: t.id, label: `${t.code} · ${t.property} · ${i.name} (${num(i.to_order)} ${i.unit} to order)`})));
    }
    function allocationRow(row = {}) {
        return `<div class="pc-allocation-row" data-allocation-row>${select('item_id', options(eligibleItems(true), row.item_id, 'Choose request item', 'label'), 'aria-label="Request item" required')}${input('allocation_quantity', row.quantity ?? '', 'aria-label="Allocated quantity" type="number" min="0.001" max="99999999" step="0.001" required')}<button type="button" class="pc-mini-remove" data-remove-row aria-label="Remove allocation">×</button></div>`;
    }
    function orderRow(row = {}) {
        return `<div class="pc-order-row" data-order-row><div class="pc-field-grid">${field('Product', input('name', row.name || '', 'required maxlength="180"'))}${field('Quantity', input('quantity', row.quantity ?? '', 'type="number" min="0.001" max="99999999" step="0.001" required'))}${field('Unit', input('unit', row.unit || 'packs', 'required maxlength="40"'))}${field('Units per pack', input('pack_size', row.pack_size || 1, 'type="number" min="0.001" max="99999999" step="0.001" required'))}${field('Price per pack', input('unit_price', row.unit_price_cents != null ? (row.unit_price_cents / 100).toFixed(2) : '', 'type="number" min="0" max="99999999" step="0.01" required'))}<button type="button" class="pc-mini-remove" data-remove-row aria-label="Remove purchase line">×</button></div><input name="catalog_id" type="hidden" value="${row.catalog_id || ''}"><div class="pc-form-section" style="margin:14px 0 0;padding-top:12px"><div class="pc-form-row-heading"><span class="pc-muted">Allocate to requests · leave excess as shared stock</span>${button('add-allocation-row', '＋ Allocate')}</div><div data-allocations>${(row.allocations || []).map(allocationRow).join('')}</div></div><label class="pc-field" style="margin-top:12px"><span>Substitution reason (if different from request)</span>${input('substitution_reason', row.substitution_reason || '', 'maxlength="2000" placeholder="Same product as requested"')}</label></div>`;
    }

    function orderForm(ticket = null, existing = null) {
        state.editingOrderItemIds = existing ? existing.lines.flatMap(l => l.allocations.map(a => a.item_id)) : [];
        const o = existing || {}, lines = existing ? o.lines : ticket ? ticket.items.filter(i => i.to_order > 0).map(i => ({...i, quantity: i.to_order, allocations: [{item_id: i.id, quantity: i.to_order}]})) : [{}];
        const defaultLocation = existing?.destination.id || activeLocations().find(l => ticket && (l.staff_id === ticket.requester_staff_id || l.listing_id === ticket.listing_id))?.id;
        openForm(existing ? 'Revise purchase' : 'Draft a purchase', `<p class="pc-form-intro">Enter the total purchased quantity in the requested unit, then the units and price per pack. For 24 rolls costing $17.49: quantity 24, units per pack 24, price $17.49. Use 1 unit per pack for individual items.</p><div class="pc-field-grid">${field('Retailer', input('vendor', o.vendor || '', 'required maxlength="160" placeholder="e.g. Walmart"'))}${field('Currency', select('currency', state.ref.currencies.map(c => opt(c, c, c === (o.currency || 'USD'))).join('')))}${field('Delivery destination', select('location_id', options(activeLocations(), defaultLocation, 'Choose delivery destination', 'label'), 'required'), 'Manage addresses in Directory.', 'pc-span-2')}${field('Expense responsibility', select('responsibility', ['unassigned', 'owner', 'company', 'included'].map(r => opt(r, {unassigned:'Not assigned yet',owner:'Owner reimbursable',company:'Management company',included:'Included in management fee'}[r], r === (o.responsibility || 'unassigned'))).join('')), '', 'pc-span-2')}</div><div class="pc-form-section"><div class="pc-form-row-heading"><h3>Purchase lines</h3>${button('add-order-row', '＋ Add product')}</div><div id="pcOrderRows">${lines.map(orderRow).join('')}</div></div><div class="pc-field-grid">${field('Tax', input('tax', ((o.tax_cents || 0) / 100).toFixed(2), 'type="number" min="0" max="99999999" step="0.01" required'))}${field('Shipping', input('shipping', ((o.shipping_cents || 0) / 100).toFixed(2), 'type="number" min="0" max="99999999" step="0.01" required'))}${field('Discount', input('discount', ((o.discount_cents || 0) / 100).toFixed(2), 'type="number" min="0" max="99999999" step="0.01" required'))}</div><div id="pcOrderPreview" class="pc-order-total"></div><div style="margin-top:20px">${field('Purchasing notes', `<textarea name="notes" rows="2" maxlength="5000">${esc(o.notes || '')}</textarea>`)}${existing ? field('Reason for revision', note('change_reason', 'Explain what changed. This revision will require a fresh approval.')) : ''}</div>`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const rows = [...form.querySelectorAll('[data-order-row]')].map(row => {
                const data = Object.fromEntries(FormDataProxy(row));
                return {name: data.name, catalog_id: data.catalog_id || null, unit: data.unit, quantity: data.quantity,
                    pack_size: data.pack_size, unit_price: data.unit_price, substitution_reason: data.substitution_reason,
                    allocations: [...row.querySelectorAll('[data-allocation-row]')].map(a => ({item_id: Number(a.querySelector('[name=item_id]').value), quantity: a.querySelector('[name=allocation_quantity]').value}))};
            });
            const body = {vendor:f.vendor, currency:f.currency, location_id:Number(f.location_id), responsibility:f.responsibility, tax:f.tax, shipping:f.shipping, discount:f.discount, notes:f.notes, lines:rows};
            if (existing) { body.version = o.version; body.change_reason = f.change_reason; }
            const result = await mutate(existing ? `orders/${o.id}` : 'orders', existing ? 'PATCH' : 'POST', body);
            return {kind:'order', id:result.id, message:existing ? 'Purchase revised. Fresh approval is required.' : 'Purchase drafted. Ready for administrator review.'};
        }, existing ? 'Save revision' : 'Save draft');
        previewOrder();
    }
    function previewOrder() {
        const panel = $('pcOrderPreview'); if (!panel) return;
        const form = $('pcForm'), value = n => Number(form.querySelector(`[name="${n}"]`)?.value || 0);
        const subtotal = [...form.querySelectorAll('[data-order-row]')].reduce((total, row) => total + Math.round(Number(row.querySelector('[name=quantity]').value || 0) / Number(row.querySelector('[name=pack_size]').value || 1) * Number(row.querySelector('[name=unit_price]').value || 0) * 100), 0);
        panel.innerHTML = `<span>Estimated order total<small class="pc-muted" style="display:block">Final amounts are calculated and validated on save.</small></span><strong>${esc(money(subtotal + Math.round((value('tax') + value('shipping') - value('discount')) * 100), form.querySelector('[name=currency]').value))}</strong>`;
    }

    async function mutate(path, method, body) {
        const signature = body instanceof FormData ? [...body.entries()].map(([k,v]) => `${k}:${v instanceof File ? `${v.name}:${v.size}:${v.lastModified}` : v}`).join('|') : JSON.stringify(body);
        if (formSignature !== null && formSignature !== signature) formKey = crypto.randomUUID();
        formSignature = signature;
        return api(path, method, body, formKey);
    }

    function confirmationForm(allocationId, returning) {
        const t = state.detail.data, item = t.items.find(i => i.allocations.some(a => a.id === allocationId));
        const a = item.allocations.find(a => a.id === allocationId), maximum = returning ? a.confirmed : a.property_confirmable;
        openForm(returning ? 'Record supplies returned from property' : 'Confirm supplies at the property', `<div class="pc-alert">${returning ? 'This return reopens the outstanding need. Record any retailer refund separately on the purchase order.' : 'Use the housekeeper’s confirmation that supplies are physically at the rental. This records physical receipt even if carrier tracking has not updated.'}</div><p class="pc-form-intro">${esc(item.name)} · ${esc(t.property)} · Up to ${num(maximum)} ${esc(item.unit)}</p><div class="pc-field-grid">${field('Property', select('listing_id', options(t.listing_id ? state.ref.properties.filter(p => p.id === t.listing_id) : state.ref.properties, t.listing_id, 'Choose actual property'), 'required'))}${field('Confirming housekeeper', select('staff_id', options(activeStaff(t.listing_id), state.ref.staff_id || t.requester_staff_id, 'Choose assigned housekeeper'), 'required'))}${field(`Quantity (${item.unit})`, input('quantity', maximum, `type="number" min="0.001" max="${maximum}" step="0.001" required`))}${field('Housekeeper confirmation reference', input('reference', '', `${state.ref.staff_id ? '' : 'required'} maxlength="2000" placeholder="WhatsApp message ID or evidence reference"`))}</div>${field(returning ? 'Reason for return' : 'Confirmation note', note())}<label class="pc-check-confirm"><input type="checkbox" required><span>${returning ? 'The stated supplies were returned from this property.' : 'The assigned housekeeper has confirmed these quantities are at the correct property and usable.'}</span></label>`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const result = await mutate(`requests/${t.id}/${returning ? 'return' : 'confirm'}`, 'POST', {version:t.version, allocation_id:a.id, listing_id:Number(f.listing_id), staff_id:Number(f.staff_id), quantity:f.quantity, confirm_delivery:!returning, note:f.note, evidence:{reference:f.reference}});
            return {kind:'request', id:t.id, message:result.status === 'completed' ? 'All supplies confirmed at the property. Request completed.' : returning ? 'Return recorded. The outstanding need is visible again.' : 'Property receipt confirmed. Remaining supplies stay open.'};
        }, returning ? 'Record return' : 'Confirm at property');
    }

    function actionForm(action, id) {
        const o = state.detail.data;
        const simple = (title, html, path, bodyBuilder, message, submit = 'Save') => openForm(title, html, async form => {
            const f = Object.fromEntries(new FormData(form));
            await mutate(path, 'POST', bodyBuilder(f));
            return {kind:state.detail.kind, id:o.id, message};
        }, submit);
        if (action === 'approve-order') simple('Review & approve purchase', `<div class="pc-approval-box"><strong>${esc(o.vendor)} · ${esc(money(o.total_cents, o.currency))}</strong><br>${o.lines.map(l => `${esc(l.name)} × ${num(l.quantity)} ${esc(l.unit)}`).join('<br>')}<hr><strong>Deliver to ${esc(o.destination.label)}</strong><p class="pc-address">${esc(o.destination.address)}</p></div><label class="pc-check-confirm"><input type="checkbox" required><span>I have reviewed the products, quantities, substitutions, costs, allocations, and delivery address. I approve this purchase version.</span></label>`, `orders/${o.id}/approve`, () => ({version:o.version}), 'Purchase approved. The purchaser can now record the retailer order.', 'Approve purchase');
        else if (action === 'place-order') simple('Record the retailer purchase', `<p class="pc-form-intro">Place the order with the retailer using the approved details, then record its order number here. This action records a purchase; it does not place an external order.</p><div class="pc-approval-box">${esc(o.vendor)} · ${esc(money(o.total_cents, o.currency))}<br>Deliver to ${esc(o.destination.label)}</div>${field('Retailer order number', input('reference', '', 'required maxlength="180"'))}${field('Purchase date', input('purchased_on', today(), `type="date" max="${today()}" required`))}`, `orders/${o.id}/place`, f => ({version:o.version,...f}), 'Purchase recorded. Add its shipment or local delivery next.', 'Record purchase');
        else if (action === 'cancel-order') simple('Cancel unpurchased order', field('Cancellation reason', note('reason')), `orders/${o.id}/cancel`, f => ({version:o.version,...f}), 'Draft cancelled. Reserved request quantities are available again.', 'Cancel order');
        else if (action === 'refund') { const l = o.lines.find(l => l.id === id); simple(`Record refund · ${l.name}`, `<p class="pc-form-intro">Record a refund only after it is received. Returns and replacement needs are managed separately on the request.</p>${field(`Refund amount (${o.currency})`, input('amount', '', 'type="number" min="0.01" step="0.01" required'))}${field('Refund received on', input('refunded_on', today(), `type="date" min="${o.purchased_on}" max="${today()}" required`))}${field('Reason / reference', note('reason'))}`, `orders/${o.id}/refund`, f => ({version:o.version,line_id:l.id,...f}), 'Refund recorded in supply spending.'); }
        else if (action === 'adjust-costs') simple('Approve final receipt costs', `<div class="pc-alert">This updates recorded costs to match the final receipt. The original approved purchase remains in the audit history. Products, quantities, and delivery destinations do not change.</div>${o.lines.map(l => field(`${l.name} · final price per ${num(l.pack_size || 1)} ${l.unit} (${o.currency})`,input(`price_${l.id}`,(l.unit_price_cents/100).toFixed(2),'type="number" min="0" step="0.01" required'))).join('')}<div class="pc-field-grid">${['tax','shipping','discount'].map(n => field(n[0].toUpperCase()+n.slice(1),input(n,(o[n+'_cents']/100).toFixed(2),'type="number" min="0" step="0.01" required'))).join('')}</div>${field('Reason for cost correction',note('reason'))}<label class="pc-check-confirm"><input type="checkbox" required><span>I approve these final costs against the attached retailer receipt.</span></label>`, `orders/${o.id}/adjust-costs`, f => ({version:o.version,tax:f.tax,shipping:f.shipping,discount:f.discount,reason:f.reason,lines:o.lines.map(l => ({line_id:l.id,unit_price:f[`price_${l.id}`]}))}), 'Final receipt costs corrected and approved. Reconcile the charged amount next.', 'Approve correction');
        else if (action === 'reconcile') simple('Reconcile purchase receipt', `<p class="pc-form-intro">Attach the final retailer receipt first. The charged amount must match the recorded order total of ${esc(money(o.total_cents, o.currency))}.</p>${field(`Actual charged (${o.currency})`, input('actual_charged', (o.total_cents / 100).toFixed(2), 'type="number" min="0" step="0.01" required'))}`, `orders/${o.id}/reconcile`, f => ({version:o.version,...f}), 'Purchase receipt reconciled.');
        else if (action === 'new-shipment') simple('Add shipment or local delivery', `<p class="pc-form-intro">Include only the quantities in this package. Set a line to zero to leave it for a later shipment.</p><div class="pc-field-grid">${field('Carrier / delivery method', input('carrier', '', 'maxlength="120" placeholder="Amazon, UPS, local pickup…"'))}${field('Tracking number', input('tracking', '', 'maxlength="240"'))}${field('Expected arrival (your local time)', input('expected_at', '', 'type="datetime-local"'), '', 'pc-span-2')}</div>${o.lines.filter(l => l.unshipped > 0).map(l => field(`${l.name} · ${l.unit}`, input(`line_${l.id}`, l.unshipped, `type="number" min="0" max="${l.unshipped}" step="0.001"`))).join('')}`, `orders/${o.id}/shipments`, f => ({version:o.version,carrier:f.carrier,tracking:f.tracking,expected_at:utc(f.expected_at),lines:o.lines.filter(l => Number(f[`line_${l.id}`]) > 0).map(l => ({line_id:l.id,quantity:f[`line_${l.id}`]}))}), 'Shipment recorded. Record delivery when it arrives.');
        else if (action === 'edit-shipment' || action === 'cancel-shipment') {
            const shipment = o.shipments.find(s => s.id === id), cancelling = action === 'cancel-shipment';
            openForm(cancelling ? 'Cancel shipment record' : 'Update tracking & arrival', cancelling ? `<p class="pc-form-intro">This preserves the shipment history and makes its quantities available for a replacement shipment. It does not cancel the retailer order.</p>${field('Cancellation reason',note('cancelled_reason'))}` : `${field('Carrier',input('carrier',shipment.carrier,'maxlength="120"'))}${field('Tracking number',input('tracking',shipment.tracking,'maxlength="240"'))}${field('Expected arrival (your local time)',input('expected_at',localInput(shipment.expected_at),'type="datetime-local"'))}`, async form => {
                const f = Object.fromEntries(new FormData(form));
                await mutate(`shipments/${id}`,'PATCH',{version:o.version,...f,...(!cancelling ? {expected_at:utc(f.expected_at)} : {})});
                return {kind:'order',id:o.id,message:cancelling ? 'Shipment cancelled with history retained.' : 'Tracking and arrival estimate updated.'};
            },cancelling ? 'Cancel shipment' : 'Save tracking');
        }
        else if (action === 'deliver') simple('Record arrival at delivery destination', `<div class="pc-alert">This records arrival at ${esc(o.destination.label)}. Housekeeper confirmation at the property is still required.</div>${field('Delivered at (your local time)', input('delivered_at', localInput(new Date().toISOString()), 'type="datetime-local" required'))}`, `shipments/${id}/deliver`, f => ({version:o.version,delivered_at:utc(f.delivered_at)}), 'Delivery recorded. Requests remain open for property confirmation.', 'Record delivery');
        else if (action === 'allocate-stock') { const l = o.lines.find(l => l.id === id); simple('Allocate shared stock to a request', `<p class="pc-form-intro">${esc(l.name)} · ${num(l.unallocated)} ${esc(l.unit)} available. The requested product and unit must match.</p>${field('Request item', select('item_id', options(eligibleItems(), null, 'Choose request', 'label'), 'required'))}${field('Quantity', input('quantity', l.unallocated, `type="number" min="0.001" max="${l.unallocated}" step="0.001" required`))}`, `orders/${o.id}/allocate`, f => ({version:o.version,line_id:l.id,item_id:Number(f.item_id),quantity:f.quantity}), 'Shared stock allocated. Confirm its arrival at the property.'); }
        else if (action === 'receipt') openForm('Attach retailer receipt', `${field('Receipt file', '<input type="file" name="file" accept="application/pdf,image/png,image/jpeg" required>', 'PDF, PNG, or JPEG · maximum 10 MB.')}<p class="pc-form-intro">Keep the final receipt or invoice with this purchase for reconciliation and property expense review.</p>`, async form => { await mutate(`orders/${o.id}/receipts`, 'POST', new FormData(form)); return {kind:'order',id:o.id,message:'Receipt attached.'}; }, 'Upload receipt');
        else if (action === 'problem') {
            const item = o.items.find(i => i.allocations.some(a => a.id === id)), allocation = item.allocations.find(a => a.id === id);
            simple('Report a fulfillment problem', `<div class="pc-alert">This releases the affected quantity so a replacement can be purchased. It does not record a refund or confirm supplies at the property.</div><p class="pc-form-intro">${esc(item.name)} · Up to ${num(allocation.reportable)} ${esc(item.unit)}</p>${field('Problem',select('kind',['lost','damaged','incorrect','retailer_cancelled'].map(k => opt(k,{lost:'Lost / not received',damaged:'Damaged / unusable',incorrect:'Wrong product',retailer_cancelled:'Cancelled by retailer'}[k])).join('')))}${field(`Affected quantity (${item.unit})`,input('quantity',allocation.reportable,`type="number" min="0.001" max="${allocation.reportable}" step="0.001" required`))}${field('Problem details and source',note('reason'))}`, `requests/${o.id}/problem`, f => ({version:o.version,allocation_id:allocation.id,...f}), 'Problem recorded. The replacement need is available for purchasing.', 'Record problem');
        }
        else if (action === 'add-note') openForm('Add message or note', field('Message', note('body')), async form => { await mutate(`requests/${o.id}`, 'PATCH', {version:o.version,source:{channel:'manual',body:new FormData(form).get('body')}}); return {kind:'request',id:o.id,message:'Note added to request history.'}; });
        else if (action === 'cancel-item') { const i = o.items.find(i => i.id === id); simple('Cancel unpurchased quantity', `${field(`Quantity (${i.unit})`, input('quantity', i.to_order, `type="number" min="0.001" max="${i.to_order}" step="0.001" required`))}${field('Cancellation reason', note('reason'))}`, `requests/${o.id}/cancel-item`, f => ({version:o.version,item_id:i.id,...f}), 'Unpurchased quantity cancelled with a recorded reason.'); }
        else if (action === 'cancel-request') simple('Cancel request', field('Cancellation reason', note('reason')), `requests/${o.id}/cancel`, f => ({version:o.version,...f}), 'Request cancelled with a recorded reason.', 'Cancel request');
    }

    function renderHousekeepers() {
        if (!state.ref.can_purchase) return;
        $('pcAddHousekeeper').hidden = !state.ref.can_approve;
        const term = $('pcHousekeeperSearch').value.trim().toLocaleLowerCase();
        const property = $('pcHousekeeperProperty').value, status = $('pcHousekeeperStatus').value;
        const rows = state.ref.staff.filter(s => {
            if (status !== 'all' && s.active !== (status === 'active')) return false;
            if (property && !s.listing_ids.includes(Number(property))) return false;
            return !term || [s.name, s.phone, ...s.listing_ids.map(id => state.ref.properties.find(p => p.id === id)?.name || '')].join(' ').toLocaleLowerCase().includes(term);
        });
        $('pcHousekeeperSummary').textContent = `${rows.length} housekeeper${rows.length === 1 ? '' : 's'}${state.ref.can_approve ? ' · Edit a person to update their property assignments.' : ' · An administrator can update this roster.'}`;
        $('pcHousekeeperList').innerHTML = rows.length ? rows.map(s => {
            const properties = s.listing_ids.map(id => state.ref.properties.find(p => p.id === id)).filter(Boolean).sort((a,b) => a.name.localeCompare(b.name));
            const destinations = state.ref.locations.filter(l => l.staff_id === s.id && l.kind === 'housekeeper' && l.active);
            const chips = properties.map(p => `<span class="pc-tag">${esc(p.name)}</span>`);
            return `<article class="pc-panel pc-housekeeper-card"><div class="pc-housekeeper-heading"><div><h3>${esc(s.name)}</h3><small class="pc-muted">${s.active ? 'Active' : 'Inactive'}</small></div>${state.ref.can_approve ? button('directory-edit','Edit',`data-kind="staff" data-id="${s.id}" aria-label="Edit ${esc(s.name)}"`) : ''}</div><p class="pc-housekeeper-contact">${esc(s.phone || 'WhatsApp number not added')}</p><div class="pc-housekeeper-assigned"><strong>${properties.length} assigned propert${properties.length === 1 ? 'y' : 'ies'}</strong><div>${chips.slice(0,4).join('') || '<span class="pc-muted">No properties assigned yet.</span>'}</div>${chips.length > 4 ? `<details><summary>Show ${chips.length - 4} more</summary><div>${chips.slice(4).join('')}</div></details>` : ''}</div><div class="pc-housekeeper-delivery"><span>${destinations.length ? `${destinations.length} delivery address${destinations.length === 1 ? '' : 'es'} saved` : 'Delivery address not added'}</span>${state.ref.can_approve ? destinations.length > 1 ? '<button type="button" class="pc-text-button" data-view="directory">Manage addresses</button>' : button('housekeeper-address',destinations.length ? 'Edit address' : 'Add address',`data-id="${s.id}"`, 'pc-text-button') : ''}</div></article>`;
        }).join('') : empty('No housekeepers found', state.ref.staff.length ? 'Try a different name, property, or status.' : 'Add your housekeepers and choose the properties they look after.', state.ref.can_approve ? button('directory-edit','Add housekeeper','data-kind="staff"','pc-primary') : '');
    }

    function assignmentPicker(row) {
        return `<div class="pc-form-section"><div class="pc-form-row-heading"><h3>Assigned properties</h3><span class="pc-muted" id="pcAssignmentCount"></span></div><div class="pc-assignment-filters"><input type="search" data-assignment-search placeholder="Search properties" aria-label="Search property assignments"><select data-assignment-tag aria-label="Filter assignment group">${opt('', 'All property groups')}${[...new Set(state.ref.properties.flatMap(p => p.tags))].sort().map(t => opt(t,t)).join('')}</select></div><div class="pc-assignment-actions">${button('assign-visible','Select shown')}${button('unassign-visible','Clear shown')}</div><div class="pc-form-assignments pc-housekeeper-assignments">${[...state.ref.properties].sort((a,b) => a.name.localeCompare(b.name)).map(p => `<label data-assignment-id="${p.id}"><input type="checkbox" name="listing_ids" value="${p.id}" ${row.listing_ids?.includes(p.id) ? 'checked' : ''}><span>${esc(p.name)}<small>${esc([p.city,...p.tags].filter(Boolean).join(' · '))}</small></span></label>`).join('')}</div><p id="pcAssignmentEmpty" class="pc-muted" hidden>No properties match this search.</p></div>`;
    }

    function filterAssignments() {
        const root = $('pcForm'), search = root.querySelector('[data-assignment-search]');
        if (!search) return;
        const term = search.value.trim().toLocaleLowerCase(), tag = root.querySelector('[data-assignment-tag]').value;
        let visible = 0;
        root.querySelectorAll('[data-assignment-id]').forEach(label => {
            const p = state.ref.properties.find(p => p.id === Number(label.dataset.assignmentId));
            label.hidden = !!((tag && !p.tags.includes(tag)) || (term && ![p.name,p.city,...p.tags].join(' ').toLocaleLowerCase().includes(term)));
            if (!label.hidden) visible++;
        });
        $('pcAssignmentCount').textContent = `${root.querySelectorAll('[name=listing_ids]:checked').length} selected · ${visible} shown`;
        $('pcAssignmentEmpty').hidden = visible > 0;
    }

    function housekeeperAddressForm(id) {
        const staff = state.ref.staff.find(s => s.id === id);
        const locations = state.ref.locations.filter(l => l.staff_id === id && l.kind === 'housekeeper' && l.active);
        const location = locations[0] || {};
        openForm(`Delivery address · ${staff.name}`, `<p class="pc-form-intro">Use the verified address where this housekeeper receives supplies.</p>${field('Delivery label',input('label', location.label || `${staff.name} · supply deliveries`,'required maxlength="160"'))}${field('Full delivery address',`<textarea name="address" rows="4" required maxlength="2000">${esc(location.address || '')}</textarea>`,'Recipient, street, unit, city, state, postal code, and country.')}${field('Delivery instructions (optional)',`<textarea name="instructions" rows="2" maxlength="2000">${esc(location.instructions || '')}</textarea>`)}`, async form => {
            await mutate('directory/locations','POST',{...Object.fromEntries(new FormData(form)),...(location.id ? {id:location.id} : {}),kind:'housekeeper',staff_id:id,active:true});
            return {message:`Delivery address saved for ${staff.name}.`};
        },'Save address');
    }

    function renderDirectory() {
        if (!state.ref.can_purchase) return;
        const sections = [
            ['locations','Delivery destinations',state.ref.locations,['Location','Address','Status'],l => `<td><strong>${esc(l.label)}</strong><small>${esc(l.kind)}</small></td><td><span class="pc-address">${esc(l.address)}</span><small>${esc(l.instructions)}</small></td><td>${l.active ? 'Active' : 'Inactive'}</td>`],
            ['catalog','Supply catalog',state.ref.catalog,['Product','Standard unit','Status'],c => `<td><strong>${esc(c.name)}</strong><small>${esc(c.category)}</small></td><td>${esc(c.unit)}<small>${esc(c.pack_description)}</small></td><td>${c.active ? 'Active' : 'Inactive'}</td>`]
        ];
        $('pcDirectoryContent').innerHTML = `<div class="pc-directory-grid">${!state.ref.can_approve ? '<div class="pc-alert">An administrator manages housekeeper assignments, delivery destinations, and the supply catalog.</div>' : ''}${sections.map(([kind,title,rows,headers,rowHtml]) => `<div class="pc-panel"><div class="pc-panel-heading"><h2>${title}</h2>${state.ref.can_approve ? button('directory-edit','＋ Add',`data-kind="${kind}"`) : ''}</div>${rows.length ? `<div class="pc-table-wrap"><table class="pc-table"><thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}<th></th></tr></thead><tbody>${rows.map(row => `<tr>${rowHtml(row)}<td>${state.ref.can_approve ? button('directory-edit','Edit',`data-kind="${kind}" data-id="${row.id}"`) : ''}</td></tr>`).join('')}</tbody></table></div>` : `<div class="pc-empty">${kind === 'staff' ? 'Add housekeepers and assign their properties.' : kind === 'locations' ? 'Add a verified delivery address before drafting purchases.' : 'Standardize product names and units to make requests easier.'}</div>`}</div>`).join('')}</div>`;
    }

    function directoryForm(kind, id) {
        const row = state.ref[kind].find(x => x.id === id) || {}, editing = !!row.id;
        let html;
        if (kind === 'staff') html = `<p class="pc-form-intro">Choose the properties this person looks after. Contact details can be added later.</p><div class="pc-field-grid">${field('Housekeeper name',input('name',row.name,'required maxlength="160"'))}${field('WhatsApp number (optional)',input('phone',row.phone,'type="tel" maxlength="80" placeholder="+1…"'))}</div>${assignmentPicker(row)}<details class="pc-form-section"><summary>Optional login account</summary>${field('Cotton Candy account',select('user_id',options(state.ref.account_users,row.user_id,'No login / WhatsApp only') + (row.user_id && !state.ref.account_users.some(u => u.id === row.user_id) ? opt(row.user_id,'Previously linked account',true) : '')),'Linking an account does not grant Procurement access. Manage login permissions in Team access.')}</details>`;
        else if (kind === 'locations') html = `<div class="pc-field-grid">${field('Location label',input('label',row.label,'required maxlength="160" placeholder="e.g. Maria’s supply storage"'))}${field('Destination type',select('kind',['housekeeper','property','storage'].map(k => opt(k,{housekeeper:'Housekeeper address',property:'Rental property',storage:'Shared storage'}[k],k === (row.kind || 'housekeeper'))).join('')))}${field('Receiving housekeeper',select('staff_id',options(state.ref.staff.filter(s => s.active || s.id === row.staff_id),row.staff_id,'No housekeeper')))}${field('Property',select('listing_id',options(state.ref.properties,row.listing_id,'No property')))}${field('Full delivery address',`<textarea name="address" rows="4" required maxlength="2000">${esc(row.address || '')}</textarea>`,'Verify the recipient, street, unit, city, state, postal code, and country.','pc-span-2')}${field('Delivery instructions',`<textarea name="instructions" rows="2" maxlength="2000">${esc(row.instructions || '')}</textarea>`,'','pc-span-2')}</div>`;
        else html = `<div class="pc-field-grid">${field('Product name',input('name',row.name,'required maxlength="180" placeholder="e.g. Toilet paper"'))}${field('Category',input('category',row.category || 'supplies','required maxlength="60"'))}${field('Standard unit',input('unit',row.unit,'required maxlength="40" placeholder="rolls, bags, bottles…"'))}${field('Preferred pack / specification',input('pack_description',row.pack_description,'maxlength="160" placeholder="e.g. 24 rolls per pack, 2-ply"'))}</div><p class="pc-form-intro" style="margin-top:18px">Use the same unit for requests and purchases. Pack sizes describe the product; they do not silently change quantities.</p>`;
        html += `<label class="pc-checkbox" style="margin-top:20px"><input type="checkbox" name="active" ${row.active !== false ? 'checked' : ''}> Active</label>`;
        openForm(`${editing ? 'Edit' : 'Add'} ${kind === 'staff' ? 'housekeeper' : kind === 'locations' ? 'delivery destination' : 'supply product'}`, html, async form => {
            const data = new FormData(form), body = Object.fromEntries(data);
            if (editing) { body.id = row.id; if (kind === 'staff') body.version = row.version; }
            body.active = data.has('active');
            if (kind === 'staff') body.listing_ids = data.getAll('listing_ids').map(Number);
            await mutate(`directory/${kind}`,'POST',body);
            return {message:kind === 'staff' ? 'Housekeeper and property assignments saved.' : 'Directory updated.'};
        });
        if (kind === 'staff') { filterAssignments(); formReload = async () => { $('pcFormDialog').close(); await load(); directoryForm('staff',id); }; }
    }

    async function loadReport() {
        $('pcReport').innerHTML = '<div class="pc-panel pc-empty">Loading supply costs…</div>';
        const result = await api(`report?month=${encodeURIComponent($('pcReportMonth').value)}`);
        state.report = result;
        $('pcReport').innerHTML = !result.currencies.length ? `<div class="pc-panel">${empty('No purchasing activity this month','Recorded purchases and received refunds will appear here, grouped by currency.')}</div>` : result.currencies.map(c => `<div class="pc-report-currency"><h3 style="margin-bottom:14px">${esc(c.currency)}</h3><div class="pc-report-stats"><div class="pc-stat"><span>Purchases recorded</span><strong>${esc(money(c.purchased_cents,c.currency))}</strong><small>Tax, shipping, and discounts included</small></div><div class="pc-stat"><span>Refunds received</span><strong>${esc(money(c.refund_cents,c.currency))}</strong><small>Based on refund received date</small></div><div class="pc-stat"><span>Net supply spending</span><strong>${esc(money(c.net_cents,c.currency))}</strong><small>${esc(money(c.unallocated_cents,c.currency))} unallocated to properties</small></div></div><div class="pc-panel"><div class="pc-table-wrap"><table class="pc-table"><thead><tr><th>Property</th><th class="pc-money-cell">Allocated cost</th><th class="pc-money-cell">Confirmed at property</th><th>Categories</th></tr></thead><tbody>${c.properties.map(p => `<tr><td><strong>${esc(p.name)}</strong></td><td class="pc-money-cell">${esc(money(p.allocated_cents,c.currency))}</td><td class="pc-money-cell">${esc(money(p.confirmed_cents,c.currency))}</td><td>${Object.entries(p.categories).map(([k,v]) => `${esc(k)} ${esc(money(v,c.currency))}`).join(' · ')}</td></tr>`).join('')}${c.unallocated_cents ? `<tr><td><strong>Unallocated shared stock</strong><small>Allocation is still required</small></td><td class="pc-money-cell">${esc(money(c.unallocated_cents,c.currency))}</td><td>—</td><td>—</td></tr>` : ''}</tbody></table></div></div></div>`).join('');
        $('pcReport').innerHTML += `<p class="pc-footnote">${esc(result.basis)} Allocated cost assigns purchases to their intended property. Confirmed cost shows the portion verified there; it is not a measure of consumption.</p>`;
    }
    function exportReport() {
        const month = $('pcReportMonth').value;
        if (!month) return notify('Select a reporting month first.');
        const link = document.createElement('a');
        link.href = `/procurement/api/report.csv?month=${encodeURIComponent(month)}`;
        link.download = `procurement-${month}.csv`;
        document.body.appendChild(link); link.click(); link.remove();
    }

    function switchView(view) {
        const allowed = state.ref.can_purchase ? ['requests','orders','spending','housekeepers','directory'] : ['requests'];
        state.view = allowed.includes(view) ? view : 'requests';
        $('pcStats').hidden = state.view === 'housekeepers';
        $('pcSetup').hidden = state.view === 'housekeepers' || !state.ref.can_purchase || activeLocations().length > 0;
        document.querySelector('.pc-heading .pc-actions').hidden = state.view === 'housekeepers';
        for (const name of ['requests','orders','spending','housekeepers','directory']) $(`pc${name[0].toUpperCase()+name.slice(1)}`).hidden = name !== state.view;
        document.querySelectorAll('.pc-tabs [data-view]').forEach(el => { el.classList.toggle('active',el.dataset.view === state.view); if (el.dataset.view === state.view) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current'); });
    }

    $('pcForm').addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return;
        busy = true; const submit = $('pcFormSubmit'), caption = submit.textContent;
        submit.disabled = true; submit.textContent = 'Saving…'; $('pcFormError').hidden = true;
        try {
            const result = await formCallback(event.currentTarget);
            $('pcFormDialog').close();
            notify(result.message || 'Saved.');
            await load();
            if (result.kind) await detail(result.kind,result.id);
            else if (state.detail && $('pcDetailDialog').open) await detail(state.detail.kind,state.detail.data.id);
            if (state.view === 'spending') await loadReport();
        } catch (error) {
            if ($('pcFormDialog').open) { $('pcFormError').innerHTML = esc(error.message) + (error.status === 409 && formReload ? `<div style="margin-top:8px">${button('reload-profile','Reload latest profile')}</div>` : error.status === 409 && state.detail ? `<div style="margin-top:8px">${button('reload-detail','Reload latest record')}</div>` : ''); $('pcFormError').hidden = false; $('pcFormError').scrollIntoView({block:'nearest'}); }
            else showError(error.message);
        } finally { busy = false; submit.disabled = false; submit.textContent = caption; }
    });
    $('pcFormDialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    document.addEventListener('keydown', event => { const row = event.target.closest('tr[data-action]'); if (row && event.target === row && ['Enter',' '].includes(event.key)) { event.preventDefault(); row.click(); } });
    document.addEventListener('click', async event => {
        const close = event.target.closest('[data-close]'); if (close && !busy) { $(close.dataset.close).close(); return; }
        const remove = event.target.closest('[data-remove-row]'); if (remove && !busy) { remove.closest('[data-allocation-row], [data-order-row], [data-request-row]').remove(); previewOrder(); return; }
        const tab = event.target.closest('[data-view]');
        if (tab) {
            switchView(tab.dataset.view);
            history.replaceState(null,'',`#${state.view}`);
            if (state.view === 'spending') try { await loadReport(); } catch (error) { showError(error.message); }
            return;
        }
        const status = event.target.closest('[data-status]'); if (status) { state.status = status.dataset.status; $('pcStatusFilters').querySelectorAll('button').forEach(el => { el.classList.toggle('active',el === status); el.setAttribute('aria-pressed',String(el === status)); }); renderRequests(); return; }
        const el = event.target.closest('[data-action]'); if (!el || busy || !state.ref) return;
        const action = el.dataset.action, id = Number(el.dataset.id);
        try {
            if (action === 'reload-profile' && formReload) await formReload();
            else if (action === 'assign-visible' || action === 'unassign-visible') { $('pcForm').querySelectorAll('[data-assignment-id]').forEach(label => { if (!label.hidden) label.querySelector('input').checked = action === 'assign-visible'; }); filterAssignments(); }
            else if (action === 'housekeeper-address') housekeeperAddressForm(id);
            else if (action === 'reload-detail') { $('pcFormDialog').close(); await load(); await detail(state.detail.kind,state.detail.data.id); }
            else if (action === 'request-detail' || action === 'order-detail') await detail(action.startsWith('request') ? 'request' : 'order',id);
            else if (action === 'new-request') requestForm();
            else if (action === 'edit-request') requestForm(state.detail.data);
            else if (action === 'new-order') orderForm();
            else if (action === 'purchase-request') orderForm(state.detail.data);
            else if (action === 'revise-order') orderForm(null,state.detail.data);
            else if (action === 'add-request-row') $('pcRequestRows').insertAdjacentHTML('beforeend',requestRow());
            else if (action === 'add-order-row') { $('pcOrderRows').insertAdjacentHTML('beforeend',orderRow()); previewOrder(); }
            else if (action === 'add-allocation-row') el.closest('[data-order-row]').querySelector('[data-allocations]').insertAdjacentHTML('beforeend',allocationRow());
            else if (action === 'confirm' || action === 'return') confirmationForm(id,action === 'return');
            else if (action === 'directory-edit') directoryForm(el.dataset.kind,id);
            else if (action === 'export-report') exportReport();
            else if (action === 'review-request') openForm('Review request', `<p class="pc-form-intro">The staff owner is ${esc(state.detail.data.owner_name)}. This records a human review; it does not approve spending.</p>`, async () => { const t = state.detail.data; await mutate(`requests/${t.id}`,'PATCH',{version:t.version,review:true}); return {kind:'request',id:t.id,message:'Staff review recorded.'}; },'Mark reviewed');
            else actionForm(action,id);
        } catch (error) { if ($('pcDetailDialog').open) notify(error.message); else showError(error.message); }
    });
    $('pcForm').addEventListener('input', event => { if (event.target.matches('[data-assignment-search]')) filterAssignments(); else previewOrder(); });
    $('pcForm').addEventListener('change', event => {
        if (event.target.matches('[data-assignment-tag],[name=listing_ids]')) { filterAssignments(); return; }
        if (event.target.matches('[data-catalog-select]')) {
            const product = state.ref.catalog.find(c => c.id === Number(event.target.value)), row = event.target.closest('[data-request-row]');
            if (product) for (const name of ['name','unit','category']) row.querySelector(`[name=${name}]`).value = product[name];
            for (const name of ['name','unit']) row.querySelector(`[name=${name}]`).readOnly = !!product;
            if (!product) row.querySelector('[name=category]').value = 'supplies';
        }
        if (event.target.name === 'listing_id' && $('pcForm').querySelector('[name=requester_staff_id]')) {
            const select = $('pcForm').querySelector('[name=requester_staff_id]'); select.innerHTML = options(activeStaff(event.target.value),select.value,'Choose housekeeper');
        }
        if (event.target.name === 'listing_id' && $('pcForm').querySelector('[name=staff_id]')) {
            const staffSelect = $('pcForm').querySelector('[name=staff_id]'); staffSelect.innerHTML = options(activeStaff(event.target.value), staffSelect.value, 'Choose assigned housekeeper');
        }
        if (event.target.name === 'item_id' && event.target.closest('[data-order-row]')) {
            const row = event.target.closest('[data-order-row]'), item = eligibleItems(true).find(i => i.id === Number(event.target.value));
            if (item && !row.querySelector('[name=name]').value.trim()) {
                for (const name of ['name','unit','catalog_id']) row.querySelector(`[name=${name}]`).value = item[name] || '';
                row.querySelector('[name=quantity]').value = item.to_order;
            }
            const qty = event.target.closest('[data-allocation-row]').querySelector('[name=allocation_quantity]');
            if (item && !qty.value) qty.value = item.to_order;
        }
        if (event.target.name === 'requester_staff_id') { const staff = state.ref.staff.find(s => s.id === Number(event.target.value)); if (staff) $('pcForm').querySelector('[name=requester_name]').value = staff.name; }
        previewOrder();
    });
    for (const id of ['pcSearch','pcProperty','pcTag','pcPriority','pcMine']) $(id).addEventListener(id === 'pcSearch' ? 'input' : 'change',renderRequests);
    for (const id of ['pcHousekeeperSearch','pcHousekeeperProperty','pcHousekeeperStatus']) $(id).addEventListener(id === 'pcHousekeeperSearch' ? 'input' : 'change',renderHousekeepers);
    state.view = location.hash.slice(1) || 'requests';
    $('pcReportMonth').value = today().slice(0,7);
    $('pcReportMonth').addEventListener('change',async () => { try { await loadReport(); } catch (error) { showError(error.message); } });
    setInterval(() => { if (!document.hidden && !busy && !$('pcFormDialog').open && !$('pcDetailDialog').open) load().catch(() => {}); }, 60000);
    load().catch(error => { showError(error.message); $('pcRequestList').innerHTML = empty('Workspace could not load','Reload the page to try again. Your saved procurement records are retained.'); });
})();
