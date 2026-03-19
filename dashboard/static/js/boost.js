/**
 * Boost module frontend - campaign management, session control, ranking dashboard.
 */
const BoostApp = (() => {
    const API = '/boost/api';
    let campaigns = [];
    let allListings = [];
    let selectedCampaignId = null;
    let rankingChart = null;
    let pollTimer = null;

    // ------------------------------------------------------------------
    // Initialisation
    // ------------------------------------------------------------------

    function init() {
        loadCampaigns();
        loadProxies();
        loadListings();
    }

    async function loadListings() {
        try {
            allListings = await api('/listings');
        } catch (e) {
            console.error('Failed to load listings', e);
        }
    }

    function populateListingDropdown(selectedId) {
        const select = document.getElementById('campTargetListing');
        select.innerHTML = '<option value="">Select a listing...</option>';
        allListings.forEach(l => {
            const opt = document.createElement('option');
            opt.value = l.id;
            opt.textContent = `${l.name}${l.city ? ' (' + l.city + (l.state ? ', ' + l.state : '') + ')' : ''}`;
            if (selectedId && l.id === selectedId) opt.selected = true;
            select.appendChild(opt);
        });
        select.onchange = async function () {
            const lid = this.value;
            if (!lid) {
                document.getElementById('campTargetName').value = '';
                return;
            }
            try {
                const details = await api(`/listings/${lid}/details`);
                document.getElementById('campTargetName').value = details.name || '';
            } catch (e) {
                const local = allListings.find(x => x.id === parseInt(lid));
                document.getElementById('campTargetName').value = local ? local.name : '';
            }
        };
    }

    // ------------------------------------------------------------------
    // API helpers
    // ------------------------------------------------------------------

    async function api(path, opts = {}) {
        const url = API + path;
        const config = { headers: { 'Content-Type': 'application/json' }, ...opts };
        if (opts.body && typeof opts.body === 'object') {
            config.body = JSON.stringify(opts.body);
        }
        const res = await fetch(url, config);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ error: res.statusText }));
            throw new Error(err.error || res.statusText);
        }
        return res.json();
    }

    // ------------------------------------------------------------------
    // Campaigns
    // ------------------------------------------------------------------

    async function loadCampaigns() {
        try {
            campaigns = await api('/campaigns');
            renderCampaignSelector();
        } catch (e) {
            console.error('Failed to load campaigns', e);
        }
    }

    function renderCampaignSelector() {
        const select = document.getElementById('campaignSelect');
        const bar = document.getElementById('campaignBar');
        const empty = document.getElementById('emptyState');
        const content = document.getElementById('boostContent');
        const stats = document.getElementById('statsGrid');

        select.innerHTML = '<option value="">Select a campaign...</option>';
        campaigns.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = c.name + (c.is_active ? '' : ' (paused)');
            select.appendChild(opt);
        });

        if (campaigns.length === 0) {
            bar.style.display = 'none';
            content.style.display = 'none';
            stats.style.display = 'none';
            empty.style.display = '';
        } else {
            bar.style.display = '';
            empty.style.display = 'none';
            if (selectedCampaignId) {
                select.value = selectedCampaignId;
                selectCampaign(selectedCampaignId);
            }
        }
    }

    async function selectCampaign(id) {
        selectedCampaignId = id ? parseInt(id) : null;
        const content = document.getElementById('boostContent');
        const stats = document.getElementById('statsGrid');
        const btnEdit = document.getElementById('btnEditCampaign');
        const btnDelete = document.getElementById('btnDeleteCampaign');
        const btnRun = document.getElementById('btnRunNow');
        const statusEl = document.getElementById('campaignStatus');

        if (!selectedCampaignId) {
            content.style.display = 'none';
            stats.style.display = 'none';
            btnEdit.disabled = true;
            btnDelete.disabled = true;
            btnRun.disabled = true;
            statusEl.textContent = '';
            return;
        }

        content.style.display = '';
        stats.style.display = '';
        btnEdit.disabled = false;
        btnDelete.disabled = false;
        btnRun.disabled = false;

        const campaign = campaigns.find(c => c.id === selectedCampaignId);
        if (campaign) {
            statusEl.textContent = campaign.is_active ? 'Active' : 'Paused';
            statusEl.className = 'campaign-status ' + (campaign.is_active ? 'active' : 'paused');
        }

        await Promise.all([
            loadStats(),
            loadRankings(),
            loadSessions(),
        ]);

        startPolling();
    }

    function showCampaignModal(editId) {
        const modal = document.getElementById('campaignModal');
        const title = document.getElementById('campaignModalTitle');
        const form = document.getElementById('campaignForm');

        form.reset();
        document.getElementById('campaignId').value = '';

        if (editId) {
            title.textContent = 'Edit Campaign';
            const c = campaigns.find(x => x.id === editId);
            if (c) {
                document.getElementById('campaignId').value = c.id;
                document.getElementById('campName').value = c.name;
                document.getElementById('campSearchArea').value = c.search_area;
                populateListingDropdown(c.target_listing_id);
                document.getElementById('campTargetName').value = c.target_listing_name || '';
                document.getElementById('campDateStart').value = c.date_window_start;
                document.getElementById('campDateEnd').value = c.date_window_end;
                document.getElementById('campMinNights').value = c.min_nights;
                document.getElementById('campMaxNights').value = c.max_nights;
                document.getElementById('campSessionsPerDay').value = c.sessions_per_day;
                document.getElementById('campActive').checked = c.is_active;
            }
        } else {
            title.textContent = 'New Campaign';
            populateListingDropdown();
        }

        modal.style.display = '';
    }

    function closeCampaignModal(event) {
        if (event && event.target !== event.currentTarget) return;
        document.getElementById('campaignModal').style.display = 'none';
    }

    function editCampaign() {
        if (selectedCampaignId) showCampaignModal(selectedCampaignId);
    }

    async function saveCampaign(event) {
        event.preventDefault();
        const id = document.getElementById('campaignId').value;
        const data = {
            name: document.getElementById('campName').value,
            search_area: document.getElementById('campSearchArea').value,
            target_listing_id: parseInt(document.getElementById('campTargetListing').value) || null,
            target_listing_name: document.getElementById('campTargetName').value || null,
            date_window_start: document.getElementById('campDateStart').value,
            date_window_end: document.getElementById('campDateEnd').value,
            min_nights: parseInt(document.getElementById('campMinNights').value) || 2,
            max_nights: parseInt(document.getElementById('campMaxNights').value) || 5,
            sessions_per_day: parseInt(document.getElementById('campSessionsPerDay').value) || 3,
            is_active: document.getElementById('campActive').checked,
        };

        try {
            if (id) {
                await api(`/campaigns/${id}`, { method: 'PUT', body: data });
            } else {
                const created = await api('/campaigns', { method: 'POST', body: data });
                selectedCampaignId = created.id;
            }
            closeCampaignModal();
            await loadCampaigns();
            if (selectedCampaignId) {
                document.getElementById('campaignSelect').value = selectedCampaignId;
                selectCampaign(selectedCampaignId);
            }
        } catch (e) {
            alert('Error saving campaign: ' + e.message);
        }
    }

    async function deleteCampaign() {
        if (!selectedCampaignId) return;
        if (!confirm('Delete this campaign and all its data?')) return;
        try {
            await api(`/campaigns/${selectedCampaignId}`, { method: 'DELETE' });
            selectedCampaignId = null;
            await loadCampaigns();
        } catch (e) {
            alert('Error deleting campaign: ' + e.message);
        }
    }

    // ------------------------------------------------------------------
    // Sessions
    // ------------------------------------------------------------------

    async function triggerSession() {
        if (!selectedCampaignId) return;
        try {
            document.getElementById('btnRunNow').disabled = true;
            document.getElementById('btnStopSession').style.display = '';
            await api(`/campaigns/${selectedCampaignId}/trigger`, { method: 'POST' });
            showRunStatus('Session started...');
            startPolling();
        } catch (e) {
            alert('Error: ' + e.message);
            document.getElementById('btnRunNow').disabled = false;
            document.getElementById('btnStopSession').style.display = 'none';
        }
    }

    async function stopSession() {
        if (!selectedCampaignId) return;
        try {
            document.getElementById('btnStopSession').disabled = true;
            await api(`/campaigns/${selectedCampaignId}/stop`, { method: 'POST' });
            showRunStatus('Stopping session...');
        } catch (e) {
            alert('Error stopping session: ' + e.message);
        } finally {
            document.getElementById('btnStopSession').disabled = false;
        }
    }

    async function loadSessions() {
        if (!selectedCampaignId) return;
        try {
            const sessions = await api(`/campaigns/${selectedCampaignId}/sessions?limit=30`);
            renderSessions(sessions);
        } catch (e) {
            console.error('Failed to load sessions', e);
        }
    }

    function renderSessions(sessions) {
        const tbody = document.getElementById('sessionsBody');
        const empty = document.getElementById('sessionsEmpty');

        if (sessions.length === 0) {
            tbody.innerHTML = '';
            empty.style.display = '';
            return;
        }
        empty.style.display = 'none';

        tbody.innerHTML = sessions.map(s => {
            const time = s.started_at ? new Date(s.started_at).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
            }) : '--';
            const badge = `<span class="boost-badge boost-badge-${s.status}">${s.status}</span>`;
            const page = s.target_found ? s.target_page_number : '--';
            const pos = s.target_found ? s.target_position_on_page : '--';
            const proxy = s.proxy_used ? truncate(s.proxy_used, 22) : 'none';

            return `<tr class="clickable" onclick="BoostApp.showSessionDetail(${s.id}, ${JSON.stringify(s).replace(/"/g, '&quot;')})">
                <td>${time}</td>
                <td>${badge}</td>
                <td>${page}</td>
                <td>${pos}</td>
                <td title="${s.proxy_used || ''}">${proxy}</td>
            </tr>`;
        }).join('');
    }

    function showSessionDetail(id, session) {
        const modal = document.getElementById('sessionDetailModal');
        const content = document.getElementById('sessionDetailContent');

        const dates = session.search_dates
            ? `${session.search_dates.checkin} to ${session.search_dates.checkout}`
            : '--';
        const others = session.other_listings_browsed
            ? session.other_listings_browsed.map(l => `<a href="https://www.airbnb.com/rooms/${l}" target="_blank">${l}</a>`).join(', ')
            : 'none';

        let logHtml = '';
        if (session.session_log && session.session_log.length) {
            logHtml = `<div class="session-detail-section">
                <h4>Activity Log</h4>
                <ul class="session-log-list">${session.session_log.map(e =>
                    `<li><span class="session-log-time">${e.t ? new Date(e.t).toLocaleTimeString() : ''}</span>${escapeHtml(e.msg)}</li>`
                ).join('')}</ul>
            </div>`;
        }

        content.innerHTML = `
            <div class="session-detail-section">
                <h4>Overview</h4>
                <table class="boost-table">
                    <tr><td><strong>Status</strong></td><td><span class="boost-badge boost-badge-${session.status}">${session.status}</span></td></tr>
                    <tr><td><strong>Search Dates</strong></td><td>${dates}</td></tr>
                    <tr><td><strong>Found</strong></td><td>${session.target_found ? 'Yes' : 'No'}</td></tr>
                    <tr><td><strong>Page</strong></td><td>${session.target_page_number ?? '--'}</td></tr>
                    <tr><td><strong>Position</strong></td><td>${session.target_position_on_page ?? '--'}</td></tr>
                    <tr><td><strong>Pages Browsed</strong></td><td>${session.total_pages_browsed ?? 0}</td></tr>
                    <tr><td><strong>Other Listings</strong></td><td>${others}</td></tr>
                    <tr><td><strong>Proxy</strong></td><td>${session.proxy_used || 'none'}</td></tr>
                    ${session.error_message ? `<tr><td><strong>Error</strong></td><td style="color:var(--error-600)">${escapeHtml(session.error_message)}</td></tr>` : ''}
                </table>
            </div>
            ${logHtml}
        `;
        modal.style.display = '';
    }

    function closeSessionDetail(event) {
        if (event && event.target !== event.currentTarget) return;
        document.getElementById('sessionDetailModal').style.display = 'none';
    }

    // ------------------------------------------------------------------
    // Polling for running sessions
    // ------------------------------------------------------------------

    function startPolling() {
        stopPolling();
        document.getElementById('btnStopSession').style.display = '';
        pollTimer = setInterval(async () => {
            if (!selectedCampaignId) return;
            try {
                const data = await api(`/campaigns/${selectedCampaignId}/status`);
                if (data.running) {
                    const status = data.running.status;
                    const msg = status === 'cancelling' ? 'Stopping session...' : 'Session in progress...';
                    showRunStatus(msg);
                } else {
                    hideRunStatus();
                    document.getElementById('btnRunNow').disabled = false;
                    document.getElementById('btnStopSession').style.display = 'none';
                    await Promise.all([loadSessions(), loadStats(), loadRankings()]);
                    stopPolling();
                }
            } catch (_) { /* ignore polling errors */ }
        }, 5000);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function showRunStatus(msg) {
        const el = document.getElementById('runStatus');
        el.innerHTML = `<div class="spinner-sm"></div> ${escapeHtml(msg)}`;
        el.style.display = '';
        document.getElementById('runHint').style.display = 'none';
    }

    function hideRunStatus() {
        document.getElementById('runStatus').style.display = 'none';
        document.getElementById('runHint').style.display = '';
        document.getElementById('btnStopSession').style.display = 'none';
    }

    // ------------------------------------------------------------------
    // Stats
    // ------------------------------------------------------------------

    async function loadStats() {
        if (!selectedCampaignId) return;
        try {
            const s = await api(`/campaigns/${selectedCampaignId}/stats`);
            document.getElementById('statTodayPosition').textContent =
                s.today_position != null ? '#' + s.today_position : '--';
            document.getElementById('statTodayFound').textContent =
                s.today_found_rate != null ? `${s.today_found_rate}% found (${s.today_sessions} sessions)` : '';
            document.getElementById('statWeekAvg').textContent =
                s.week_avg_position != null ? '#' + s.week_avg_position : '--';
            document.getElementById('statBestEver').textContent =
                s.best_position_ever != null ? '#' + s.best_position_ever : '--';
            document.getElementById('statTotalSessions').textContent = s.total_sessions || 0;
        } catch (_) { /* ignore */ }
    }

    // ------------------------------------------------------------------
    // Rankings & Chart
    // ------------------------------------------------------------------

    async function loadRankings() {
        if (!selectedCampaignId) return;
        try {
            const rankings = await api(`/campaigns/${selectedCampaignId}/rankings`);
            renderRankingsTable(rankings);
            renderChart(rankings);
        } catch (e) {
            console.error('Failed to load rankings', e);
        }
    }

    function renderRankingsTable(rankings) {
        const tbody = document.getElementById('rankingsBody');
        const empty = document.getElementById('rankingsEmpty');

        if (rankings.length === 0) {
            tbody.innerHTML = '';
            empty.style.display = '';
            return;
        }
        empty.style.display = 'none';

        tbody.innerHTML = rankings.map(r => `
            <tr>
                <td>${r.date}</td>
                <td>${r.avg_position != null ? r.avg_position.toFixed(1) : '--'}</td>
                <td>${r.avg_page_number != null ? r.avg_page_number.toFixed(1) : '--'}</td>
                <td>${r.best_position ?? '--'}</td>
                <td>${r.worst_position ?? '--'}</td>
                <td>${r.sessions_count}</td>
                <td>${r.found_count} / ${r.sessions_count}</td>
            </tr>
        `).join('');
    }

    function renderChart(rankings) {
        const canvas = document.getElementById('rankingChart');
        const emptyEl = document.getElementById('chartEmpty');

        const withData = rankings.filter(r => r.avg_position != null).reverse();

        if (withData.length === 0) {
            canvas.style.display = 'none';
            emptyEl.style.display = '';
            return;
        }
        canvas.style.display = '';
        emptyEl.style.display = 'none';

        const labels = withData.map(r => r.date);
        const avgData = withData.map(r => r.avg_position);
        const bestData = withData.map(r => r.best_position);

        if (rankingChart) {
            rankingChart.destroy();
        }

        rankingChart = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Avg Position',
                        data: avgData,
                        borderColor: '#2563EB',
                        backgroundColor: 'rgba(37, 99, 235, 0.08)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.3,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                    },
                    {
                        label: 'Best Position',
                        data: bestData,
                        borderColor: '#10B981',
                        borderWidth: 1.5,
                        borderDash: [4, 3],
                        fill: false,
                        tension: 0.3,
                        pointRadius: 2,
                        pointHoverRadius: 4,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'top', labels: { font: { size: 12 } } },
                    tooltip: {
                        callbacks: {
                            label: ctx => `${ctx.dataset.label}: #${ctx.parsed.y.toFixed(1)}`
                        }
                    },
                },
                scales: {
                    y: {
                        reverse: true,
                        beginAtZero: false,
                        title: { display: true, text: 'Position (lower is better)' },
                        ticks: { precision: 0 },
                    },
                    x: {
                        ticks: { maxTicksLimit: 15 },
                    },
                },
            },
        });
    }

    // ------------------------------------------------------------------
    // Proxies
    // ------------------------------------------------------------------

    async function loadProxies() {
        try {
            const proxies = await api('/proxies');
            renderProxies(proxies);
        } catch (_) { /* ignore */ }
    }

    function renderProxies(proxies) {
        const tbody = document.getElementById('proxiesBody');
        const empty = document.getElementById('proxiesEmpty');
        const delBtn = document.getElementById('proxyDeleteSelected');

        if (proxies.length === 0) {
            tbody.innerHTML = '';
            empty.style.display = '';
            if (delBtn) delBtn.style.display = 'none';
            return;
        }
        empty.style.display = 'none';
        if (delBtn) delBtn.style.display = '';

        tbody.innerHTML = proxies.map(p => {
            const statusClass = p.is_active ? 'boost-badge-active' : 'boost-badge-inactive';
            const statusText = p.is_active ? 'active' : 'off';
            const toggleLabel = p.is_active ? 'Disable' : 'Enable';

            return `<tr>
                <td><input type="checkbox" class="proxy-select-cb" value="${p.id}"></td>
                <td>${escapeHtml(p.host)}:${p.port}</td>
                <td>${p.username ? escapeHtml(p.username) : '--'}</td>
                <td><span class="boost-badge ${statusClass}">${statusText}</span>${p.fail_count > 0 ? ` <small>(${p.fail_count} fails)</small>` : ''}</td>
                <td>
                    <button class="btn btn-sm btn-secondary" onclick="BoostApp.toggleProxy(${p.id}, ${!p.is_active})">${toggleLabel}</button>
                    <button class="btn btn-sm btn-danger" onclick="BoostApp.deleteProxy(${p.id})">Delete</button>
                </td>
            </tr>`;
        }).join('');
    }

    function showProxyModal() {
        document.getElementById('proxyModal').style.display = '';
        document.getElementById('proxyText').value = '';
    }

    function closeProxyModal(event) {
        if (event && event.target !== event.currentTarget) return;
        document.getElementById('proxyModal').style.display = 'none';
    }

    async function importProxies() {
        const text = document.getElementById('proxyText').value.trim();
        if (!text) return;
        try {
            const result = await api('/proxies/import', { method: 'POST', body: { text } });
            alert(`Added: ${result.added}, Updated: ${result.updated}`);
            closeProxyModal();
            loadProxies();
        } catch (e) {
            alert('Error importing proxies: ' + e.message);
        }
    }

    async function clearProxies() {
        if (!confirm('Remove all proxies?')) return;
        try {
            await api('/proxies/clear', { method: 'POST' });
            loadProxies();
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    async function toggleProxy(id, active) {
        try {
            await api(`/proxies/${id}/toggle`, { method: 'POST', body: { is_active: active } });
            loadProxies();
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    async function deleteProxy(id) {
        if (!confirm('Delete this proxy?')) return;
        try {
            await api('/proxies/delete', { method: 'POST', body: { ids: [id] } });
            loadProxies();
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    async function deleteSelectedProxies() {
        const checkboxes = document.querySelectorAll('.proxy-select-cb:checked');
        const ids = Array.from(checkboxes).map(cb => parseInt(cb.value));
        if (ids.length === 0) { alert('No proxies selected.'); return; }
        if (!confirm(`Delete ${ids.length} selected proxy(s)?`)) return;
        try {
            const result = await api('/proxies/delete', { method: 'POST', body: { ids } });
            alert(`Deleted ${result.deleted} proxy(s).`);
            loadProxies();
        } catch (e) {
            alert('Error: ' + e.message);
        }
    }

    function toggleAllProxyCbs(master) {
        document.querySelectorAll('.proxy-select-cb').forEach(cb => { cb.checked = master.checked; });
    }

    // ------------------------------------------------------------------
    // Utilities
    // ------------------------------------------------------------------

    function truncate(str, len) {
        if (!str) return '';
        return str.length > len ? str.substring(0, len) + '...' : str;
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // Auto-init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', init);

    // Public API
    return {
        showCampaignModal,
        closeCampaignModal,
        saveCampaign,
        selectCampaign,
        editCampaign,
        deleteCampaign,
        triggerSession,
        stopSession,
        showSessionDetail,
        closeSessionDetail,
        showProxyModal,
        closeProxyModal,
        importProxies,
        clearProxies,
        toggleProxy,
        deleteProxy,
        deleteSelectedProxies,
        toggleAllProxyCbs,
    };
})();
