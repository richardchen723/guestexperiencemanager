/* Procurement workspace. Server-derived quantities/statuses remain authoritative. */
(() => {
    'use strict';
    const planning = window.ProcurementPlanning;
    const $ = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const state = { ref: null, requests: [], orders: [], status: 'active', view: 'requests', detail: null, report: null, purchaseTab: 'buy', buySelection: {}, requestDraft: null };
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
        $('pcProperty').innerHTML = options(ref.properties, previousProperty, 'All');
        $('pcTag').innerHTML = opt('', 'All') + [...new Set(ref.properties.flatMap(p => p.tags))].sort().map(t => opt(t, t, t === previousTag)).join('');
        const previousHousekeeperProperty = $('pcHousekeeperProperty').value;
        $('pcHousekeeperProperty').innerHTML = options(ref.properties, previousHousekeeperProperty, 'All properties');
        $('pcBuyProperty').innerHTML = options(ref.properties, $('pcBuyProperty').value, 'All properties');
        $('pcBuyDestination').innerHTML = options(activeLocations(), $('pcBuyDestination').value, 'Choose destination…', 'label');
        render();
        switchView(state.view);
        document.querySelector('[data-action="new-request"]').disabled = false;
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
        renderRequests(); renderOrders(); renderToBuy(); renderHousekeepers(); renderDirectory();
    }

    const requestSupplyNames = t => [...new Map(t.items.map(i => [JSON.stringify([i.catalog_id,i.name,i.unit]),i.name])).values()];

    function renderRequests() {
        const term = $('pcSearch').value.trim().toLowerCase(), property = $('pcProperty').value, priority = $('pcPriority').value, tag = $('pcTag').value;
        const filtered = state.requests.filter(t => {
            if (state.status === 'active' && ['completed', 'cancelled'].includes(t.status)) return false;
            if (state.status === 'attention' && (!t.flags.length && t.priority !== 'critical' || ['completed', 'cancelled'].includes(t.status))) return false;
            if (!['active', 'attention', 'all'].includes(state.status) && t.status !== state.status) return false;
            if (property && !t.listing_ids.includes(Number(property)) || priority && t.priority !== priority || tag && !t.tags.includes(tag)) return false;
            if ($('pcMine').checked && t.owner_user_id !== state.ref.user_id) return false;
            return !term || [t.code, t.title, t.property, ...t.properties.map(p => p.name), t.requester_name, t.owner_name, ...t.items.map(i => i.name)].join(' ').toLowerCase().includes(term);
        }).sort((a, b) => {
            const rank = {critical: 0, high: 1, normal: 2, planned: 3};
            return rank[a.priority] - rank[b.priority] || (a.required_by || '9999').localeCompare(b.required_by || '9999');
        });
        $('pcQueueCount').textContent = `${filtered.length} request${filtered.length === 1 ? '' : 's'}`;
        if (!filtered.length) {
            $('pcRequestList').innerHTML = empty(state.requests.length ? 'No requests match these filters' : 'Your supply requests start here', state.requests.length ? 'Adjust your search or choose All requests to see more.' : 'Capture a request with its items, property, staff owner, and deadline.', button('new-request', 'Create a request', '', 'pc-primary'));
            return;
        }
        $('pcRequestList').innerHTML = `<div class="pc-table-wrap"><table class="pc-table pc-request-table"><thead><tr><th>Request / Property</th><th>Supplies</th><th>Status</th><th>Priority</th><th>Needed by</th><th>Staff owner</th><th></th></tr></thead><tbody>${filtered.map(t => `<tr data-action="request-detail" data-id="${t.id}" tabindex="0" role="button" aria-label="Open ${esc(t.code)}: ${esc(t.title)}"><td><div class="pc-code">${esc(t.code)}</div><strong>${esc(t.property)}</strong><small>${esc(t.title)}</small>${t.tags.slice(0, 2).map(tag => `<span class="pc-tag">${esc(tag)}</span>`).join('')}</td><td><strong>${requestSupplyNames(t).length} ${requestSupplyNames(t).length === 1 ? 'supply' : 'supplies'}</strong><small>${t.property_count > 1 ? `${t.ready_properties} / ${t.property_count} properties ready` : `${t.fulfilled_items} fulfilled`} · ${esc(requestSupplyNames(t).slice(0,2).join(', '))}${requestSupplyNames(t).length > 2 ? '…' : ''}</small><div class="pc-progress"><span style="width:${t.items.length ? Math.round(t.fulfilled_items / t.items.length * 100) : 0}%"></span></div></td><td>${badge(t.status, t.status_label)}<div>${t.flags.map(f => `<span class="pc-flag">${esc(f)}</span>`).join('')}</div></td><td><span class="pc-priority ${esc(t.priority)}">${esc(t.priority[0].toUpperCase() + t.priority.slice(1))}</span></td><td class="${t.flags.includes('Overdue') ? 'pc-date-overdue' : ''}">${esc(date(t.required_by, t.timezone))}</td><td>${esc(t.owner_name)}<small>From ${esc(t.requester_name)}</small></td><td class="pc-subtle">↗</td></tr>`).join('')}</tbody></table></div>`;
    }

    function renderOrders() {
        $('pcOrderList').innerHTML = !state.orders.length ? empty('No purchases yet', 'Draft a purchase from a request, or buy shared stock and allocate it later.', button('new-order', 'Draft a purchase', '', 'pc-primary')) : `<div class="pc-table-wrap"><table class="pc-table"><thead><tr><th>Purchase</th><th>Delivery destination</th><th>Progress</th><th>Total</th><th>Financial records</th></tr></thead><tbody>${state.orders.map(o => `<tr data-action="order-detail" data-id="${o.id}" tabindex="0" role="button" aria-label="Open ${esc(o.code)}"><td><div class="pc-code">${esc(o.code)}</div><strong>${esc(o.vendor)}</strong><small>${esc(o.reference || 'Retailer order not recorded')}</small></td><td>${esc(o.destination.label)}<small>${o.lines.length} purchase line${o.lines.length === 1 ? '' : 's'}</small></td><td>${badge(o.state)}<small>${o.shipments.filter(s => s.delivered_at).length} / ${o.shipments.filter(s => !s.cancelled_reason).length} shipments delivered</small></td><td><strong>${esc(money(o.total_cents, o.currency))}</strong>${o.refund_cents ? `<small>${esc(money(o.refund_cents, o.currency))} refunded</small>` : ''}</td><td>${o.reconciled_at ? '✓ Reconciled' : 'Not reconciled'}<small>${o.attachments.length} receipt${o.attachments.length === 1 ? '' : 's'}</small></td></tr>`).join('')}</tbody></table></div>`;
    }

    const actionLabels = {request_created:'Request captured',request_updated:'Request updated',order_created:'Purchase drafted',order_revised:'Purchase revised',purchase_approve:'Purchase approved',order_approve:'Purchase approved',purchase_place:'Purchase recorded',order_place:'Purchase recorded',purchase_cancel:'Draft purchase cancelled',order_cancel:'Draft purchase cancelled',shipment_updated:'Tracking updated',shipment_cancelled:'Shipment cancelled',shipment_created:'Shipment added',shipment_delivered:'Delivery recorded',delivery_reported:'Delivery reported · property confirmation needed',property_confirmed:'Housekeeper confirmed at property',property_return:'Supplies returned from property',fulfillment_issue:'Fulfillment problem reported',item_cancelled:'Unpurchased quantity cancelled',request_cancelled:'Request cancelled',refund_recorded:'Refund received',receipt_attached:'Receipt attached',purchase_reconcile:'Receipt reconciled',receipt_costs_approved:'Final receipt costs corrected',purchase_adjust_costs:'Cost correction approved',shared_stock_allocated:'Shared stock allocated'};
    function defaultDestination(listingId) {
        if (!listingId) return '';
        const direct = activeLocations().filter(l => l.kind === 'property' && l.listing_id === listingId);
        if (direct.length) return direct.length === 1 ? direct[0].id : '';
        const keepers = activeStaff(listingId).map(s => s.id);
        const locations = activeLocations().filter(l => l.kind === 'housekeeper' && keepers.includes(l.staff_id));
        return locations.length === 1 ? locations[0].id : '';
    }
    function buyItems() {
        return eligibleItems(false, true).filter(i => i.listing_id || state.requests.find(t => t.id === i.ticket_id)?.storage_location_id);
    }
    function shownBuyItems() {
        const term = $('pcBuySearch').value.trim().toLocaleLowerCase(), property = Number($('pcBuyProperty').value);
        return buyItems().filter(i => (!property || i.listing_id === property) && (!term || i.label.toLocaleLowerCase().includes(term)));
    }
    function selectBuyItem(item) {
        if (item.requested == null) return;
        state.buySelection[item.id] = state.buySelection[item.id] || {quantity: item.to_order, location_id: defaultDestination(item.listing_id) || state.requests.find(t => t.id === item.ticket_id)?.storage_location_id || ''};
    }
    function renderToBuy() {
        if (!state.ref.can_purchase) return;
        const all = buyItems(), shown = shownBuyItems();
        const ids = new Set(all.filter(i => i.requested != null).map(i => i.id));
        for (const id of Object.keys(state.buySelection)) if (!ids.has(Number(id))) delete state.buySelection[id];
        $('pcBuyList').innerHTML = shown.length ? shown.map(i => {
            const choice = state.buySelection[i.id], request = state.requests.find(t => t.id === i.ticket_id);
            if (i.requested == null) return `<div class="pc-buy-row pc-buy-unplanned" data-buy-row="${i.id}"><div class="pc-buy-item"><span><strong>${esc(i.name)}</strong><small>${esc(request.code)} · ${esc(i.property || request.property)}</small><small>Choose how much to supply. You can purchase it in parts.</small></span></div><div class="pc-buy-plan-action">${button('plan-quantity','Set quantity',`data-id="${i.id}"`,'pc-primary')}</div></div>`;
            return `<div class="pc-buy-row" data-buy-row="${i.id}">
                <label class="pc-buy-item"><input type="checkbox" data-buy-check="${i.id}" ${choice ? 'checked' : ''}><span><strong>${esc(i.name)}</strong><small>${esc(request.code)} · ${esc(i.property || request.property)}</small><small>${num(i.to_order)} ${esc(i.unit)} still to buy${i.reserved ? ` · ${num(i.reserved)} reserved / purchased` : ''}</small></span></label>
                <label class="pc-field"><span>Buy now · ${esc(i.unit)}</span><input type="number" min="0.001" max="${i.to_order}" step="0.001" data-buy-quantity="${i.id}" value="${esc(choice?.quantity ?? i.to_order)}" aria-label="Buy ${esc(i.name)} for ${esc(i.property || request.property)} (${esc(request.code)})" ${choice ? '' : 'disabled'}></label>
                <label class="pc-field"><span>Receive at</span><select data-buy-location="${i.id}" aria-label="Receive ${esc(i.name)} for ${esc(i.property || request.property)} (${esc(request.code)})" ${choice ? '' : 'disabled'}>${options(activeLocations(), choice?.location_id || defaultDestination(i.listing_id), 'Choose verified address…', 'label')}</select></label>
            </div>`;
        }).join('') : empty('Nothing to buy here', 'Supplies still needed appear here. Staff can choose quantities before preparing a purchase.');
        updateBuySummary();
        switchPurchaseTab(state.purchaseTab);
    }
    function updateBuySummary() {
        const selected = buyItems().filter(i => state.buySelection[i.id]);
        const requests = new Set(selected.map(i => i.ticket_id)), properties = new Set(selected.map(i => i.listing_id).filter(Boolean));
        const addresses = new Set(selected.map(i => state.buySelection[i.id].location_id).filter(Boolean));
        const missing = selected.filter(i => !state.buySelection[i.id].location_id).length;
        $('pcBuySummary').textContent = selected.length ? `${selected.length} quantities · ${requests.size} request${requests.size === 1 ? '' : 's'} · ${properties.size} propert${properties.size === 1 ? 'y' : 'ies'} · ${addresses.size} destination${addresses.size === 1 ? '' : 's'}${missing ? ` · ${missing} need an address` : ''}` : 'Select the supplies you want to purchase together.';
        $('pcPrepareCombined').disabled = !selected.length;
    }
    function switchPurchaseTab(tab) {
        state.purchaseTab = tab === 'orders' ? 'orders' : 'buy';
        $('pcToBuy').hidden = state.purchaseTab !== 'buy';
        $('pcPurchaseOrders').hidden = state.purchaseTab !== 'orders';
        document.querySelectorAll('[data-purchase-tab]').forEach(el => { el.classList.toggle('active', el.dataset.purchaseTab === state.purchaseTab); el.setAttribute('aria-pressed', String(el.dataset.purchaseTab === state.purchaseTab)); });
    }
    function prepareCombined(items = null) {
        const selected = items || buyItems().filter(i => state.buySelection[i.id]);
        orderForm(null,null,planning.combinePurchases(selected,state.buySelection,activeLocations().map(l => l.id)));
    }

    function snapshotDetails(snapshot) {
        const destinations = snapshot.destinations || (snapshot.destination ? [snapshot.destination] : []);
        return `<div class="pc-approval-box"><strong>${esc(snapshot.vendor)} · ${esc(money(snapshot.total_cents,snapshot.currency))}</strong>
            ${destinations.map(d => `<p>${esc(d.label)}</p><p class="pc-address">${esc(d.address)}</p>`).join('<hr>')}
            ${(snapshot.lines || []).map(line => `<div class="pc-approval-product"><p><strong>${esc(line.name)}</strong> · ${num(line.quantity)} ${esc(line.unit)} · ${esc(money(line.unit_price_cents,snapshot.currency))} per ${num(line.pack_size || 1)} ${esc(line.unit)}</p>${line.destination ? `<p>${esc(line.destination.label)}</p>` : ''}${(line.allocations || []).map(a => `<p>${esc(a.request)} · ${esc(a.property)} · ${num(a.quantity)} ${esc(line.unit)}</p>`).join('')}${line.unallocated ? `<p>${num(line.unallocated)} ${esc(line.unit)} shared stock</p>` : ''}</div>`).join('')}</div>`;
    }
    function auditDetails(detail) {
        const rows = [], labels = {title:'Request',source:'Source',reason:'Reason',note:'Note',quantity:'Quantity',housekeeper:'Housekeeper',reference:'Retailer reference',filename:'Receipt',tracking:'Tracking number',carrier:'Carrier',expected_at:'Expected arrival',amount_cents:'Amount (minor currency units)',kind:'Problem / record type'};
        for (const [key,label] of Object.entries(labels)) if (detail[key] != null && typeof detail[key] !== 'object') rows.push([label,String(detail[key])]);
        const affected = detail.listing_ids || (detail.listing_id ? [detail.listing_id] : []);
        if (affected.length) rows.push(['Properties',affected.map(id => state.ref.properties.find(p => p.id === id)?.name || String(id)).join(', ')]);
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
        const canOrder = canPurchase && !cancelled && t.items.some(i => i.requested == null || i.to_order > 0);
        const missing = t.missing_information || [], otherFlags = t.flags.filter(flag => flag !== 'Needs information' || !missing.length);
        return `<div class="pc-detail-summary"><div class="pc-actions">${badge(t.status, t.status_label)}<span class="pc-priority ${esc(t.priority)}">${esc(t.priority)}</span></div><div class="pc-actions">${canPurchase && !cancelled ? button('edit-request', 'Edit request') + (!t.reviewed_at ? button('review-request', 'Mark reviewed') : '') : ''}${canOrder ? button('purchase-request', 'Prepare purchase', '', 'pc-primary') : ''}</div></div>
            ${missing.length ? `<section class="pc-alert pc-information-needed" aria-labelledby="pcMissingInfoTitle"><div class="pc-form-row-heading"><h3 id="pcMissingInfoTitle">Needs information</h3>${canPurchase ? button('edit-request','Update missing details') : ''}</div><ul>${missing.map(detail => `<li>${esc(detail.message)}</li>`).join('')}</ul>${!canPurchase ? `<p>Ask purchasing staff to update these request details.</p>` : ''}</section>` : ''}
            ${otherFlags.length ? `<div class="pc-alert">${otherFlags.map(esc).join(' · ')}</div>` : ''}
            ${t.possible_duplicates.length && !['completed','cancelled'].includes(t.status) ? `<div class="pc-alert">Similar open requests: ${t.possible_duplicates.map(d => `<button type="button" class="pc-text-button" data-action="request-detail" data-id="${d.id}">${esc(d.code)} — ${esc(d.title)}</button>`).join(', ')}. Check whether this is a follow-up or an additional need before purchasing.</div>` : ''}
            <div class="pc-stepper">${['Captured', 'Purchasing', 'Delivered', 'At property'].map((label, i) => `<div class="${i === 0 || i === 1 && t.status !== 'new' || i === 2 && ['awaiting_confirmation', 'completed'].includes(t.status) || i === 3 && t.status === 'completed' ? 'done' : ''}">${label}</div>`).join('')}</div>
            <dl class="pc-info-grid"><div><dt>Property</dt><dd>${esc(t.property)}</dd></div>${canPurchase ? `<div><dt>Requested by</dt><dd>${esc(t.requester_name)}</dd></div><div><dt>Staff owner</dt><dd>${esc(t.owner_name)}</dd></div>` : ''}<div><dt>Needed by</dt><dd>${esc(date(t.required_by, t.timezone))}</dd></div>${canPurchase ? `<div><dt>Staff response due</dt><dd>${esc(date(t.response_due_at,t.timezone))}</dd></div>` : ''}<div><dt>Captured</dt><dd>${esc(date(t.created_at, t.timezone))}</dd></div></dl>
            ${t.properties.length ? `<div class="pc-property-progress"><div class="pc-form-row-heading"><h3>${t.ready_properties} of ${t.property_count} properties ready</h3>${t.items.some(i => i.listing_id && i.allocations.some(a => a.property_confirmable > 0)) ? button('confirm-properties','Confirm property receipts','','pc-primary') : ''}</div>${t.properties.map(p => `<div class="pc-property-progress-row"><span><strong>${esc(p.name)}</strong><small>${esc(p.housekeepers.map(s => s.name).join(', ') || 'Housekeeper not assigned')} · ${esc(date(p.required_by,p.timezone))}</small></span>${badge(p.status,p.status_label)}</div>`).join('')}</div>` : ''}
            <h3>Requested supplies <span class="pc-subtle">${t.fulfilled_items} of ${t.items.length} fulfilled</span></h3>
            ${t.items.map(i => `<article class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(i.name)}</h4><small>${esc(i.property || t.property)} · ${esc(i.unit)} · ${esc(i.category)}</small></div>${i.requested != null && i.remaining === 0 ? badge('completed', 'Fulfilled') : `<span class="pc-muted">${i.requested == null ? 'Staff will choose quantity' : `${num(i.remaining)} remaining`}</span>`}</div><div class="pc-quantity-grid ${i.requested == null ? 'pc-quantity-unplanned' : ''}">${i.requested != null ? `<div><span>To supply</span><strong>${num(i.requested)}</strong></div>` : ''}<div><span>Ordered</span><strong>${num(i.ordered)}</strong></div><div><span>Delivered</span><strong>${num(i.delivered)}</strong></div><div><span>At property</span><strong>${num(i.confirmed)}</strong></div></div>${canPurchase && i.requested == null && !cancelled ? `<div class="pc-actions" style="margin-top:12px">${button('plan-quantity','Set quantity',`data-id="${i.id}"`)}</div>` : ''}${i.cancelled ? `<p class="pc-detail-note">${num(i.cancelled)} cancelled: ${esc(i.cancellation_reason)}</p>` : ''}
            ${i.allocations.map(a => `<div class="pc-allocation"><div>${canPurchase ? `<button type="button" class="pc-text-button" data-action="order-detail" data-id="${a.order_id}">PO-${String(a.order_id).padStart(4, '0')}</button>` : `PO-${String(a.order_id).padStart(4, '0')}`} · ${esc(a.vendor)} <span class="pc-subtle">${num(a.quantity)} ${esc(i.unit)} · ${esc(a.order_state)}</span></div><div class="pc-actions">${a.property_confirmable > 0 ? button('confirm', 'Confirm at property', `data-id="${a.id}"`, 'pc-primary') : ''}${a.confirmed > 0 ? button('return', 'Record return', `data-id="${a.id}"`) : ''}${a.reportable > 0 && a.order_state === 'ordered' ? button('problem', 'Report problem', `data-id="${a.id}"`) : ''}</div></div>${a.problem_quantity ? `<p class="pc-detail-note pc-danger-text">${num(a.problem_quantity)} ${esc(i.unit)} reported lost, damaged, incorrect, or cancelled. Replacement need remains tracked.</p>` : ''}${a.confirmations.map(c => `<p class="pc-detail-note">${c.quantity < 0 ? '↩ Returned' : '✓ Confirmed'} ${num(Math.abs(c.quantity))} at ${esc(c.property)} · ${esc(c.housekeeper)} · ${esc(date(c.at))}<br>${esc(c.note)}</p>`).join('')}`).join('')}
            ${canPurchase && i.to_order > 0 && !cancelled ? `<div style="margin-top:12px">${button('cancel-item', 'Cancel unpurchased quantity', `data-id="${i.id}"`)}</div>` : ''}</article>`).join('')}
            <p class="pc-detail-note">Delivered means arrival at the shipping destination. At property means the assigned housekeeper has verified the supplies reached the rental.</p>
            ${!t.messages_private ? `<div class="pc-detail-section"><div class="pc-form-row-heading"><h3>Messages & notes</h3>${!cancelled && !t.messages_private ? button('add-note', 'Add note') : ''}</div>${t.messages.length ? t.messages.map(m => `<div class="pc-message"><div class="pc-source-label">${esc(m.source)} · ${esc(date(m.at))}${m.message_id ? ` · ${esc(m.message_id)}` : ''}</div>${esc(m.body)}${m.url ? `<br><a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">Original message ↗</a>` : ''}</div>`).join('') : `<p class="pc-muted">${t.messages_private ? 'Shared request messages are available to purchasing staff.' : 'No messages attached.'}</p>`}</div>` : ''}
            ${t.activity.length ? `<div class="pc-detail-section"><h3>Activity history</h3>${activity(t.activity)}</div>` : ''}${canPurchase && !cancelled && !t.items.some(i => i.reserved > 0) ? `<div class="pc-detail-section">${button('cancel-request', 'Cancel request', '', 'pc-destructive')}</div>` : ''}${t.cancelled_reason ? `<div class="pc-alert">Cancelled: ${esc(t.cancelled_reason)}</div>` : ''}`;
    }

    function orderDetail(o) {
        return `<div class="pc-detail-summary"><div class="pc-actions">${badge(o.state)}<strong>${esc(money(o.total_cents, o.currency))}</strong></div><div class="pc-actions">${['draft', 'approved'].includes(o.state) ? button('revise-order', 'Revise purchase') + button('cancel-order', 'Cancel draft', '', 'pc-destructive') : ''}${o.state === 'draft' && state.ref.can_approve ? button('approve-order', 'Review & approve', '', 'pc-primary') : ''}${o.state === 'approved' ? button('place-order', 'Record purchase', '', 'pc-primary') : ''}</div></div>
            <dl class="pc-info-grid"><div><dt>Retailer order number</dt><dd>${esc(o.reference || 'Not recorded')}</dd></div><div><dt>Delivery destination</dt><dd>${o.destinations.map(d => `${esc(d.label)}<br><span class="pc-address">${esc(d.address)}</span>`).join('<hr>')}</dd></div><div><dt>Expense responsibility</dt><dd>${esc(o.responsibility)}</dd></div><div><dt>Purchase date</dt><dd>${esc(o.purchased_on || 'Not purchased')}</dd></div><div><dt>Approval</dt><dd>${esc(o.approval.actor || 'Awaiting administrator')}<br>${esc(date(o.approval.approved_at))}</dd></div><div><dt>Financial reconciliation</dt><dd>${o.reconciled_at ? `✓ ${esc(date(o.reconciled_at))}` : 'Not reconciled'}</dd></div></dl>
            <h3>Purchase lines</h3>${o.lines.map(l => `<article class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(l.name)}</h4><small>${esc(l.destination.label)} · ${num(l.quantity)} ${esc(l.unit)} · ${esc(money(l.unit_price_cents, o.currency))} per ${num(l.pack_size || 1)} ${esc(l.unit)}</small></div><strong>${esc(money(l.landed_cents, o.currency))}</strong></div>${l.substitution_reason ? `<p class="pc-detail-note">Substitution: ${esc(l.substitution_reason)}</p>` : ''}${l.allocations.map(a => `<p class="pc-detail-note"><button type="button" class="pc-text-button" data-action="request-detail" data-id="${a.ticket_id}">${esc(a.request)}</button> · ${esc(a.property)} · ${num(a.quantity)} ${esc(l.unit)} · ${esc(money(a.allocated_cents,o.currency))}</p>`).join('')}${l.unallocated > 0 ? `<p class="pc-detail-note">${num(l.unallocated)} ${esc(l.unit)} shared stock held at ${esc(l.destination.label)}.</p>` : ''}${l.refunds.map(r => `<p class="pc-detail-note">Refund ${esc(money(r.amount_cents, o.currency))} · ${esc(r.date)} · ${esc(r.reason)}</p>`).join('')}${o.state === 'ordered' ? `<div class="pc-actions" style="margin-top:12px">${l.unallocated > 0 ? button('allocate-stock', 'Allocate shared stock', `data-id="${l.id}"`) : ''}${button('refund', 'Record refund', `data-id="${l.id}"`)}</div>` : ''}</article>`).join('')}
            <div class="pc-order-total"><span>Subtotal ${esc(money(o.subtotal_cents, o.currency))}<br><span class="pc-muted">Tax ${esc(money(o.tax_cents, o.currency))} · Shipping ${esc(money(o.shipping_cents, o.currency))} · Discount −${esc(money(o.discount_cents, o.currency))}</span></span><strong>${esc(money(o.total_cents, o.currency))}</strong></div>${o.refund_cents ? `<p class="pc-detail-note">Refunds received: ${esc(money(o.refund_cents, o.currency))} · Net cost: ${esc(money(o.total_cents - o.refund_cents, o.currency))}</p>` : ''}
            ${o.state === 'ordered' ? `<div class="pc-detail-section"><div class="pc-form-row-heading"><h3>Shipments & delivery</h3>${o.lines.some(l => l.unshipped > 0) ? button('new-shipment', 'Add shipment') : ''}</div>${!o.shipments.length ? '<p class="pc-muted">No shipment recorded. Add a delivery, including local purchases.</p>' : o.shipments.map(s => `<div class="pc-item-card"><div class="pc-item-heading"><div><h4>${esc(s.carrier || 'Local / untracked delivery')}</h4><small>${esc(s.destination.label)}</small><br><small>${esc(s.tracking || 'No tracking number')} · ${s.delivered_at ? `Delivered ${esc(date(s.delivered_at))}` : `Expected ${esc(date(s.expected_at))}`}</small></div>${s.cancelled_reason ? badge('cancelled','Cancelled') : !s.delivered_at ? `<div class="pc-actions">${button('edit-shipment','Edit tracking',`data-id="${s.id}"`)}${button('cancel-shipment','Cancel shipment',`data-id="${s.id}"`)}${button('deliver','Record delivery',`data-id="${s.id}"`)}</div>` : badge('completed', 'Delivered')}</div><p class="pc-detail-note">${s.lines.map(sl => `${esc(o.lines.find(l => l.id === sl.line_id)?.name)} × ${num(sl.quantity)}`).join(' · ')}${s.cancelled_reason ? `<br>Cancelled: ${esc(s.cancelled_reason)}` : ''}</p></div>`).join('')}<p class="pc-detail-note">Recording delivery keeps requests open until the housekeeper confirms at the property.</p></div>` : ''}
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

    function* FormDataProxy(root) { for (const el of root.querySelectorAll('[name]')) yield [el.name, el.value]; }

    function captureRequestDraft() {
        const draft = state.requestDraft;
        if (!draft || !$('pcRequestMatrix')) return;
        for (const el of $('pcForm').querySelectorAll('[data-column-field]')) {
            const column = draft.columns.find(c => c.key === Number(el.dataset.column));
            if (column) column[el.dataset.columnField] = el.value;
        }
        for (const el of $('pcRequestMatrix').querySelectorAll('[data-matrix-quantity]')) {
            const column = draft.columns.find(c => c.key === Number(el.dataset.column));
            if (column) column.values[el.dataset.property] = {...column.values[el.dataset.property], quantity:el.value};
        }
        draft.ids = [...$('pcForm').querySelectorAll('[name=listing_ids]:checked')].map(el => Number(el.value));
    }
    function requestColumn() {
        const draft = state.requestDraft;
        return {key:++draft.sequence, catalog_id:null, name:'', unit:'packs', category:'supplies', values:{}};
    }
    function renderRequestProducts() {
        const draft = state.requestDraft;
        $('pcRequestProducts').innerHTML = draft.columns.map((c, index) => {
            const locked = Object.values(c.values).some(v => v.locked);
            const attr = name => `data-column="${c.key}" data-column-field="${name}"`;
            return `<div class="pc-request-product" data-product-column="${c.key}"><div class="pc-request-product-heading"><h4>Supply ${index + 1}</h4>${!locked ? button('remove-product','Remove',`data-id="${c.key}" aria-label="Remove supply ${index + 1}"`,'pc-destructive') : '<span class="pc-history-note">History retained</span>'}</div><div class="pc-field-grid">
                ${field('Standard product', `<select ${attr('catalog_id')} ${locked ? 'disabled' : ''}>${options(state.ref.catalog.filter(c => c.active || c.id === Number(c.catalog_id)),c.catalog_id,'Custom supply')}</select>`)}
                ${field('Supply', `<input value="${esc(c.name)}" ${attr('name')} required maxlength="180" ${locked || c.catalog_id ? 'readonly' : ''}>`)}
                ${field('Unit', `<input value="${esc(c.unit)}" ${attr('unit')} required maxlength="40" ${locked || c.catalog_id ? 'readonly' : ''}>`)}
                </div></div>`;
        }).join('');
    }
    function renderRequestMatrix() {
        const draft = state.requestDraft, properties = draft.ids.length ? draft.ids.map(id => state.ref.properties.find(p => p.id === id)).filter(Boolean) : [{id:0,name:'Property not yet known'}];
        $('pcRequestMatrix').innerHTML = `${draft.columns.map((c,index) => `<div class="pc-matrix-bulk"><div class="pc-bulk-copy"><label for="pcBulkQuantity${c.key}">${esc(c.name || `Supply ${index + 1}`)} · same quantity for each property</label><p>Optional. Adjust individual quantities below.</p></div><input id="pcBulkQuantity${c.key}" type="number" inputmode="decimal" min="0" step="0.001" placeholder="e.g. 12" data-bulk-column="${c.key}" aria-label="${esc(c.name || `Supply ${index + 1}`)} quantity for all properties">${button('apply-product-quantity','Apply to all',`data-id="${c.key}"`)}</div>`).join('')}<div class="pc-table-wrap"><table class="pc-table pc-quantity-matrix"><thead><tr><th>Property / housekeeper</th>${draft.columns.map(c => `<th>${esc(c.name || 'New supply')}<small>${esc(c.unit)}</small></th>`).join('')}</tr></thead><tbody>${properties.map(p => `<tr><td><strong>${esc(p.name)}</strong><small>${p.id ? esc(activeStaff(p.id).map(s => s.name).join(', ') || 'Housekeeper not assigned') : 'Clarify before purchasing'}</small></td>${draft.columns.map(c => {
            const v = c.values[p.id] || c.values[0] || {quantity:''};
            if (!c.values[p.id]) c.values[p.id] = {...v,id:null,locked:false};
            return `<td data-label="${esc(c.name || 'New supply')} · ${esc(c.unit)}"><input data-matrix-quantity data-column="${c.key}" data-property="${p.id}" value="${esc(v.quantity ?? '')}" type="number" min="0" max="99999999" step="0.001" placeholder="Optional" aria-label="${esc(p.name)} · ${esc(c.name || 'New supply')} (${esc(c.unit)})" ${v.locked ? 'readonly' : ''}>${v.locked ? '<small>Purchasing history · add another supply for changes</small>' : ''}</td>`;
        }).join('')}</tr>`).join('')}</tbody></table></div><p class="pc-form-intro">Leave blank for purchasing staff to choose · 0 = not needed at this property. Quantities with purchasing history retain their original details.</p>`;
        const staffSelect = $('pcForm').querySelector('[name=requester_staff_id]');
        const candidates = activeStaff().filter(s => draft.ids.every(id => s.listing_ids.includes(id)));
        const original = state.ref.staff.find(s => s.id === draft.originalRequesterId);
        if (original && !candidates.some(s => s.id === original.id)) candidates.push({...original,name:`Original requester · ${original.name}`});
        if (staffSelect) staffSelect.innerHTML = options(candidates, staffSelect.value || state.ref.staff_id, 'Staff request / requester below');
    }
    function requestForm(existing = null) {
        const t = existing || {}, draft = planning.requestGrid(t.items || [],t.listing_ids || []);
        draft.originalRequesterId = t.requester_staff_id;
        state.requestDraft = draft;
        if (!draft.columns.length) draft.columns.push(requestColumn());
        const picker = assignmentPicker({listing_ids:draft.ids}).replace('Assigned properties','Request properties');
        openForm(existing ? 'Edit supply request' : 'New supply request', `<p class="pc-form-intro">Select the properties and add the supplies needed. Quantities are optional; purchasing staff can choose them later. Use property groups to select several houses at once.</p>
            <div class="pc-field-grid">${field('Request title',input('title',t.title || 'Supply restock','required maxlength="240"'),'','pc-span-2')}${state.ref.can_purchase ? field('Staff owner',select('owner_user_id',options(state.ref.users,t.owner_user_id || state.ref.user_id,'Unassigned'))) : ''}${field('Priority',select('priority',state.ref.priorities.map(p => opt(p,p[0].toUpperCase()+p.slice(1),p === (t.priority || 'normal'))).join('')))}${field('Needed by (your local time)',input('required_by',localInput(t.required_by),'type="datetime-local"'),Intl.DateTimeFormat().resolvedOptions().timeZone)}${existing ? field('Staff response due (your local time)',input('response_due_at',localInput(t.response_due_at),'type="datetime-local"')) : ''}</div>
            ${picker}${!existing && state.ref.can_purchase ? field('Or shared storage',select('storage_location_id',options(activeLocations().filter(l => l.kind === 'storage'),null,'No shared storage','label')),'Use this only when no properties are selected.') : ''}
            <details class="pc-form-section"><summary>Requester details</summary><div class="pc-field-grid">${field('Requesting housekeeper',select('requester_staff_id',options(activeStaff(),t.requester_staff_id || state.ref.staff_id,'Staff request / requester below')),'Only people assigned to every selected property can request as one housekeeper.')}${field('Requester name',input('requester_name',t.requester_name || state.ref.users.find(u => u.id === state.ref.user_id)?.name || '', 'maxlength="160"'))}</div></details>
            <div class="pc-form-section"><div class="pc-form-row-heading"><h3>Supplies</h3>${button('add-request-row','＋ Add supply')}</div><div id="pcRequestProducts"></div></div>
            <details class="pc-form-section pc-optional-quantities" ${t.items?.some(i => i.quantity != null) ? 'open' : ''}><summary>Quantities (optional)</summary><p class="pc-form-intro">Add quantities if you know them, or leave them blank. Staff will decide how much to supply when preparing purchases.</p><div id="pcRequestMatrix"></div></details>
            ${!existing ? field('Original request / notes',note('body','Paste the original request or describe the need.',false)) : ''}`, async form => {
            captureRequestDraft();
            const f = Object.fromEntries(new FormData(form)), items = planning.requestChanges(draft,t.items || []);
            const staff = state.ref.staff.find(s => s.id === Number(f.requester_staff_id));
            const body = {title:f.title,requester_name:staff?.name || f.requester_name,requester_staff_id:f.requester_staff_id || null,owner_user_id:f.owner_user_id || null,priority:f.priority,required_by:utc(f.required_by),items};
            if (!existing || JSON.stringify([...draft.ids].sort()) !== JSON.stringify([...(t.listing_ids || [])].sort())) body.listing_ids = draft.ids;
            if (!existing && f.storage_location_id) body.storage_location_id = Number(f.storage_location_id);
            if (existing) {body.version = t.version;body.response_due_at = utc(f.response_due_at);}
            else if (f.body) body.source = {channel:'manual',body:f.body};
            const result = await mutate(existing ? `requests/${t.id}` : 'requests',existing ? 'PATCH' : 'POST',body);
            return {kind:'request',id:result.id,message:existing ? 'Request updated.' : 'Request captured for the selected properties.'};
        },existing ? 'Save changes' : 'Create request');
        for (const label of $('pcForm').querySelectorAll('[data-assignment-id]')) {
            if ((t.items || []).some(i => i.listing_id === Number(label.dataset.assignmentId) && (i.allocations.length || i.cancelled))) label.querySelector('input').disabled = true;
        }
        const requesterSelect = $('pcForm').querySelector('[name=requester_staff_id]');
        const original = state.ref.staff.find(s => s.id === t.requester_staff_id);
        if (original && !activeStaff().some(s => s.id === original.id)) requesterSelect.insertAdjacentHTML('beforeend',opt(original.id,`Original requester · ${original.name}`,true));
        filterAssignments(); renderRequestProducts(); renderRequestMatrix();
    }

    function eligibleItems(includeReserved = false, includeUnplanned = false) {
        return state.requests.filter(t => t.status !== 'cancelled').flatMap(t => t.items.filter(i => i.requested == null ? includeUnplanned : i.to_order > 0 || includeReserved && (state.editingOrderItemIds || []).includes(i.id)).map(i => ({...i, ticket_id: t.id, label: `${t.code} · ${i.property || t.property} · ${i.name} (${i.requested == null ? 'staff choose quantity' : `${num(i.to_order)} ${i.unit} to order`})`})));
    }
    function quantityPlanForm(itemId) {
        const ticket = state.requests.find(t => t.items.some(i => i.id === itemId));
        const item = ticket?.items.find(i => i.id === itemId);
        if (!state.ref.can_purchase || !item || ticket.status === 'cancelled' || item.requested != null) throw new Error('Reload the request to see its current quantity.');
        // Conflict recovery must reload this request even when opened from To buy.
        state.detail = {kind:'request',data:ticket};
        openForm('Choose quantity to supply', `<p class="pc-form-intro"><strong>${esc(item.name)}</strong> · ${esc(item.property || ticket.property)} · ${esc(ticket.code)}</p>${field(`Total to supply (${item.unit})`,input('quantity','','type="number" min="0.001" max="99999999" step="0.001" inputmode="decimal" required'),'Choose the total needed at this property. You can buy some now and leave the rest for later.')}`, async form => {
            const amount = new FormData(form).get('quantity');
            await mutate(`requests/${ticket.id}`,'PATCH',{version:ticket.version,items:[{id:item.id,listing_id:item.listing_id,catalog_id:item.catalog_id,name:item.name,unit:item.unit,category:item.category,quantity:amount}]});
            if (state.view === 'orders' && !$('pcDetailDialog').open) selectBuyItem({...item,requested:Number(amount),to_order:Number(amount),ticket_id:ticket.id});
            return {message:'Quantity set. Choose how much to buy now; any remainder stays outstanding.'};
        },'Set quantity');
    }
    function allocationRow(row = {}) {
        return `<div class="pc-allocation-row" data-allocation-row>${select('item_id', options(eligibleItems(true), row.item_id, 'Choose request item', 'label'), 'aria-label="Request item" required')}${input('allocation_quantity', row.quantity ?? '', 'aria-label="Allocated quantity" type="number" min="0.001" max="99999999" step="0.001" required')}<button type="button" class="pc-mini-remove" data-remove-row aria-label="Remove allocation">×</button></div>`;
    }
    function orderRow(row = {}) {
        const allocations = row.allocations || [];
        return `<div class="pc-order-row" data-order-row><div class="pc-field-grid">
            ${field('Product',input('name',row.name || '','required maxlength="180"'))}
            ${field('Purchased quantity',input('quantity',row.quantity ?? '','type="number" min="0.001" max="99999999" step="0.001" required'))}
            ${field('Unit',input('unit',row.unit || 'packs','required maxlength="40"'))}
            ${field('Units per pack',input('pack_size',row.pack_size || 1,'type="number" min="0.001" max="99999999" step="0.001" required'))}
            ${field('Price per pack',input('unit_price',row.unit_price_cents != null ? (row.unit_price_cents / 100).toFixed(2) : '','type="number" min="0" max="99999999" step="0.01" required'))}
            <button type="button" class="pc-mini-remove" data-remove-row aria-label="Remove purchase line">×</button></div>
            ${field('Receive at',select('location_id',options(activeLocations(),row.location_id || row.destination?.id,'Choose verified address…','label'),'required'))}
            <div class="pc-actions">${button('round-packs','Round to whole packs')}<span class="pc-muted" data-line-quantity-summary role="status"></span></div>
            <input name="catalog_id" type="hidden" value="${row.catalog_id || ''}">
            <details class="pc-form-section pc-allocation-details"><summary>Request allocations · ${allocations.length} quantities</summary><div class="pc-form-row-heading"><span class="pc-muted">Original request and property stay linked.</span>${button('add-allocation-row','＋ Allocate')}</div><div data-allocations>${allocations.map(allocationRow).join('')}</div></details>
            <details class="pc-form-section"><summary>Product substitution</summary>${field('Reason (required if product differs)',input('substitution_reason',row.substitution_reason || '','maxlength="2000" placeholder="Same product as requested"'))}</details></div>`;
    }

    function orderForm(ticket = null, existing = null, prepared = null) {
        state.editingOrderItemIds = existing ? existing.lines.flatMap(l => l.allocations.map(a => a.item_id)) : [];
        const o = existing || {};
        const lines = existing ? o.lines : prepared || (ticket ? ticket.items.filter(i => i.to_order > 0).map(i => ({...i,quantity:i.to_order,location_id:defaultDestination(i.listing_id),allocations:[{item_id:i.id,quantity:i.to_order}]})) : [{}]);
        openForm(existing ? 'Revise purchase' : 'Prepare purchase', `<p class="pc-form-intro">One purchase represents one actual retailer order. Matching supplies are combined per receiving address. Use separate purchases if checkout creates separate order numbers.</p>
            <div class="pc-field-grid">${field('Retailer',input('vendor',o.vendor || '','required maxlength="160" placeholder="e.g. Walmart"'))}${field('Currency',select('currency',state.ref.currencies.map(c => opt(c,c,c === (o.currency || 'USD'))).join('')))}${field('Expense responsibility',select('responsibility',['unassigned','owner','company','included'].map(r => opt(r,{unassigned:'Not assigned yet',owner:'Owner reimbursable',company:'Management company',included:'Included in management fee'}[r],r === (o.responsibility || 'unassigned'))).join('')),'','pc-span-2')}</div>
            <div class="pc-form-section"><div class="pc-form-row-heading"><h3>Products & deliveries</h3>${button('add-order-row','＋ Add product')}</div><p class="pc-form-intro">Enter quantity in the requested unit. For a 24-roll pack: units per pack 24, the actual pack price, then “Round to whole packs”. Extra rolls remain shared stock at that receiving address.</p><div id="pcOrderRows">${lines.map(orderRow).join('')}</div></div>
            <div class="pc-field-grid">${['tax','shipping','discount'].map(n => field(n[0].toUpperCase()+n.slice(1),input(n,((o[n+'_cents'] || 0)/100).toFixed(2),'type="number" min="0" max="99999999" step="0.01" required'))).join('')}</div>
            <div id="pcOrderPreview" class="pc-order-total" role="status"></div><div style="margin-top:20px">${field('Purchasing notes',`<textarea name="notes" rows="2" maxlength="5000">${esc(o.notes || '')}</textarea>`)}${existing ? field('Reason for revision',note('change_reason','Explain what changed. This revision will require a fresh approval.')) : ''}</div>`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const rows = [...form.querySelectorAll('[data-order-row]')].map(row => {
                const values = Object.fromEntries(FormDataProxy(row));
                return {name:values.name,catalog_id:values.catalog_id || null,unit:values.unit,quantity:values.quantity,location_id:Number(row.querySelector('[name=location_id]').value),pack_size:values.pack_size,unit_price:values.unit_price,substitution_reason:values.substitution_reason,
                    allocations:[...row.querySelectorAll('[data-allocation-row]')].map(a => ({item_id:Number(a.querySelector('[name=item_id]').value),quantity:a.querySelector('[name=allocation_quantity]').value}))};
            });
            const body = {vendor:f.vendor,currency:f.currency,responsibility:f.responsibility,tax:f.tax,shipping:f.shipping,discount:f.discount,notes:f.notes,lines:rows};
            if (existing) {body.version = o.version;body.change_reason = f.change_reason;}
            const result = await mutate(existing ? `orders/${o.id}` : 'orders',existing ? 'PATCH' : 'POST',body);
            if (prepared) state.buySelection = {};
            state.purchaseTab = 'orders';
            return {kind:'order',id:result.id,message:existing ? 'Purchase revised. Fresh approval is required.' : 'Purchase drafted. Selected quantities are reserved; remaining needs stay in To buy.'};
        },existing ? 'Save revision' : 'Save draft for approval');
        previewOrder();
    }
    function previewOrder() {
        const panel = $('pcOrderPreview'); if (!panel) return;
        const form = $('pcForm'), value = n => Number(form.querySelector(`[name="${n}"]`)?.value || 0);
        for (const row of form.querySelectorAll('[data-order-row]')) {
            const allocated = [...row.querySelectorAll('[name=allocation_quantity]')].reduce((n, el) => n + Number(el.value || 0), 0);
            const bought = Number(row.querySelector('[name=quantity]').value || 0), pack = Number(row.querySelector('[name=pack_size]').value || 1);
            const extra = Math.round((bought - allocated) * 1000) / 1000;
            row.querySelector('[data-line-quantity-summary]').textContent = `${num(allocated)} allocated · ${extra >= 0 ? `${num(extra)} shared stock` : `${num(-extra)} short of allocations`} · ${num(bought / pack)} packs`;
        }
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
        const listingId = item.listing_id;
        const a = item.allocations.find(a => a.id === allocationId), maximum = returning ? a.confirmed : a.property_confirmable;
        openForm(returning ? 'Record supplies returned from property' : 'Confirm supplies at the property', `<div class="pc-alert">${returning ? 'This return reopens the outstanding need. Record any retailer refund separately on the purchase order.' : 'Use the housekeeper’s confirmation that supplies are physically at the rental. This records physical receipt even if carrier tracking has not updated.'}</div><p class="pc-form-intro">${esc(item.name)} · ${esc(item.property || t.property)} · Up to ${num(maximum)} ${esc(item.unit)}</p><div class="pc-field-grid">${field('Property', select('listing_id', options(listingId ? state.ref.properties.filter(p => p.id === listingId) : state.ref.properties, listingId, 'Choose actual property'), 'required'))}${field('Confirming housekeeper', select('staff_id', options(activeStaff(listingId), state.ref.staff_id || t.requester_staff_id, 'Choose assigned housekeeper'), 'required'))}${field(`Quantity (${item.unit})`, input('quantity', maximum, `type="number" min="0.001" max="${maximum}" step="0.001" required`))}${field('Housekeeper confirmation reference', input('reference', '', `${state.ref.staff_id ? '' : 'required'} maxlength="2000" placeholder="WhatsApp message ID or evidence reference"`))}</div>${field(returning ? 'Reason for return' : 'Confirmation note', note())}<label class="pc-check-confirm"><input type="checkbox" required><span>${returning ? 'The stated supplies were returned from this property.' : 'The assigned housekeeper has confirmed these quantities are at the correct property and usable.'}</span></label>`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const result = await mutate(`requests/${t.id}/${returning ? 'return' : 'confirm'}`, 'POST', {version:t.version, allocation_id:a.id, listing_id:Number(f.listing_id), staff_id:Number(f.staff_id), quantity:f.quantity, confirm_delivery:!returning, note:f.note, evidence:{reference:f.reference}});
            return {kind:'request', id:t.id, message:result.status === 'completed' ? 'All supplies confirmed at the property. Request completed.' : returning ? 'Return recorded. The outstanding need is visible again.' : 'Property receipt confirmed. Remaining supplies stay open.'};
        }, returning ? 'Record return' : 'Confirm at property');
    }

    function batchConfirmationForm() {
        const t = state.detail.data;
        const properties = t.properties.filter(p => t.items.some(i => i.listing_id === p.id && i.allocations.some(a => a.property_confirmable > 0)));
        openForm('Confirm supplies at properties', `<p class="pc-form-intro">Select only supplies the assigned housekeeper confirms are physically at the property and usable. Reduce quantities for a partial receipt. Unselected supplies stay open.</p>
            ${properties.map(p => `<section class="pc-receipt-property"><div class="pc-form-row-heading"><h3>${esc(p.name)}</h3>${button('select-property-receipts','Select supplies',`data-id="${p.id}"`)}</div>
                ${state.ref.staff_id ? input(`staff_${p.id}`,state.ref.staff_id,'type="hidden"') : field('Confirming housekeeper',select(`staff_${p.id}`,options(activeStaff(p.id),p.housekeepers.length === 1 ? p.housekeepers[0].id : null,'Choose assigned housekeeper'),'data-receipt-staff'))}
                ${t.items.filter(i => i.listing_id === p.id).map(i => i.allocations.filter(a => a.property_confirmable > 0).map(a => `<div class="pc-receipt-row" data-receipt-property="${p.id}"><label><input type="checkbox" data-receipt-pick="${a.id}" data-property="${p.id}"><span>${esc(i.name)}<small>PO-${String(a.order_id).padStart(4,'0')} · ${esc(a.vendor)}</small></span></label><label class="pc-field"><span>At property · ${esc(i.unit)}</span><input type="number" data-receipt-quantity="${a.id}" value="${a.property_confirmable}" min="0.001" max="${a.property_confirmable}" step="0.001" aria-label="${esc(p.name)} · ${esc(i.name)} received (PO-${String(a.order_id).padStart(4,'0')})" disabled></label></div>`).join('')).join('')}</section>`).join('')}
            ${!state.ref.staff_id ? field('Housekeeper confirmation reference',input('reference','','required maxlength="2000" placeholder="Original message or evidence reference"')) : ''}
            ${field(state.ref.staff_id ? 'Note (optional)' : 'Confirmation note',note('note','Supplies checked at the property.',!state.ref.staff_id))}<label class="pc-check-confirm"><input type="checkbox" required><span>The assigned housekeepers confirmed the selected quantities are at the stated properties and usable.</span></label>`, async form => {
            const f = Object.fromEntries(new FormData(form));
            const rows = [...form.querySelectorAll('[data-receipt-pick]:checked')].map(el => ({allocation_id:Number(el.dataset.receiptPick),listing_id:Number(el.dataset.property),staff_id:Number(f[`staff_${el.dataset.property}`]),quantity:form.querySelector(`[data-receipt-quantity="${el.dataset.receiptPick}"]`).value}));
            if (!rows.length) throw new Error('Select the supplies you want to confirm.');
            const result = await mutate(`requests/${t.id}/confirm-batch`,'POST',{version:t.version,confirmations:rows,note:f.note || 'Confirmed usable supplies physically at the property.',evidence:{reference:f.reference},confirm_delivery:true});
            return {kind:'request',id:t.id,message:result.status === 'completed' ? 'All requested supplies are confirmed at their properties.' : 'Selected receipts recorded. Remaining quantities stay open.'};
        },'Confirm selected supplies');
    }

    function filterShipmentLines() {
        const id = $('pcForm').querySelector('[data-shipment-destination]')?.value;
        $('pcForm').querySelectorAll('[data-shipment-location]').forEach(el => {el.hidden = el.dataset.shipmentLocation !== id;el.querySelector('input').disabled = el.hidden;});
    }

    function actionForm(action, id) {
        const o = state.detail.data;
        const simple = (title, html, path, bodyBuilder, message, submit = 'Save') => openForm(title, html, async form => {
            const f = Object.fromEntries(new FormData(form));
            await mutate(path, 'POST', bodyBuilder(f));
            return {kind:state.detail.kind, id:o.id, message};
        }, submit);
        if (action === 'approve-order') simple('Review & approve purchase', `<div class="pc-approval-box"><strong>${esc(o.vendor)} · ${esc(money(o.total_cents, o.currency))}</strong><br>${o.lines.map(l => `<div class="pc-approval-product"><strong>${esc(l.name)} · ${num(l.quantity)} ${esc(l.unit)}</strong><p>${esc(l.destination.label)} · ${esc(money(l.landed_cents,o.currency))}</p>${l.allocations.map(a => `<p>${esc(a.request)} · ${esc(a.property)} · ${num(a.quantity)} ${esc(l.unit)}</p>`).join('')}${l.unallocated ? `<p>${num(l.unallocated)} ${esc(l.unit)} shared stock at this destination</p>` : ''}${l.substitution_reason ? `<p>Substitution: ${esc(l.substitution_reason)}</p>` : ''}</div>`).join('')}<p>Tax ${esc(money(o.tax_cents,o.currency))} · Shipping ${esc(money(o.shipping_cents,o.currency))} · Discount ${esc(money(o.discount_cents,o.currency))}</p><p>Expense responsibility: ${esc(o.responsibility)}</p><hr>${o.destinations.map(d => `<strong>Deliver to ${esc(d.label)}</strong><p class="pc-address">${esc(d.address)}</p>`).join('<hr>')}</div><label class="pc-check-confirm"><input type="checkbox" required><span>I have reviewed the products, quantities, substitutions, costs, allocations, and delivery address. I approve this purchase version.</span></label>`, `orders/${o.id}/approve`, () => ({version:o.version}), 'Purchase approved. The purchaser can now record the retailer order.', 'Approve purchase');
        else if (action === 'place-order') simple('Record the retailer purchase', `<p class="pc-form-intro">Place the order with the retailer using the approved details, then record its order number here. This action records a purchase; it does not place an external order.</p><div class="pc-approval-box">${esc(o.vendor)} · ${esc(money(o.total_cents, o.currency))}<br>Deliver to ${esc(o.destination.label)}</div>${field('Retailer order number', input('reference', '', 'required maxlength="180"'))}${field('Purchase date', input('purchased_on', today(), `type="date" max="${today()}" required`))}`, `orders/${o.id}/place`, f => ({version:o.version,...f}), 'Purchase recorded. Add its shipment or local delivery next.', 'Record purchase');
        else if (action === 'cancel-order') simple('Cancel unpurchased order', field('Cancellation reason', note('reason')), `orders/${o.id}/cancel`, f => ({version:o.version,...f}), 'Draft cancelled. Reserved request quantities are available again.', 'Cancel order');
        else if (action === 'refund') { const l = o.lines.find(l => l.id === id); simple(`Record refund · ${l.name}`, `<p class="pc-form-intro">Record a refund only after it is received. Returns and replacement needs are managed separately on the request.</p>${field(`Refund amount (${o.currency})`, input('amount', '', 'type="number" min="0.01" step="0.01" required'))}${field('Refund received on', input('refunded_on', today(), `type="date" min="${o.purchased_on}" max="${today()}" required`))}${field('Reason / reference', note('reason'))}`, `orders/${o.id}/refund`, f => ({version:o.version,line_id:l.id,...f}), 'Refund recorded in supply spending.'); }
        else if (action === 'adjust-costs') simple('Approve final receipt costs', `<div class="pc-alert">This updates recorded costs to match the final receipt. The original approved purchase remains in the audit history. Products, quantities, and delivery destinations do not change.</div>${o.lines.map(l => field(`${l.name} · final price per ${num(l.pack_size || 1)} ${l.unit} (${o.currency})`,input(`price_${l.id}`,(l.unit_price_cents/100).toFixed(2),'type="number" min="0" step="0.01" required'))).join('')}<div class="pc-field-grid">${['tax','shipping','discount'].map(n => field(n[0].toUpperCase()+n.slice(1),input(n,(o[n+'_cents']/100).toFixed(2),'type="number" min="0" step="0.01" required'))).join('')}</div>${field('Reason for cost correction',note('reason'))}<label class="pc-check-confirm"><input type="checkbox" required><span>I approve these final costs against the attached retailer receipt.</span></label>`, `orders/${o.id}/adjust-costs`, f => ({version:o.version,tax:f.tax,shipping:f.shipping,discount:f.discount,reason:f.reason,lines:o.lines.map(l => ({line_id:l.id,unit_price:f[`price_${l.id}`]}))}), 'Final receipt costs corrected and approved. Reconcile the charged amount next.', 'Approve correction');
        else if (action === 'reconcile') simple('Reconcile purchase receipt', `<p class="pc-form-intro">Attach the final retailer receipt first. The charged amount must match the recorded order total of ${esc(money(o.total_cents, o.currency))}.</p>${field(`Actual charged (${o.currency})`, input('actual_charged', (o.total_cents / 100).toFixed(2), 'type="number" min="0" step="0.01" required'))}`, `orders/${o.id}/reconcile`, f => ({version:o.version,...f}), 'Purchase receipt reconciled.');
        else if (action === 'new-shipment') {
            const locations = o.destinations.filter(d => o.lines.some(l => l.destination.id === d.id && l.unshipped > 0));
            simple('Add shipment or local delivery', `<p class="pc-form-intro">Record one delivery destination per shipment. Include only quantities in this package; leave other quantities for a later shipment.</p>
                ${field('Receiving destination',select('shipment_location_id',options(locations,locations[0]?.id,'Choose destination','label'),'required data-shipment-destination'))}
                <div class="pc-field-grid">${field('Carrier / delivery method',input('carrier','','maxlength="120" placeholder="UPS, retailer delivery, local pickup…"'))}${field('Tracking number',input('tracking','','maxlength="240"'))}${field('Expected arrival (your local time)',input('expected_at','','type="datetime-local"'),'','pc-span-2')}</div>
                ${o.lines.filter(l => l.unshipped > 0).map(l => `<div data-shipment-location="${l.destination.id}">${field(`${l.name} · ${l.unit}`,input(`line_${l.id}`,l.unshipped,`type="number" min="0" max="${l.unshipped}" step="0.001"`))}</div>`).join('')}`,`orders/${o.id}/shipments`,f => ({version:o.version,carrier:f.carrier,tracking:f.tracking,expected_at:utc(f.expected_at),lines:o.lines.filter(l => l.destination.id === Number(f.shipment_location_id) && Number(f[`line_${l.id}`]) > 0).map(l => ({line_id:l.id,quantity:f[`line_${l.id}`]}))}),'Shipment recorded. Record delivery when it arrives.');
            filterShipmentLines();
        }
        else if (action === 'edit-shipment' || action === 'cancel-shipment') {
            const shipment = o.shipments.find(s => s.id === id), cancelling = action === 'cancel-shipment';
            openForm(cancelling ? 'Cancel shipment record' : 'Update tracking & arrival', cancelling ? `<p class="pc-form-intro">This preserves the shipment history and makes its quantities available for a replacement shipment. It does not cancel the retailer order.</p>${field('Cancellation reason',note('cancelled_reason'))}` : `${field('Carrier',input('carrier',shipment.carrier,'maxlength="120"'))}${field('Tracking number',input('tracking',shipment.tracking,'maxlength="240"'))}${field('Expected arrival (your local time)',input('expected_at',localInput(shipment.expected_at),'type="datetime-local"'))}`, async form => {
                const f = Object.fromEntries(new FormData(form));
                await mutate(`shipments/${id}`,'PATCH',{version:o.version,...f,...(!cancelling ? {expected_at:utc(f.expected_at)} : {})});
                return {kind:'order',id:o.id,message:cancelling ? 'Shipment cancelled with history retained.' : 'Tracking and arrival estimate updated.'};
            },cancelling ? 'Cancel shipment' : 'Save tracking');
        }
        else if (action === 'deliver') simple('Record arrival at delivery destination', `<div class="pc-alert">This records arrival at ${esc(o.shipments.find(s => s.id === id).destination.label)}. Housekeeper confirmation at the property is still required.</div>${field('Delivered at (your local time)', input('delivered_at', localInput(new Date().toISOString()), 'type="datetime-local" required'))}`, `shipments/${id}/deliver`, f => ({version:o.version,delivered_at:utc(f.delivered_at)}), 'Delivery recorded. Requests remain open for property confirmation.', 'Record delivery');
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
        return `<div class="pc-form-section"><div class="pc-form-row-heading"><h3>Assigned properties</h3><span class="pc-muted" id="pcAssignmentCount" role="status"></span></div><ul class="pc-selected-properties" id="pcAssignmentSelected" aria-label="Selected properties" tabindex="0" hidden></ul><p id="pcAssignmentSelectedEmpty" class="pc-muted">No properties selected yet.</p><div class="pc-assignment-filters"><input type="search" data-assignment-search placeholder="Search properties" aria-label="Search property assignments"><select data-assignment-tag aria-label="Filter assignment group">${opt('', 'All property groups')}${[...new Set(state.ref.properties.flatMap(p => p.tags))].sort().map(t => opt(t,t)).join('')}</select></div><div class="pc-assignment-actions">${button('assign-visible','Select shown')}${button('unassign-visible','Clear shown')}</div><div class="pc-form-assignments pc-housekeeper-assignments">${[...state.ref.properties].sort((a,b) => a.name.localeCompare(b.name)).map(p => `<label data-assignment-id="${p.id}"><input type="checkbox" name="listing_ids" value="${p.id}" ${row.listing_ids?.includes(p.id) ? 'checked' : ''}><span>${esc(p.name)}<small>${esc([p.city,...p.tags].filter(Boolean).join(' · '))}</small></span></label>`).join('')}</div><p id="pcAssignmentEmpty" class="pc-muted" hidden>No properties match this search.</p></div>`;
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
        const selected = [...root.querySelectorAll('[name=listing_ids]:checked')].map(input => state.ref.properties.find(p => p.id === Number(input.value)));
        $('pcAssignmentSelected').innerHTML = selected.map(p => `<li><span aria-hidden="true">✓</span> ${esc(p.name)}</li>`).join('');
        $('pcAssignmentSelected').hidden = !selected.length;
        $('pcAssignmentSelectedEmpty').hidden = selected.length > 0;
        $('pcAssignmentCount').textContent = `${selected.length} selected · ${visible} shown`;
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
        const purchaseTab = event.target.closest('[data-purchase-tab]');
        if (purchaseTab) { switchPurchaseTab(purchaseTab.dataset.purchaseTab); return; }
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
            else if (action === 'assign-visible' || action === 'unassign-visible') { $('pcForm').querySelectorAll('[data-assignment-id]').forEach(label => { if (!label.hidden && !label.querySelector('input').disabled) label.querySelector('input').checked = action === 'assign-visible'; }); filterAssignments(); if ($('pcRequestMatrix')) { captureRequestDraft(); renderRequestMatrix(); } }
            else if (action === 'housekeeper-address') housekeeperAddressForm(id);
            else if (action === 'reload-detail') { $('pcFormDialog').close(); await load(); await detail(state.detail.kind,state.detail.data.id); }
            else if (action === 'request-detail' || action === 'order-detail') await detail(action.startsWith('request') ? 'request' : 'order',id);
            else if (action === 'new-request') requestForm();
            else if (action === 'edit-request') requestForm(state.detail.data);
            else if (action === 'new-order') orderForm();
            else if (action === 'plan-quantity') quantityPlanForm(id);
            else if (action === 'purchase-request') { buyItems().filter(i => i.ticket_id === state.detail.data.id).forEach(selectBuyItem); $('pcBuySearch').value = ''; $('pcBuyProperty').value = ''; $('pcDetailDialog').close(); switchView('orders'); switchPurchaseTab('buy'); renderToBuy(); notify('Set any quantities staff need to choose, then select how much to buy now. You can include other requests.'); }
            else if (action === 'revise-order') orderForm(null,state.detail.data);
            else if (action === 'add-request-row') { captureRequestDraft(); state.requestDraft.columns.push(requestColumn()); renderRequestProducts(); renderRequestMatrix(); }
            else if (action === 'remove-product') { captureRequestDraft(); state.requestDraft.columns = state.requestDraft.columns.filter(c => c.key !== id); renderRequestProducts(); renderRequestMatrix(); }
            else if (action === 'apply-product-quantity') { captureRequestDraft(); const c = state.requestDraft.columns.find(c => c.key === id), v = $('pcForm').querySelector(`[data-bulk-column="${id}"]`).value; for (const property of state.requestDraft.ids.length ? state.requestDraft.ids : [0]) if (!c.values[property]?.locked) c.values[property] = {...c.values[property],quantity:v}; renderRequestMatrix(); }
            else if (action === 'select-buy') { shownBuyItems().forEach(selectBuyItem); renderToBuy(); }
            else if (action === 'clear-buy') { state.buySelection = {}; renderToBuy(); }
            else if (action === 'set-buy-destination') { const location = $('pcBuyDestination').value; if (!location) throw new Error('Choose a receiving destination to apply.'); for (const choice of Object.values(state.buySelection)) choice.location_id = Number(location); renderToBuy(); }
            else if (action === 'prepare-combined') prepareCombined();
            else if (action === 'round-packs') { const row = el.closest('[data-order-row]'), allocated = [...row.querySelectorAll('[name=allocation_quantity]')].reduce((n,q) => n + Number(q.value || 0),0); row.querySelector('[name=quantity]').value = planning.wholePackQuantity(row.querySelector('[name=quantity]').value,allocated,row.querySelector('[name=pack_size]').value); previewOrder(); }
            else if (action === 'confirm-properties') batchConfirmationForm();
            else if (action === 'select-property-receipts') { $('pcForm').querySelectorAll(`[data-receipt-pick][data-property="${id}"]`).forEach(check => { check.checked = true; $('pcForm').querySelector(`[data-receipt-quantity="${check.dataset.receiptPick}"]`).disabled = false; }); }
            else if (action === 'add-order-row') { $('pcOrderRows').insertAdjacentHTML('beforeend',orderRow()); previewOrder(); }
            else if (action === 'add-allocation-row') el.closest('[data-order-row]').querySelector('[data-allocations]').insertAdjacentHTML('beforeend',allocationRow());
            else if (action === 'confirm' || action === 'return') confirmationForm(id,action === 'return');
            else if (action === 'directory-edit') directoryForm(el.dataset.kind,id);
            else if (action === 'export-report') exportReport();
            else if (action === 'review-request') openForm('Review request', `<p class="pc-form-intro">The staff owner is ${esc(state.detail.data.owner_name)}. This records a human review; it does not approve spending.</p>`, async () => { const t = state.detail.data; await mutate(`requests/${t.id}`,'PATCH',{version:t.version,review:true}); return {kind:'request',id:t.id,message:'Staff review recorded.'}; },'Mark reviewed');
            else actionForm(action,id);
        } catch (error) { if ($('pcFormDialog').open) { $('pcFormError').textContent = error.message; $('pcFormError').hidden = false; $('pcFormError').scrollIntoView({block:'nearest'}); } else if ($('pcDetailDialog').open) notify(error.message); else showError(error.message); }
    });
    $('pcForm').addEventListener('input', event => { if (event.target.matches('[data-assignment-search]')) filterAssignments(); else previewOrder(); });
    $('pcForm').addEventListener('change', event => {
        if (event.target.matches('[data-assignment-tag],[name=listing_ids]')) { filterAssignments(); if ($('pcRequestMatrix') && event.target.name === 'listing_ids') { captureRequestDraft(); renderRequestMatrix(); } return; }
        if (event.target.matches('[data-column-field]')) {
            captureRequestDraft();
            if (event.target.dataset.columnField === 'catalog_id') { const c = state.requestDraft.columns.find(c => c.key === Number(event.target.dataset.column)), product = state.ref.catalog.find(p => p.id === Number(event.target.value)); if (product) for (const key of ['name','unit','category']) c[key] = product[key]; else c.category = 'supplies'; renderRequestProducts(); }
            renderRequestMatrix(); return;
        }
        if (event.target.matches('[data-receipt-pick]')) { $('pcForm').querySelector(`[data-receipt-quantity="${event.target.dataset.receiptPick}"]`).disabled = !event.target.checked; return; }
        if (event.target.matches('[data-shipment-destination]')) { filterShipmentLines(); return; }
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
    for (const id of ['pcBuySearch','pcBuyProperty']) $(id).addEventListener(id === 'pcBuySearch' ? 'input' : 'change',renderToBuy);
    $('pcBuyList').addEventListener('change', event => {
        const check = event.target.dataset.buyCheck, location = event.target.dataset.buyLocation;
        if (check) { const item = buyItems().find(i => i.id === Number(check)); if (event.target.checked && item) selectBuyItem(item); else delete state.buySelection[check]; renderToBuy(); }
        if (location && state.buySelection[location]) { state.buySelection[location].location_id = Number(event.target.value) || ''; updateBuySummary(); }
    });
    $('pcBuyList').addEventListener('input', event => { const id = event.target.dataset.buyQuantity; if (id && state.buySelection[id]) { state.buySelection[id].quantity = event.target.value; updateBuySummary(); } });
    state.view = location.hash.slice(1) || 'requests';
    $('pcReportMonth').value = today().slice(0,7);
    $('pcReportMonth').addEventListener('change',async () => { try { await loadReport(); } catch (error) { showError(error.message); } });
    setInterval(() => { if (!document.hidden && !busy && !$('pcFormDialog').open && !$('pcDetailDialog').open) load().catch(() => {}); }, 60000);
    load().catch(error => { showError(error.message); $('pcRequestList').innerHTML = empty('Workspace could not load','Reload the page to try again. Your saved procurement records are retained.'); });
})();
