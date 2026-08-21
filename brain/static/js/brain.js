const app = document.getElementById('brainApp');
const page = app?.dataset.page || 'today';
const portfolioId = app?.dataset.portfolioId || '';
const content = document.getElementById('pageContent');
const loading = document.getElementById('loadingState');
const errorState = document.getElementById('errorState');
const pageTitle = document.getElementById('pageTitle');
const refreshButton = document.getElementById('refreshButton');
const runButton = document.getElementById('runButton');
const globalPortfolioFilter = document.getElementById('globalPortfolioFilter');
const initialParams = new URLSearchParams(window.location.search);
let selectedPortfolioId = portfolioId || initialParams.get('portfolio_id') || localStorage.getItem('brainPortfolioId') || '';

const titles = {
    'today': 'Today',
    'portfolios': 'Portfolios',
    'portfolio-detail': 'Portfolio Detail',
    'todos': 'Manager Todos',
    'signals': 'Signal Inbox',
    'booking-health': 'Booking Health',
    'open-loops': 'Open Loops',
    'ask': 'Ask Brain',
    'settings': 'Settings'
};

document.querySelectorAll('.brain-nav a').forEach(link => {
    if (link.dataset.nav === page || (page === 'portfolio-detail' && link.dataset.nav === 'portfolios')) {
        link.classList.add('active');
    }
});

pageTitle.textContent = titles[page] || 'Today';
refreshButton?.addEventListener('click', () => loadPage());
if (runButton) runButton.hidden = page !== 'settings';
runButton?.addEventListener('click', async () => {
    runButton.disabled = true;
    runButton.textContent = 'Running...';
    try {
        await api('/api/brain/runs/manual', { method: 'POST' });
        await loadPage();
    } catch (error) {
        showError(error);
    } finally {
        runButton.disabled = false;
        runButton.textContent = 'Run Brain';
    }
});

bootstrap();

async function bootstrap() {
    try {
        await initGlobalPortfolioFilter();
    } catch (error) {
        console.warn('Portfolio filter failed to initialize', error);
    }
    await loadPage();
}

async function loadPage() {
    showLoading();
    try {
        if (page === 'today') renderToday(await api(scopedApiUrl('/api/brain/today')));
        else if (page === 'portfolios') renderPortfolios(await api('/api/brain/portfolios'));
        else if (page === 'portfolio-detail') renderPortfolioDetail(await api(`/api/brain/portfolios/${portfolioId}`));
        else if (page === 'todos') renderTodos(await api(scopedApiUrl('/api/brain/todos', { limit: 9 })));
        else if (page === 'signals') renderSignals(await api(scopedApiUrl('/api/brain/signals', { status: 'active' })));
        else if (page === 'booking-health') renderBookingHealth(await api(scopedApiUrl('/api/brain/booking-health')));
        else if (page === 'open-loops') renderOpenLoops(await api(scopedApiUrl('/api/brain/open-loops')));
        else if (page === 'ask') renderAsk();
        else if (page === 'settings') renderSettings();
        showContent();
    } catch (error) {
        showError(error);
    }
}

async function api(url, options = {}) {
    const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
}

async function initGlobalPortfolioFilter() {
    if (!globalPortfolioFilter) return;
    const data = await api('/api/brain/portfolios');
    const options = (data.items || []).map(item => (
        `<option value="${escapeHtml(item.portfolio_id)}">${escapeHtml(item.name)}</option>`
    )).join('');
    globalPortfolioFilter.innerHTML = `<option value="">All portfolios</option>${options}`;
    if (selectedPortfolioId && ![...globalPortfolioFilter.options].some(option => option.value === String(selectedPortfolioId))) {
        selectedPortfolioId = '';
        localStorage.removeItem('brainPortfolioId');
    }
    globalPortfolioFilter.value = selectedPortfolioId;
    globalPortfolioFilter.addEventListener('change', () => {
        selectedPortfolioId = globalPortfolioFilter.value;
        if (selectedPortfolioId) localStorage.setItem('brainPortfolioId', selectedPortfolioId);
        else localStorage.removeItem('brainPortfolioId');

        if (page === 'portfolio-detail') {
            window.location.href = selectedPortfolioId ? `/portfolios/${selectedPortfolioId}` : '/portfolios';
            return;
        }

        const params = new URLSearchParams(window.location.search);
        if (selectedPortfolioId) params.set('portfolio_id', selectedPortfolioId);
        else params.delete('portfolio_id');
        const query = params.toString();
        window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
        loadPage();
    });
}

function scopedApiUrl(path, params = {}) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') query.set(key, value);
    });
    if (selectedPortfolioId) query.set('portfolio_id', selectedPortfolioId);
    const queryString = query.toString();
    return `${path}${queryString ? `?${queryString}` : ''}`;
}

function showLoading() {
    loading.hidden = false;
    errorState.hidden = true;
    content.hidden = true;
}

function showContent() {
    loading.hidden = true;
    errorState.hidden = true;
    content.hidden = false;
}

function showError(error) {
    loading.hidden = true;
    content.hidden = true;
    errorState.hidden = false;
    errorState.textContent = error.message || String(error);
}

function renderToday(data) {
    const memoryHighlights = data.memory_highlights || [];
    const operatingSnapshot = data.operating_snapshot || [];
    const portfolioHealth = data.portfolio_health || [];
    const bookingWarnings = data.booking_health_warnings || [];
    const openLoops = data.open_loops || [];
    const latestBriefs = data.latest_briefs || [];
    content.innerHTML = `
        ${readinessStrip(data.readiness || {})}
        ${operatingSnapshotSection(operatingSnapshot)}
        <div class="section-grid">
            ${detailsSection('Portfolio Pulse', portfolioHealth.length, cardList(portfolioHealth.map(portfolioCard), 'No portfolios yet.'), 'half', true)}
            ${detailsSection('WhatsApp Misses', memoryHighlights.length, cardList(memoryHighlights.map(memoryCard), 'No WhatsApp memory highlights yet.'), 'half')}
            ${detailsSection('Booking Revenue', bookingWarnings.length, cardList(bookingWarnings.map(item => healthRow(item, { compact: true })), 'No booking warnings yet.'), 'half')}
            ${detailsSection('Open Loops', openLoops.length, cardList(openLoops.map(loopRow), 'No open loops yet.'), 'half')}
            ${detailsSection('Latest Briefs', latestBriefs.length, cardList(latestBriefs.map(briefCard), 'No briefs generated yet.'))}
        </div>
    `;
    bindStatusButtons();
}

function operatingSnapshotSection(items) {
    const cards = (items || []).slice(0, 7).map(snapshotCard).join('');
    return `
        <section class="section operating-snapshot action-queue">
            <div class="row-topline">
                <h2>Needs Attention</h2>
                <span class="badge">${Number((items || []).length)} surfaced</span>
            </div>
            <div class="snapshot-grid action-grid">${cards || '<div class="empty">No operating snapshot yet.</div>'}</div>
        </section>
    `;
}

function snapshotCard(item) {
    const meta = [
        item.portfolio_name,
        item.listing_name,
        item.rank_score !== undefined && item.rank_score !== null ? `score ${Math.round(Number(item.rank_score || 0))}` : ''
    ].filter(Boolean).join(' · ');
    const answer = item.answer || item.title || '';
    const showTitle = item.title && item.title !== answer;
    return `
        <article class="snapshot-card action-card ${escapeHtml(item.key || '')}">
            <div class="row-topline">
                <h3>${escapeHtml(item.question || '')}</h3>
                <span class="badge ${escapeHtml(item.severity || item.status || 'ok')}">${escapeHtml(label(item.status || item.severity || 'ok'))}</span>
            </div>
            <strong>${escapeHtml(answer)}</strong>
            ${meta ? `<p class="meta-line">${escapeHtml(meta)}</p>` : ''}
            ${showTitle ? `<p class="meta-line">${escapeHtml(item.title)}</p>` : ''}
            <p>${escapeHtml(truncateText(item.summary || '', 180))}</p>
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(item.suggested_action || '', 180))}</p>
        </article>
    `;
}

function readinessStrip(readiness) {
    const components = Array.isArray(readiness.components) ? readiness.components : [];
    const status = readiness.status || 'missing';
    if (status === 'ok') {
        const latest = components.find(component => component.key === 'scheduled_reads')?.detail;
        return `
            <section class="readiness-compact">
                <span class="status-dot ok"></span>
                <span>${escapeHtml(readiness.message || 'Data current')}</span>
                ${latest ? `<span class="meta-line">Latest read ${escapeHtml(formatDateTime(latest))}</span>` : ''}
            </section>
        `;
    }
    const componentBadges = components.map(component => {
        const rawDetail = component.key === 'scheduled_reads' && component.detail ? formatDateTime(component.detail) : component.detail;
        const detail = rawDetail ? ` · ${rawDetail}` : '';
        return `<span class="badge ${escapeHtml(component.status || 'missing')}">${escapeHtml(component.label || component.key || 'Source')}: ${escapeHtml(label(component.status || 'missing'))}${escapeHtml(detail)}</span>`;
    }).join('');
    return `
        <section class="section readiness-strip">
            <div class="row-topline">
                <h2>Brain Readiness</h2>
                <span class="badge ${escapeHtml(readiness.status || 'missing')}">${escapeHtml(label(readiness.status || 'missing'))}</span>
            </div>
            <p class="meta-line">${escapeHtml(readiness.message || 'Readiness has not been calculated yet.')}</p>
            ${componentBadges ? `<div class="badge-row">${componentBadges}</div>` : ''}
        </section>
    `;
}

function renderPortfolios(data) {
    const items = selectedPortfolioId
        ? (data.items || []).filter(item => String(item.portfolio_id) === String(selectedPortfolioId))
        : (data.items || []);
    content.innerHTML = `<div class="section-grid">${section(selectedPortfolioId ? 'Selected Portfolio' : 'All Portfolios', cardList(items.map(portfolioCard), 'No portfolios yet.'))}</div>`;
}

function renderPortfolioDetail(data) {
    pageTitle.textContent = data.portfolio.name;
    content.innerHTML = `
        <div class="section-grid">
            ${section('Portfolio Summary', `
                <p>${escapeHtml(data.portfolio.description || 'No description')}</p>
                <div class="badge-row"><span class="badge ${escapeHtml(data.portfolio.status)}">${escapeHtml(data.portfolio.status)}</span></div>
            `)}
            ${section('Properties With Context', cardList(data.listings.map(listingRow), 'No mapped properties yet.'), 'half')}
            ${section('Top Signals', cardList(data.top_signals.map(signalCard), 'No signals for this portfolio.'), 'half')}
            ${section('Booking Health', cardList(data.booking_health.map(healthRow), 'No booking-health snapshots.'), 'half')}
            ${section('Open Loops', cardList(data.open_loops.map(loopRow), 'No open loops for this portfolio.'), 'half')}
        </div>
    `;
    bindStatusButtons();
}

function renderTodos(data) {
    const items = data.items || [];
    content.innerHTML = `
        <div class="section-grid">
            ${section('Manager Todo List', `
                <div class="todo-summary">
                    <span class="badge">${items.length} items</span>
                    <span class="meta-line">Short list from unclosed team conversations and high-ROI Brain signals.</span>
                </div>
                ${cardList(items.map((item, index) => todoCard(item, index)), 'No manager todos right now.')}
            `)}
        </div>
    `;
    bindStatusButtons();
}

function renderSignals(data) {
    content.innerHTML = `
        <div class="section">
            <div class="filter-bar">
                <select id="statusFilter">
                    <option value="active" selected>Needs attention</option>
                    <option value="all">All history</option>
                    <option value="new">New</option>
                    <option value="acknowledged">Acknowledged</option>
                    <option value="watching">Watching</option>
                    <option value="escalated">Escalated</option>
                    <option value="resolved">Resolved</option>
                    <option value="ignored">Ignored</option>
                </select>
                <select id="severityFilter">
                    <option value="">All severities</option>
                    <option value="critical">Critical</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                </select>
                <select id="categoryFilter">
                    <option value="">All categories</option>
                    <option value="guest_experience">Guest experience</option>
                    <option value="review_risk">Review risk</option>
                    <option value="operational_open_loop">Open loop</option>
                    <option value="checkin_checkout_risk">Check-in / checkout</option>
                    <option value="repeated_issue">Repeated issue</option>
                    <option value="revenue_booking_health">Revenue</option>
                    <option value="owner_decision">Decision / approval</option>
                </select>
                <input id="signalSearchInput" type="search" placeholder="Search signals">
            </div>
            <div id="signalsList" class="card-list">${signalList(data.items || [])}</div>
        </div>
    `;
    document.getElementById('statusFilter').addEventListener('change', reloadSignals);
    document.getElementById('severityFilter').addEventListener('change', reloadSignals);
    document.getElementById('categoryFilter').addEventListener('change', reloadSignals);
    document.getElementById('signalSearchInput').addEventListener('input', reloadSignals);
    bindStatusButtons();
}

async function reloadSignals() {
    const params = new URLSearchParams();
    const status = document.getElementById('statusFilter')?.value || 'active';
    const severity = document.getElementById('severityFilter')?.value || '';
    const category = document.getElementById('categoryFilter')?.value || '';
    const search = (document.getElementById('signalSearchInput')?.value || '').trim().toLowerCase();
    if (status) params.set('status', status);
    if (severity) params.set('severity', severity);
    if (category) params.set('category', category);
    if (selectedPortfolioId) params.set('portfolio_id', selectedPortfolioId);
    const data = await api(`/api/brain/signals?${params.toString()}`);
    const items = (data.items || []).filter(signal => {
        if (!search) return true;
        return [signal.title, signal.summary, signal.suggested_action, signal.listing_name, signal.portfolio_name]
            .filter(Boolean)
            .join(' ')
            .toLowerCase()
            .includes(search);
    });
    document.getElementById('signalsList').innerHTML = signalList(items);
    bindStatusButtons();
}

function signalList(items) {
    return cardList((items || []).map(signalCard), 'No signals match those filters.');
}

function renderBookingHealth(data) {
    const items = data.items || [];
    const topItems = items.filter(item => ['critical', 'high'].includes(item.severity)).slice(0, 6);
    content.innerHTML = `
        <div class="section-grid">
            ${section('Booking Signals To Act On', cardList((topItems.length ? topItems : items.slice(0, 6)).map(item => healthRow(item, { compact: true })), 'No booking-health warnings yet.'))}
            ${section('Property Booking Health', `
                <div class="filter-bar">
                    <select id="bookingSeverityFilter">
                        <option value="">All severities</option>
                        <option value="critical">Critical</option>
                        <option value="high">High</option>
                        <option value="watch">Watch</option>
                        <option value="healthy">Healthy</option>
                    </select>
                </div>
                <div id="bookingHealthGroups">${bookingHealthGroups(items)}</div>
            `)}
        </div>
    `;
    document.getElementById('bookingSeverityFilter')?.addEventListener('change', event => {
        document.getElementById('bookingHealthGroups').innerHTML = bookingHealthGroups(items, event.target.value);
    });
}

function renderOpenLoops(data) {
    const items = data.items || [];
    content.innerHTML = `
        <div class="section-grid">
            ${section('Unclosed Team Threads', cardList(items.map(loopRow), 'No open loops yet.'))}
        </div>
    `;
    bindStatusButtons();
}

function renderAsk() {
    content.innerHTML = `
        <div class="section">
            <form class="ask-form" id="askForm">
                <textarea id="questionInput" placeholder="What are the biggest risks today?"></textarea>
                <button class="primary-button" type="submit">Ask</button>
            </form>
            <div id="answerPanel" class="answer-panel"></div>
        </div>
    `;
    document.getElementById('askForm').addEventListener('submit', async event => {
        event.preventDefault();
        const panel = document.getElementById('answerPanel');
        panel.textContent = 'Thinking...';
        try {
            const result = await api('/api/brain/ask', {
                method: 'POST',
                body: JSON.stringify({
                    question: document.getElementById('questionInput').value,
                    portfolio_id: selectedPortfolioId || null
                })
            });
            panel.innerHTML = `
                <div class="signal-card">
                    <p>${escapeHtml(result.answer)}</p>
                    <div class="badge-row">
                        <span class="badge ${result.insufficient_evidence ? 'watch' : 'ok'}">${result.insufficient_evidence ? 'Insufficient evidence' : 'Evidence-backed'}</span>
                        <span class="badge">Confidence ${Math.round((result.confidence || 0) * 100)}%</span>
                        ${result.evidence_count !== undefined ? `<span class="badge">${Number(result.evidence_count || 0)} evidence records checked</span>` : ''}
                    </div>
                    ${result.missing_data ? `<p class="meta-line">${escapeHtml(result.missing_data)}</p>` : ''}
                    ${result.citations?.length ? `<ol class="evidence-list">${result.citations.map(citationItem).join('')}</ol>` : ''}
                </div>
            `;
        } catch (error) {
            panel.textContent = error.message || String(error);
        }
    });
}

function renderSettings() {
    content.innerHTML = `
        <div class="section-grid">
            ${section('Portfolio Setup', `
                <button class="primary-button" id="bootstrapButton" type="button">Bootstrap Portfolio Mapping</button>
                <div id="settingsResult" class="meta-line"></div>
            `, 'half')}
            ${section('Run Controls', `
                <div class="header-actions">
                    <button class="secondary-button" data-run="morning" type="button">Morning Read</button>
                    <button class="secondary-button" data-run="afternoon" type="button">Afternoon Read</button>
                </div>
            `, 'half')}
            ${section('Source Health', `
                <div id="sourceHealthPanel" class="source-health-panel"></div>
            `)}
            ${section('Create Portfolio', `
                <form class="ask-form" id="createPortfolioForm">
                    <input id="portfolioNameInput" placeholder="Portfolio name">
                    <textarea id="portfolioDescriptionInput" placeholder="Description"></textarea>
                    <button class="primary-button" type="submit">Create Portfolio</button>
                </form>
            `, 'half')}
            ${section('Map Listing', `
                <form class="ask-form" id="mapListingForm">
                    <select id="listingSelect"></select>
                    <select id="portfolioSelect"></select>
                    <button class="primary-button" type="submit">Map Listing</button>
                </form>
            `, 'half')}
            ${section('Brief Recipients', `
                <form class="ask-form" id="portfolioUserForm">
                    <select id="userSelect"></select>
                    <select id="userPortfolioSelect"></select>
                    <button class="primary-button" type="submit">Add Recipient</button>
                </form>
                <div id="portfolioUsersList" class="card-list" style="margin-top: 12px;"></div>
            `)}
        </div>
    `;
    loadSettingsData();
    document.getElementById('bootstrapButton').addEventListener('click', async () => {
        const result = await api('/api/brain/settings/bootstrap', { method: 'POST' });
        document.getElementById('settingsResult').textContent = result.status;
        await loadSettingsData();
    });
    document.querySelectorAll('[data-run]').forEach(button => {
        button.addEventListener('click', async () => {
            button.disabled = true;
            const original = button.textContent;
            button.textContent = 'Running...';
            try {
                await api(`/api/brain/runs/${button.dataset.run}`, { method: 'POST' });
                button.textContent = 'Done';
            } catch (error) {
                button.textContent = error.message || 'Failed';
            } finally {
                setTimeout(() => {
                    button.disabled = false;
                    button.textContent = original;
                }, 1400);
            }
        });
    });
    document.getElementById('createPortfolioForm').addEventListener('submit', async event => {
        event.preventDefault();
        await api('/api/brain/settings/portfolios', {
            method: 'POST',
            body: JSON.stringify({
                name: document.getElementById('portfolioNameInput').value,
                description: document.getElementById('portfolioDescriptionInput').value
            })
        });
        event.target.reset();
        await loadSettingsData();
    });
    document.getElementById('mapListingForm').addEventListener('submit', async event => {
        event.preventDefault();
        await api('/api/brain/settings/portfolio-listings', {
            method: 'POST',
            body: JSON.stringify({
                listing_id: document.getElementById('listingSelect').value,
                portfolio_id: document.getElementById('portfolioSelect').value
            })
        });
        await loadSettingsData();
    });
    document.getElementById('portfolioUserForm').addEventListener('submit', async event => {
        event.preventDefault();
        await api('/api/brain/settings/portfolio-users', {
            method: 'POST',
            body: JSON.stringify({
                user_id: document.getElementById('userSelect').value,
                portfolio_id: document.getElementById('userPortfolioSelect').value,
                role: 'operator'
            })
        });
        await loadSettingsData();
    });
}

async function loadSettingsData() {
    const data = await api('/api/brain/settings/data');
    const portfolioOptions = data.portfolios.map(item => `<option value="${item.portfolio_id}">${escapeHtml(item.name)}</option>`).join('');
    const listingOptions = data.listings.map(item => {
        const suffix = item.portfolio_id ? ` · mapped to ${item.portfolio_id}` : ' · unmapped';
        return `<option value="${item.listing_id}">${escapeHtml(item.name)}${escapeHtml(suffix)}</option>`;
    }).join('');
    const userOptions = data.users.map(item => `<option value="${item.user_id}">${escapeHtml(item.name)}${item.email ? ` · ${escapeHtml(item.email)}` : ''}</option>`).join('');
    document.getElementById('portfolioSelect').innerHTML = portfolioOptions || '<option value="">No portfolios</option>';
    document.getElementById('userPortfolioSelect').innerHTML = portfolioOptions || '<option value="">No portfolios</option>';
    document.getElementById('listingSelect').innerHTML = listingOptions || '<option value="">No listings</option>';
    document.getElementById('userSelect').innerHTML = userOptions || '<option value="">No users</option>';
    const sourceHealthPanel = document.getElementById('sourceHealthPanel');
    if (sourceHealthPanel) sourceHealthPanel.innerHTML = sourceHealth(data.source_health || {});
    const usersById = new Map(data.users.map(item => [item.user_id, item]));
    const portfoliosById = new Map(data.portfolios.map(item => [item.portfolio_id, item]));
    document.getElementById('portfolioUsersList').innerHTML = data.portfolio_users.length
        ? data.portfolio_users.map(row => {
            const user = usersById.get(row.user_id);
            const portfolio = portfoliosById.get(row.portfolio_id);
            return `
                <article class="loop-row">
                    <div class="row-topline">
                        <h3>${escapeHtml(user?.name || row.user_id)}</h3>
                        <span class="badge">Briefs</span>
                    </div>
                    <p class="meta-line">${escapeHtml(portfolio?.name || row.portfolio_id)}</p>
                    <button class="status-button" data-remove-user="${row.user_id}" data-remove-portfolio="${row.portfolio_id}" type="button">Remove</button>
                </article>
            `;
        }).join('')
        : '<div class="empty">No portfolio brief recipients yet.</div>';
    document.querySelectorAll('[data-remove-user]').forEach(button => {
        button.addEventListener('click', async () => {
            await api('/api/brain/settings/portfolio-users', {
                method: 'DELETE',
                body: JSON.stringify({
                    user_id: button.dataset.removeUser,
                    portfolio_id: button.dataset.removePortfolio
                })
            });
            await loadSettingsData();
        });
    });
}

function sourceHealth(data) {
    const latestRun = data.latest_run || {};
    const dataAggregator = data.data_aggregator || {};
    const scheduledReads = data.scheduled_reads || {};
    const briefDelivery = data.brief_delivery || {};
    const hostaway = data.hostaway || {};
    const pricelabs = data.pricelabs || {};
    const whatsapp = data.whatsapp || {};
    const whatsappIngestion = whatsapp.ongoing_ingestion || {};
    const groups = Array.isArray(whatsapp.groups) ? whatsapp.groups : [];
    const orderedGroups = [...groups].sort((a, b) => sourceStatusRank(a.status) - sourceStatusRank(b.status) || String(a.name || '').localeCompare(String(b.name || '')));
    const groupRows = orderedGroups.map(group => {
        const backfill = group.backfill || {};
        const details = [
            `${Number(group.message_count_24h || 0)} msgs / 24h`,
            `${Number(group.message_count_7d || 0)} msgs / 7d`,
            `${Number(group.message_count_60d || 0)} stored msgs / 60d`,
            `${Number(group.account_sent_count_24h || 0)} paired-account msgs / 24h`,
            group.verified_empty ? 'verified empty' : '',
            backfill.lookback_days ? `backfill lookback ${Number(backfill.lookback_days)}d` : '',
            backfill.fetch_rounds ? `${Number(backfill.fetch_rounds)} fetch round${Number(backfill.fetch_rounds) === 1 ? '' : 's'}` : '',
            backfill.sync_history_attempts ? `${Number(backfill.sync_history_attempts)} sync attempt${Number(backfill.sync_history_attempts) === 1 ? '' : 's'}` : '',
            group.last_received_at ? `last ${formatDateTime(group.last_received_at)}` : 'no stored messages',
            backfill.fetched !== undefined && backfill.fetched !== null ? `backfill fetched ${backfill.fetched}` : '',
            backfill.oldest_fetched_at ? `oldest fetched ${formatDateTime(backfill.oldest_fetched_at)}` : '',
            backfill.reached_cutoff === true ? 'older-history cutoff reached' : backfill.reached_cutoff === false ? 'older-history cutoff not reached' : ''
        ].filter(Boolean).join(' · ');
        return `
            <article class="loop-row source-group-row">
                <div class="row-topline">
                    <h3>${escapeHtml(group.name || 'Unnamed group')}</h3>
                    <span class="badge ${escapeHtml(group.status || 'missing')}">${escapeHtml(label(group.status || 'missing'))}</span>
                </div>
                <p class="meta-line">${escapeHtml([group.portfolio, group.kind, group.matched_thread && `matched ${group.matched_thread}`].filter(Boolean).join(' · '))}</p>
                <p>${escapeHtml(details)}</p>
                ${group.status_reason ? `<p class="meta-line">${escapeHtml(group.status_reason)}</p>` : ''}
                ${group.recommended_action ? `<p class="meta-line"><strong>Action:</strong> ${escapeHtml(group.recommended_action)}</p>` : ''}
            </article>
        `;
    }).join('');
    return `
        <div class="source-health-grid">
            ${sourceHealthSummaryCard('Latest Brain Run', latestRun.status || 'missing', [
                latestRun.signal_run_id ? `Run ${latestRun.signal_run_id}` : 'No run yet',
                latestRun.completed_at ? `Completed ${formatDateTime(latestRun.completed_at)}` : '',
                latestRun.source_counts ? `${Number(latestRun.source_counts.signals || 0)} signals · ${Number(latestRun.source_counts.booking_analyses || 0)} booking analyses` : ''
            ])}
            ${sourceHealthSummaryCard('Data Foundation', dataAggregator.status || 'missing', [
                `${Number(dataAggregator.fact_count || 0)} active facts`,
                `${Number(dataAggregator.ok_source_count || 0)} / ${Number(dataAggregator.source_count || 0)} sources current`,
                dataAggregator.latest_completed_at ? `Latest aggregation ${formatDateTime(dataAggregator.latest_completed_at)}` : 'No aggregation completed yet',
                dataAggregator.status_counts ? Object.entries(dataAggregator.status_counts).map(([key, value]) => `${value} ${label(key)}`).join(' · ') : '',
                dataAggregator.message || ''
            ])}
            ${sourceHealthSummaryCard('Scheduled Reads', scheduledReads.status || 'missing', [
                scheduledReads.latest_read_completed_at ? `Latest read ${formatDateTime(scheduledReads.latest_read_completed_at)}` : 'No morning/afternoon read yet',
                scheduledReads.message || '',
                Array.isArray(scheduledReads.reads) ? scheduledReads.reads.map(read => `${label(read.run_type)} ${label(read.status)}${read.signal_run_id ? ` #${read.signal_run_id}` : ''}`).join(' · ') : ''
            ])}
            ${sourceHealthSummaryCard('Brief Delivery', briefDelivery.status || 'missing', [
                briefDelivery.run_type ? `${label(briefDelivery.run_type)} run ${briefDelivery.signal_run_id || ''}` : 'No scheduled brief yet',
                briefDelivery.latest_generated_at ? `Generated ${formatDateTime(briefDelivery.latest_generated_at)}` : '',
                briefDelivery.channel_status_counts ? Object.entries(briefDelivery.channel_status_counts).map(([channel, statuses]) => `${label(channel)} ${Object.entries(statuses).map(([status, count]) => `${count} ${label(status)}`).join('/')}`).join(' · ') : '',
                briefDelivery.message || ''
            ])}
            ${sourceHealthSummaryCard('Hostaway', hostaway.status || 'missing', [
                `${Number(hostaway.mapped_listing_count || 0)} / ${Number(hostaway.active_listing_count || 0)} active listings mapped`,
                `${Number(hostaway.calendar_snapshot_count || 0)} calendar rows · ${Number(hostaway.booking_snapshot_count || 0)} booking snapshots`,
                `${Number(hostaway.booking_analysis_count || 0)} booking analyses · ${Number(hostaway.guest_stay_memory_count || 0)} guest memories`,
                hostaway.ignored_or_unmapped_listing_count ? `${Number(hostaway.ignored_or_unmapped_listing_count || 0)} intentionally ignored/unmapped active listings` : '',
                hostaway.completed_at ? `Latest Hostaway-backed run ${formatDateTime(hostaway.completed_at)}` : '',
                hostaway.message || ''
            ])}
            ${sourceHealthSummaryCard('PriceLabs', pricelabs.status || 'missing', [
                pricelabs.expected_snapshot_count ? `${Number(pricelabs.snapshot_count || 0)} / ${Number(pricelabs.expected_snapshot_count || 0)} mapped listings snapshotted` : `${Number(pricelabs.snapshot_count || 0)} snapshots`,
                pricelabs.latest_snapshot_at ? `Latest snapshot ${formatDateTime(pricelabs.latest_snapshot_at)}` : '',
                pricelabs.snapshot_age_hours !== undefined && pricelabs.snapshot_age_hours !== null ? `${Math.round(Number(pricelabs.snapshot_age_hours || 0) * 10) / 10} hours old` : '',
                pricelabs.status_counts ? Object.entries(pricelabs.status_counts).map(([key, value]) => `${value} ${label(key)}`).join(' · ') : '',
                pricelabs.configured === false ? 'API key not configured' : '',
                pricelabs.message || ''
            ])}
            ${sourceHealthSummaryCard('WhatsApp', whatsapp.status || 'missing', [
                `${Number(whatsapp.synced_group_count || 0)} / ${Number(whatsapp.configured_group_count || 0)} groups with stored messages`,
                `${Number(whatsappIngestion.active_group_count_24h || 0)} groups active in 24h · ${Number(whatsappIngestion.message_count_24h || 0)} messages`,
                `${Number(whatsappIngestion.account_sent_count_24h || 0)} paired-account messages captured in 24h`,
                whatsappIngestion.status === 'ok' ? 'Forward capture is current for ongoing twice-daily reads' : '',
                Number(whatsapp.history_limited_group_count || 0) || Number(whatsapp.incomplete_history_group_count || 0) ? `${Number(whatsapp.history_limited_group_count || 0) + Number(whatsapp.incomplete_history_group_count || 0)} groups have partial older history; not blocking daily operations` : '',
                `${Number(whatsapp.missing_group_count || 0)} missing · ${Number(whatsapp.not_found_group_count || 0)} not found · ${Number(whatsapp.stale_group_count || 0)} stale · ${Number(whatsapp.verified_empty_group_count || 0)} verified empty`,
                whatsappIngestion.latest_message_at ? `Latest message ${formatDateTime(whatsappIngestion.latest_message_at)}` : '',
                whatsappIngestion.bridge_ready_at ? `Bridge ready ${formatDateTime(whatsappIngestion.bridge_ready_at)}` : '',
                whatsappIngestion.latest_backfill_at ? `Latest batch read ${formatDateTime(whatsappIngestion.latest_backfill_at)}` : (whatsapp.last_backfill?.started_at ? `Last backfill ${formatDateTime(whatsapp.last_backfill.started_at)}` : 'No scheduled backfill recorded'),
                whatsappIngestion.message || ''
            ])}
        </div>
        <h3 class="subsection-title">Expected WhatsApp Groups</h3>
        <div class="card-list source-group-list">${groupRows || '<div class="empty">No WhatsApp groups configured.</div>'}</div>
    `;
}

function sourceHealthSummaryCard(title, status, lines) {
    return `
        <article class="loop-row source-summary-card">
            <div class="row-topline">
                <h3>${escapeHtml(title)}</h3>
                <span class="badge ${escapeHtml(status || 'missing')}">${escapeHtml(label(status || 'missing'))}</span>
            </div>
            ${(lines || []).filter(Boolean).map(line => `<p class="meta-line">${escapeHtml(line)}</p>`).join('')}
        </article>
    `;
}

function sourceStatusRank(status) {
    return { missing: 0, not_found: 1, incomplete_history: 2, stale: 3, unavailable: 4, degraded: 5, not_configured: 6, history_limited: 7, ok: 8, completed: 9 }.hasOwnProperty(status)
        ? { missing: 0, not_found: 1, incomplete_history: 2, stale: 3, unavailable: 4, degraded: 5, not_configured: 6, history_limited: 7, ok: 8, completed: 9 }[status]
        : 4;
}

function formatDateTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function section(title, body, size = '') {
    return `<section class="section ${size}"><h2>${escapeHtml(title)}</h2>${body}</section>`;
}

function detailsSection(title, count, body, size = '', open = false) {
    return `
        <details class="section details-section ${size}" ${open ? 'open' : ''}>
            <summary>
                <span>${escapeHtml(title)}</span>
                <span class="badge">${Number(count || 0)}</span>
            </summary>
            ${body}
        </details>
    `;
}

function cardList(cards, emptyText) {
    return cards.length ? `<div class="card-list">${cards.join('')}</div>` : `<div class="empty">${escapeHtml(emptyText)}</div>`;
}

function bookingHealthGroups(items, severity = '') {
    const filtered = severity ? (items || []).filter(item => item.severity === severity) : (items || []);
    if (!filtered.length) return '<div class="empty">No properties match that filter.</div>';
    const groups = groupBy(filtered, item => item.portfolio_name || 'Unassigned');
    return Object.entries(groups).map(([portfolioName, rows]) => `
        <section class="booking-group">
            <div class="row-topline">
                <h3>${escapeHtml(portfolioName)}</h3>
                <span class="badge">${rows.length} properties</span>
            </div>
            <div class="card-list">${rows.map(item => healthRow(item)).join('')}</div>
        </section>
    `).join('');
}

function groupBy(items, keyFn) {
    return (items || []).reduce((groups, item) => {
        const key = keyFn(item);
        groups[key] = groups[key] || [];
        groups[key].push(item);
        return groups;
    }, {});
}

function signalCard(signal) {
    const evidenceItems = signal.evidence || [];
    const evidence = evidenceItems.length ? `
        <details class="evidence-disclosure">
            <summary>Evidence (${evidenceItems.length})</summary>
            <ol class="evidence-list">${evidenceItems.slice(0, 4).map(item => `<li>${escapeHtml(truncateText(item.summary, 220))}</li>`).join('')}</ol>
        </details>
    ` : '';
    const context = [signal.portfolio_name, signal.listing_name, label(signal.category)].filter(Boolean).join(' · ');
    return `
        <article class="signal-card">
            <div class="card-topline">
                <div>
                    <h3>${escapeHtml(signal.title)}</h3>
                    ${context ? `<p class="meta-line">${escapeHtml(context)}</p>` : ''}
                </div>
                <span class="badge ${escapeHtml(signal.severity)}">${escapeHtml(signal.severity)}</span>
            </div>
            <div class="badge-row">
                <span class="badge ${escapeHtml(signal.status)}">${escapeHtml(label(signal.status))}</span>
                <span class="badge">Score ${Number(signal.rank_score || 0).toFixed(0)}</span>
                <span class="badge">Confidence ${Math.round((signal.confidence || 0) * 100)}%</span>
            </div>
            <p>${escapeHtml(truncateText(signal.summary || signal.why_it_matters || '', 260))}</p>
            ${signal.why_it_matters && signal.summary ? `<p class="meta-line">${escapeHtml(truncateText(signal.why_it_matters, 220))}</p>` : ''}
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(signal.suggested_action || '', 240))}</p>
            ${evidence}
            <details class="status-disclosure">
                <summary>Update status</summary>
                <div class="status-actions" data-signal-id="${signal.signal_id}">
                    ${['acknowledged', 'watching', 'resolved', 'ignored', 'escalated'].map(status => `<button class="status-button" data-status="${status}" type="button">${label(status)}</button>`).join('')}
                </div>
                <p class="status-feedback" hidden></p>
            </details>
        </article>
    `;
}

function citationItem(citation) {
    const meta = citation.metadata || {};
    const context = [citation.source_type, meta.thread_name, citation.occurred_at].filter(Boolean).join(' · ');
    const excerpt = citation.excerpt ? `<p class="citation-excerpt">${escapeHtml(citation.excerpt)}</p>` : '';
    return `
        <li>
            <strong>${escapeHtml(citation.summary || `Evidence ${citation.evidence_id}`)}</strong>
            ${context ? `<span class="meta-line">${escapeHtml(context)}</span>` : ''}
            <span>${escapeHtml(citation.reason || '')}</span>
            ${excerpt}
        </li>
    `;
}

function todoCard(item, index) {
    const context = [item.portfolio_name, item.listing_name, label(item.category)].filter(Boolean).join(' · ');
    const sourceLabel = item.source_type === 'booking_health' ? 'ROI' : item.source_type === 'open_loop' ? 'Team Thread' : 'Signal';
    return `
        <article class="todo-card">
            <div class="card-topline">
                <div class="todo-title">
                    <span class="todo-index">${index + 1}</span>
                    <div>
                        <h3>${escapeHtml(item.title)}</h3>
                        ${context ? `<p class="meta-line">${escapeHtml(context)}</p>` : ''}
                    </div>
                </div>
                <span class="badge ${escapeHtml(item.severity || 'medium')}">${escapeHtml(label(item.severity || 'medium'))}</span>
            </div>
            <div class="badge-row">
                <span class="badge">${escapeHtml(sourceLabel)}</span>
                <span class="badge ${escapeHtml(item.status || 'open')}">${escapeHtml(label(item.status || 'open'))}</span>
                <span class="badge">Priority ${Math.round(Number(item.priority_score || 0))}</span>
            </div>
            <p>${escapeHtml(truncateText(cleanOperationalText(item.summary || ''), 240))}</p>
            <p class="meta-line">${escapeHtml(item.reason || '')}</p>
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(cleanOperationalText(item.suggested_action || ''), 220))}</p>
            ${item.signal_id ? `
                <details class="status-disclosure">
                    <summary>Update status</summary>
                    <div class="status-actions" data-signal-id="${item.signal_id}">
                        ${['acknowledged', 'watching', 'resolved', 'ignored', 'escalated'].map(status => `<button class="status-button" data-status="${status}" type="button">${label(status)}</button>`).join('')}
                    </div>
                    <p class="status-feedback" hidden></p>
                </details>
            ` : ''}
        </article>
    `;
}

function portfolioCard(portfolio) {
    const signalSummary = [
        portfolio.active_signal_count ? `${Number(portfolio.active_signal_count)} active signals` : '',
        portfolio.critical_signal_count ? `${Number(portfolio.critical_signal_count)} critical` : '',
        portfolio.high_signal_count ? `${Number(portfolio.high_signal_count)} high` : '',
        portfolio.open_loop_count ? `${Number(portfolio.open_loop_count)} open loops` : ''
    ].filter(Boolean).join(' · ') || 'No active signals';
    return `
        <article class="portfolio-card">
            <div class="row-topline">
                <h3><a href="/portfolios/${portfolio.portfolio_id}">${escapeHtml(portfolio.name)}</a></h3>
                <span class="badge ${escapeHtml(portfolio.status)}">${escapeHtml(label(portfolio.status))}</span>
            </div>
            <p class="meta-line">${portfolio.property_count || 0} properties · ${escapeHtml(signalSummary)}</p>
            <p>${escapeHtml(truncateText(portfolio.top_operational_signal || portfolio.top_revenue_signal || 'No active portfolio signal', 180))}</p>
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(portfolio.suggested_action || 'No urgent action.', 180))}</p>
        </article>
    `;
}

function memoryCard(item) {
    const meta = [
        item.portfolio_name,
        item.group_kind ? label(item.group_kind) : '',
        `${Number(item.message_count || 0)} messages`,
        `${Number(item.participant_count || 0)} people`,
        item.last_message_at ? `last ${formatDateTime(item.last_message_at)}` : ''
    ].filter(Boolean).join(' · ');
    return `
        <article class="memory-card">
            <div class="row-topline">
                <h3>${escapeHtml(item.group_name || 'Team WhatsApp')}</h3>
                <span class="badge ${escapeHtml(item.status || 'ok')}">${escapeHtml(label(item.status || 'ok'))}</span>
            </div>
            ${meta ? `<p class="meta-line">${escapeHtml(meta)}</p>` : ''}
            <p>${escapeHtml(truncateText(cleanOperationalText(item.focus || item.summary || ''), 220))}</p>
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(cleanOperationalText(item.suggested_action || ''), 180))}</p>
        </article>
    `;
}

function healthRow(item, options = {}) {
    const horizons = Array.isArray(item.horizons) ? item.horizons : [];
    const meta = [
        item.portfolio_name,
        item.snapshot_date,
        item.priority_score !== undefined && item.priority_score !== null ? `priority ${Math.round(Number(item.priority_score || 0))}` : '',
        item.confidence !== undefined && item.confidence !== null ? `confidence ${Math.round(Number(item.confidence || 0) * 100)}%` : ''
    ].filter(Boolean).join(' · ');
    const horizonCells = [7, 30, 60].map(days => {
        const horizon = horizons.find(row => Number(row.horizon_days) === days) || {};
        const occupancy = Number(horizon.occupancy_rate || 0);
        const diagnosis = horizon.diagnosis || 'insufficient_data';
        return `
            <div class="horizon-cell">
                <strong>${days} days</strong>
                <span>${Math.round(occupancy * 100)}% booked</span>
                <small>${escapeHtml(horizon.booked_nights ?? 0)} booked · ${escapeHtml(horizon.available_nights ?? 0)} open</small>
                <small>${escapeHtml(label(diagnosis))}</small>
            </div>
        `;
    }).join('');
    const sourceStatuses = item.source_statuses || {};
    const sourceBadges = ['hostaway', 'pricelabs', 'airbnb', 'analysis']
        .filter(source => sourceStatuses[source])
        .map(source => `<span class="badge">${escapeHtml(label(source))}: ${escapeHtml(label(sourceStatuses[source]))}</span>`)
        .join('');
    const actionItems = item.action_items || [];
    const actions = actionItems.length
        ? `<ol class="action-list">${actionItems.map(action => `<li>${escapeHtml(action)}</li>`).join('')}</ol>`
        : '<p class="meta-line">No urgent action.</p>';
    const insight = item.opinion || item.booking_pattern || '';
    return `
        <article class="health-row health-card ${options.compact ? 'compact-health-card' : ''}">
            <div class="row-topline">
                <h3>${escapeHtml(displayListingName(item))}</h3>
                <span class="badge ${escapeHtml(item.severity || 'watch')}">${escapeHtml(label(item.severity || 'watch'))}</span>
            </div>
            ${meta ? `<p class="meta-line">${escapeHtml(meta)}</p>` : ''}
            <div class="horizon-grid">${horizonCells}</div>
            <p>${escapeHtml(truncateText(insight, options.compact ? 220 : 420))}</p>
            ${!options.compact && item.booking_pattern ? `<p><strong>Pattern:</strong> ${escapeHtml(item.booking_pattern)}</p>` : ''}
            ${!options.compact && item.pricelabs_opinion ? `<p><strong>PriceLabs:</strong> ${escapeHtml(item.pricelabs_opinion)}</p>` : ''}
            ${!options.compact && item.airbnb_page_opinion ? `<p><strong>Airbnb:</strong> ${escapeHtml(item.airbnb_page_opinion)}</p>` : ''}
            ${sourceBadges ? `<div class="badge-row">${sourceBadges}</div>` : ''}
            <p class="next-line"><strong>Action:</strong></p>
            ${actions}
        </article>
    `;
}

function loopRow(loop) {
    const context = [loop.portfolio_name, loop.listing_name, loop.category ? label(loop.category) : 'Open loop'].filter(Boolean).join(' · ');
    return `
        <article class="loop-row">
            <div class="row-topline">
                <h3>${escapeHtml(loop.title)}</h3>
                <span class="badge ${escapeHtml(loop.status || loop.severity || 'watch')}">${escapeHtml(label(loop.status || loop.severity || 'watch'))}</span>
            </div>
            ${context ? `<p class="meta-line">${escapeHtml(context)}</p>` : ''}
            <p>${escapeHtml(truncateText(cleanOperationalText(loop.summary || ''), 260))}</p>
            ${loop.last_known_update ? `<p class="meta-line">Last: ${escapeHtml(truncateText(cleanOperationalText(loop.last_known_update), 160))}</p>` : ''}
            <p class="next-line"><strong>Next:</strong> ${escapeHtml(truncateText(cleanOperationalText(loop.suggested_next_step || ''), 200))}</p>
            ${loop.signal_id ? `
                <details class="status-disclosure">
                    <summary>Update status</summary>
                    <div class="status-actions" data-signal-id="${loop.signal_id}">
                        ${['acknowledged', 'watching', 'resolved', 'ignored', 'escalated'].map(status => `<button class="status-button" data-status="${status}" type="button">${label(status)}</button>`).join('')}
                    </div>
                    <p class="status-feedback" hidden></p>
                </details>
            ` : ''}
        </article>
    `;
}

function listingRow(listing) {
    const meta = [listing.city, listing.status].filter(Boolean).join(' · ');
    return `
        <article class="portfolio-card">
            <h3>${escapeHtml(displayListingName(listing))}</h3>
            ${meta ? `<p class="meta-line">${escapeHtml(meta)}</p>` : ''}
        </article>
    `;
}

function briefCard(brief) {
    const payload = brief.payload || {};
    const counts = payload.counts || {};
    const sections = payload.sections || [];
    const operatingSnapshot = payload.operating_snapshot || [];
    const countBadges = Object.entries({
        Signals: counts.active_signals,
        'Guest risks': counts.guest_review_risks,
        'Open loops': counts.open_loops,
        'WhatsApp misses': counts.memory_highlights,
        'Booking ROI': counts.booking_roi
    }).filter(([, value]) => value !== undefined && value !== null).map(([labelText, value]) => (
        `<span class="badge">${escapeHtml(labelText)} ${escapeHtml(value)}</span>`
    )).join('');
    const snapshotPreview = operatingSnapshot.length ? `
        <div class="brief-snapshot">
            ${operatingSnapshot.slice(0, 6).map(briefSnapshotItem).join('')}
        </div>
    ` : '';
    const sectionPreview = sections.length ? `
        <div class="brief-sections">
            ${sections.map(briefSection).join('')}
        </div>
    ` : `<p>${escapeHtml((brief.body || '').split('\n').slice(0, 4).join(' '))}</p>`;
    return `
        <article class="brief-card">
            <h3>${escapeHtml(brief.subject)}</h3>
            <p class="meta-line">${escapeHtml(brief.generated_at || '')}</p>
            ${countBadges ? `<div class="badge-row">${countBadges}</div>` : ''}
            ${snapshotPreview}
            ${sectionPreview}
        </article>
    `;
}

function briefSnapshotItem(item) {
    const context = [item.portfolio_name, item.listing_name].filter(Boolean).join(' · ');
    const answer = item.answer || item.title || '';
    return `
        <article class="brief-snapshot-item">
            <h4>${escapeHtml(item.question || '')}</h4>
            <strong>${escapeHtml(answer)}</strong>
            ${context ? `<p class="meta-line">${escapeHtml(context)}</p>` : ''}
            ${item.suggested_action ? `<p>Next: ${escapeHtml(item.suggested_action)}</p>` : ''}
        </article>
    `;
}

function briefSection(section) {
    const items = section.items || [];
    const preview = items.slice(0, 2).map(item => `
        <li>
            <strong>${escapeHtml(item.title || 'Untitled')}</strong>
            ${item.action ? `<span>${escapeHtml(item.action)}</span>` : ''}
        </li>
    `).join('');
    return `
        <section class="brief-section">
            <h4>${escapeHtml(section.title || '')}</h4>
            ${items.length ? `<ol>${preview}</ol>` : '<p class="meta-line">None surfaced.</p>'}
        </section>
    `;
}

function bindStatusButtons() {
    document.querySelectorAll('.status-actions button').forEach(button => {
        button.addEventListener('click', async () => {
            const actions = button.closest('.status-actions');
            const signalId = actions.dataset.signalId;
            const feedback = actions.parentElement?.querySelector('.status-feedback');
            const status = button.dataset.status;
            const originalText = button.textContent;
            actions.querySelectorAll('button').forEach(item => { item.disabled = true; });
            button.textContent = 'Processing...';
            setStatusFeedback(feedback, 'Processing workflow update...');
            try {
                const result = await api(`/api/brain/signals/${signalId}/status`, {
                    method: 'PATCH',
                    body: JSON.stringify({ status })
                });
                const effects = result.processing_effects || {};
                const loopText = effects.open_loops_closed
                    ? ` Closed ${effects.open_loops_closed} open loop${effects.open_loops_closed === 1 ? '' : 's'}.`
                    : effects.open_loops_reopened
                        ? ` Reopened ${effects.open_loops_reopened} open loop${effects.open_loops_reopened === 1 ? '' : 's'}.`
                        : effects.open_loops_created
                            ? ` Created an open loop.`
                            : '';
                setStatusFeedback(feedback, `Processed as ${label(status)}.${loopText}`);
                setTimeout(() => loadPage(), 450);
            } catch (error) {
                button.textContent = originalText;
                actions.querySelectorAll('button').forEach(item => { item.disabled = false; });
                setStatusFeedback(feedback, error.message || String(error), true);
            }
        });
    });
}

function setStatusFeedback(element, message, isError = false) {
    if (!element) return;
    element.hidden = false;
    element.textContent = message;
    element.classList.toggle('error', Boolean(isError));
}

function label(value) {
    const labels = {
        owner_decision: 'Decision / Approval',
        revenue_booking_health: 'Revenue / Booking Health',
        operational_open_loop: 'Open Loop',
        checkin_checkout_risk: 'Check-In / Checkout',
        guest_experience: 'Guest Experience',
        review_risk: 'Review Risk',
        repeated_issue: 'Repeated Issue',
        operator: 'Team',
        active: 'Needs Attention',
        healthy: 'Healthy',
        watch: 'Watch'
    };
    if (labels[value]) return labels[value];
    return String(value || '').split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function displayListingName(item) {
    return item?.listing_name || item?.name || 'Unknown property';
}

function truncateText(value, maxLength = 220) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}...`;
}

function cleanOperationalText(value) {
    return String(value || '')
        .replace(/@\d{6,}/g, '')
        .replace(/@\+/g, '@')
        .replace(/\bGA Concierge\s*\d*\s*:/gi, '')
        .replace(/\._\./g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}
