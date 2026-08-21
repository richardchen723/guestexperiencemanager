(() => {
    'use strict';

    const initialQuery = new URLSearchParams(window.location.search);
    const todayIso = localIsoDate(new Date());
    const currentMonthStartIso = `${todayIso.slice(0, 7)}-01`;
    const state = {
        data: null,
        charts: {},
        selectedPortfolio: initialQuery.get('portfolio') || 'all',
        selectedPeriod: initialQuery.get('period') || 'current_month',
        customFrom: initialQuery.get('from') || currentMonthStartIso,
        customTo: initialQuery.get('to') || todayIso,
        refreshJobId: null,
        refreshPollTimer: null,
        isRefreshing: false
    };

    const colors = {
        blue: '#3158d8',
        blueFill: 'rgba(49, 88, 216, .12)',
        green: '#168466',
        greenFill: 'rgba(22, 132, 102, .12)',
        amber: '#c87521',
        red: '#d33b45',
        grid: 'rgba(111, 124, 148, .13)',
        muted: '#7d889b'
    };

    const elements = {};

    function cacheElements() {
        [
            'kpiLoading', 'kpiContent', 'kpiError', 'kpiErrorMessage', 'kpiErrorRetry',
            'portfolioFilter', 'kpiRefreshButton', 'kpiScopeName', 'kpiPropertyCount',
            'periodFilter', 'kpiCustomRange', 'kpiCustomFrom', 'kpiCustomTo',
            'kpiApplyRange', 'kpiRangeError', 'kpiDateRangeLabel', 'revenuePeriodUnit',
            'kpiRefreshLabel', 'kpiDialogRefreshButton', 'kpiRefreshStatusTitle',
            'kpiRefreshStatusText', 'kpiRefreshProgress', 'kpiRefreshProgressBar',
            'kpiRefreshProgressMeta',
            'kpiFreshnessButton', 'kpiFreshnessValue', 'kpiFreshnessDialog',
            'kpiFreshnessClose', 'kpiFreshnessList', 'kpiDefinitionDialog',
            'kpiDefinitionClose', 'kpiDefinitionTitle', 'kpiDefinitionFormula',
            'kpiDefinitionWhy', 'kpiDefinitionNumerator', 'kpiDefinitionDenominator',
            'kpiDefinitionWindow', 'kpiDefinitionSource', 'kpiDefinitionIncluded',
            'kpiDefinitionExcluded', 'kpiDefinitionFields'
        ].forEach(id => { elements[id] = document.getElementById(id); });
    }

    async function loadKPI() {
        setLoading(true);
        hideError();
        try {
            const query = new URLSearchParams({
                portfolio: state.selectedPortfolio,
                period: state.selectedPeriod
            });
            if (state.selectedPeriod === 'custom') {
                query.set('from', state.customFrom);
                query.set('to', state.customTo);
            }
            const response = await fetch(`/kpi/api/data?${query.toString()}`, {
                headers: { 'Accept': 'application/json' }
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || `Request failed with status ${response.status}`);
            state.data = payload;
            state.selectedPortfolio = payload.scope?.selected || 'all';
            state.selectedPeriod = payload.reporting_period?.key || 'current_month';
            if (state.selectedPeriod === 'custom') {
                state.customFrom = payload.reporting_period?.start_date || state.customFrom;
                state.customTo = payload.reporting_period?.end_date || state.customTo;
            }
            renderDashboard(payload);
            updateUrl();
            elements.kpiContent.hidden = false;
        } catch (error) {
            console.error('KPI dashboard load failed', error);
            showError(error.message || 'Please try refreshing the page.');
        } finally {
            setLoading(false);
        }
    }

    function renderDashboard(data) {
        renderScope(data.scope || {});
        renderReportingPeriod(data.reporting_period || {});
        renderDefinitions(data.definitions || {});
        renderFreshness(data.freshness || {});
        renderResponse(data.metrics?.response || {});
        renderAdvocacy(data.metrics?.advocacy || {});
        renderRevenue(data.metrics?.revenue || {});
        renderForward(data.metrics?.forward || {});
        renderOutcome(data.metrics?.outcome || {});
    }

    function renderReportingPeriod(period) {
        if (elements.periodFilter) elements.periodFilter.value = period.key || state.selectedPeriod;
        if (elements.kpiCustomFrom) {
            elements.kpiCustomFrom.value = state.customFrom;
            elements.kpiCustomFrom.max = todayIso;
        }
        if (elements.kpiCustomTo) {
            elements.kpiCustomTo.value = state.customTo;
            elements.kpiCustomTo.max = todayIso;
        }
        if (elements.kpiCustomRange) elements.kpiCustomRange.hidden = state.selectedPeriod !== 'custom';
        if (elements.kpiDateRangeLabel) elements.kpiDateRangeLabel.textContent = period.label || 'Current month';
        if (elements.revenuePeriodUnit) {
            elements.revenuePeriodUnit.textContent = period.day_count
                ? `${formatInteger(period.day_count)} selected stay dates`
                : 'selected stay dates';
        }
        clearRangeError();
    }

    function renderScope(scope) {
        const select = elements.portfolioFilter;
        const options = ['<option value="all">All properties</option>'];
        (scope.portfolios || []).forEach(portfolio => {
            const selected = scope.selected === portfolio.name ? ' selected' : '';
            options.push(`<option value="${escapeHtml(portfolio.name)}"${selected}>${escapeHtml(portfolio.name)} · ${formatInteger(portfolio.property_count)}</option>`);
        });
        select.innerHTML = options.join('');
        select.value = scope.selected || 'all';
        elements.kpiScopeName.textContent = scope.selected === 'all' ? 'All properties' : scope.selected;
        elements.kpiPropertyCount.textContent = `${formatInteger(scope.property_count || 0)} active ${scope.property_count === 1 ? 'property' : 'properties'}`;
    }

    function renderDefinitions(definitions) {
        document.querySelectorAll('.kpi-info-button[data-metric]').forEach(button => {
            const definition = definitions[button.dataset.metric];
            const preview = button.querySelector('.kpi-info-preview');
            if (preview) preview.textContent = definition?.short_formula || 'Metric definition is unavailable.';
        });
    }

    function renderFreshness(freshness) {
        elements.kpiFreshnessValue.textContent = freshness.latest_at ? formatRelativeTime(freshness.latest_at) : 'No timestamp';
        elements.kpiFreshnessList.innerHTML = (freshness.sources || []).map(source => `
            <div class="kpi-freshness-row">
                <strong>${escapeHtml(source.name)}</strong>
                <span>${source.timestamp ? escapeHtml(formatDateTime(source.timestamp)) : 'No successful snapshot'}</span>
            </div>
        `).join('') || '<p>No source timestamps are available.</p>';
    }

    function renderResponse(metric) {
        setCardStatus('response', metric);
        setText('responseValue', metric.median_minutes == null ? '—' : formatDuration(metric.median_minutes));
        setText('responseCaption', metric.reason || `${formatInteger(metric.answered_sequences)} answered guest message sequences in the selected period`);
        setText('responseP90', metric.p90_minutes == null ? '—' : formatDuration(metric.p90_minutes));
        setText('responseSla', formatPercent(metric.within_15_minutes_pct));
        setText('responseOpen', metric.open_over_4_hours == null ? '—' : formatInteger(metric.open_over_4_hours));
        renderLineChart('response', 'responseChart', metric.trend || [], {
            valueKey: 'median_minutes',
            label: 'Median reply time',
            color: colors.blue,
            fill: colors.blueFill,
            yFormat: value => `${value}m`,
            tooltip: item => `${formatDuration(item.raw)} median · ${formatInteger(metric.trend?.[item.dataIndex]?.sample_size || 0)} sequences`
        });
    }

    function renderAdvocacy(metric) {
        setCardStatus('advocacy', metric);
        setText('advocacyValue', formatPercent(metric.perfect_score_pct));
        setText('advocacyCaption', metric.reason || `${formatInteger(metric.review_count)} published guest reviews in the selected period`);
        setText('advocacyCoverage', formatPercent(metric.review_coverage_pct));
        setText('advocacyAverage', metric.average_rating == null ? '—' : `${Number(metric.average_rating).toFixed(2)} / 10`);
        setText('advocacyLow', formatPercent(metric.low_score_pct));
        renderLineChart('advocacy', 'advocacyChart', metric.trend || [], {
            valueKey: 'perfect_score_pct',
            label: 'Perfect-score rate',
            color: colors.green,
            fill: colors.greenFill,
            yFormat: value => `${value}%`,
            min: 0,
            max: 100,
            tooltip: item => `${formatPercent(item.raw)} · ${formatInteger(metric.trend?.[item.dataIndex]?.sample_size || 0)} reviews`
        });
    }

    function renderRevenue(metric) {
        setCardStatus('revenue', metric);
        setText('revenueValue', metric.gross_stay_value == null ? '—' : formatMoney(metric.gross_stay_value, metric.currency));
        setText('revenueCaption', metric.reason || `Allocated Hostaway total price across ${formatInteger(metric.reservation_nights)} occupied nights`);
        setText('revenueAdr', metric.adr == null ? '—' : formatMoney(metric.adr, metric.currency));
        setText('revenueNights', metric.reservation_nights == null ? '—' : formatInteger(metric.reservation_nights));
        setText('revenueCancellation', metric.cancellation_rate_pct == null ? '—' : `${formatPercent(metric.cancellation_rate_pct)} · ${formatInteger(metric.cancelled_bookings)}/${formatInteger(metric.bookings_created)}`);
        renderBarChart('revenue', 'revenueChart', metric.trend || [], metric.currency);
    }

    function renderForward(metric) {
        setCardStatus('forward', metric);
        setText('forwardSnapshot', metric.snapshot_date ? `Snapshot ${formatShortDate(metric.snapshot_date)}` : 'Snapshot unavailable');
        setText('forwardCaption', metric.reason || 'Reserved nights as a share of reserved plus available Hostaway calendar nights');
        setText('forwardCoverage', metric.property_coverage_pct == null
            ? 'Calendar coverage unavailable'
            : `Calendar coverage ${formatPercent(metric.property_coverage_pct)} · ${formatInteger(metric.covered_properties)}/${formatInteger(metric.expected_properties)} properties`);
        const container = document.getElementById('forwardHorizons');
        if (!metric.horizons?.length) {
            container.innerHTML = `<div class="kpi-card-caption">${escapeHtml(metric.reason || 'No forward calendar data is available.')}</div>`;
            return;
        }
        container.innerHTML = metric.horizons.map(horizon => {
            const rate = horizon.occupancy_pct == null ? 0 : horizon.occupancy_pct;
            const pickupClass = horizon.pickup_nights > 0 ? 'positive' : horizon.pickup_nights < 0 ? 'negative' : '';
            const pickup = horizon.pickup_nights == null
                ? 'No weekly comparison'
                : `${horizon.pickup_nights > 0 ? '+' : ''}${formatInteger(horizon.pickup_nights)} booked-night pickup`;
            return `
                <div class="forward-horizon">
                    <div class="forward-horizon-label"><strong>${escapeHtml(horizon.label)}</strong><span>${formatInteger(horizon.sellable_nights)} sellable nights</span></div>
                    <div class="forward-bar-track" title="${formatInteger(horizon.booked_nights)} booked · ${formatInteger(horizon.available_nights)} available · ${formatInteger(horizon.blocked_nights)} blocked">
                        <div class="forward-bar-fill" style="width:${Math.max(0, Math.min(100, rate))}%"></div>
                    </div>
                    <div class="forward-rate">${formatPercent(horizon.occupancy_pct)}</div>
                    <div class="forward-pickup ${pickupClass}">${escapeHtml(pickup)}</div>
                </div>
            `;
        }).join('');
    }

    function renderOutcome(metric) {
        setCardStatus('outcome', metric);
        setText('outcomeValue', formatPercent(metric.guest_outcome_pct));
        setText('outcomeCaption', metric.reason || 'Smooth or recovered stays among confidently classified completed stays');
        setText('outcomeCoverageBadge', `Coverage ${formatPercent(metric.classification_coverage_pct)}`);
        setText('outcomeProblems', formatPercent(metric.problem_incidence_pct));
        setText('outcomeRecovery', formatPercent(metric.recovery_rate_pct));
        setText('outcomeSample', metric.classified_stays == null ? '—' : `${formatInteger(metric.classified_stays)} / ${formatInteger(metric.eligible_stays)}`);
        setText('outcomeConfidence', formatPercent(metric.average_confidence_pct));

        const counts = metric.counts || { smooth: 0, recovered: 0, unresolved: 0, needs_review: 0 };
        const total = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
        const labels = {
            smooth: 'Smooth',
            recovered: 'Recovered',
            unresolved: 'Unresolved',
            needs_review: 'Needs review'
        };
        document.getElementById('outcomeStackedBar').innerHTML = Object.keys(labels).map(key => {
            const width = total ? (Number(counts[key] || 0) / total) * 100 : 0;
            return `<span class="outcome-segment ${key}" style="width:${width}%" title="${labels[key]}: ${formatInteger(counts[key] || 0)} stays"></span>`;
        }).join('');
        document.getElementById('outcomeLegend').innerHTML = Object.keys(labels).map(key => `
            <div class="outcome-legend-item ${key}">
                <span><i></i>${labels[key]}</span>
                <strong>${formatInteger(counts[key] || 0)}</strong>
            </div>
        `).join('');
        document.getElementById('outcomeStackedBar').setAttribute('aria-label', Object.keys(labels)
            .map(key => `${labels[key]} ${formatInteger(counts[key] || 0)}`)
            .join(', '));

        renderLineChart('outcome', 'outcomeChart', metric.trend || [], {
            valueKey: 'guest_outcome_pct',
            label: 'Guest outcome rate',
            color: colors.green,
            fill: colors.greenFill,
            yFormat: value => `${value}%`,
            min: 0,
            max: 100,
            tooltip: item => `${formatPercent(item.raw)} · ${formatInteger(metric.trend?.[item.dataIndex]?.classified_stays || 0)} classified stays`
        });
    }

    function renderLineChart(key, canvasId, rows, options) {
        destroyChart(key);
        const canvas = document.getElementById(canvasId);
        if (!window.Chart || !canvas) return;
        state.charts[key] = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: {
                labels: rows.map(row => formatWeek(row.week)),
                datasets: [{
                    label: options.label,
                    data: rows.map(row => row[options.valueKey]),
                    borderColor: options.color,
                    backgroundColor: options.fill,
                    borderWidth: 2,
                    pointRadius: rows.length <= 6 ? 2 : 0,
                    pointHoverRadius: 4,
                    pointBackgroundColor: options.color,
                    fill: true,
                    tension: .34,
                    spanGaps: false
                }]
            },
            options: chartOptions({
                min: options.min,
                max: options.max,
                yFormat: options.yFormat,
                tooltip: options.tooltip
            })
        });
    }

    function renderBarChart(key, canvasId, rows, currency) {
        destroyChart(key);
        const canvas = document.getElementById(canvasId);
        if (!window.Chart || !canvas) return;
        state.charts[key] = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: {
                labels: rows.map(row => formatWeek(row.week)),
                datasets: [{
                    label: 'Gross stay value',
                    data: rows.map(row => row.gross_stay_value),
                    backgroundColor: 'rgba(49, 88, 216, .68)',
                    borderColor: colors.blue,
                    borderWidth: 1,
                    borderRadius: 4,
                    maxBarThickness: 26
                }]
            },
            options: chartOptions({
                yFormat: value => compactMoney(value, currency),
                tooltip: item => `${formatMoney(item.raw, currency)} · ${formatInteger(rows[item.dataIndex]?.reservation_nights || 0)} nights`
            })
        });
    }

    function chartOptions(options = {}) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            animation: { duration: 280 },
            scales: {
                x: {
                    grid: { display: false },
                    border: { display: false },
                    ticks: { color: colors.muted, font: { size: 9 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 6 }
                },
                y: {
                    beginAtZero: options.min == null,
                    min: options.min,
                    max: options.max,
                    grid: { color: colors.grid, drawTicks: false },
                    border: { display: false },
                    ticks: { color: colors.muted, font: { size: 9 }, padding: 6, maxTicksLimit: 4, callback: options.yFormat }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    displayColors: false,
                    backgroundColor: '#17233e',
                    titleFont: { size: 11 },
                    bodyFont: { size: 11 },
                    padding: 9,
                    cornerRadius: 7,
                    callbacks: { label: options.tooltip }
                }
            }
        };
    }

    function openDefinition(metricKey) {
        const definition = state.data?.definitions?.[metricKey];
        if (!definition) return;
        elements.kpiDefinitionTitle.textContent = definition.title;
        elements.kpiDefinitionFormula.textContent = definition.short_formula;
        elements.kpiDefinitionWhy.textContent = definition.why;
        elements.kpiDefinitionNumerator.textContent = definition.numerator;
        elements.kpiDefinitionDenominator.textContent = definition.denominator;
        elements.kpiDefinitionWindow.textContent = definition.window;
        elements.kpiDefinitionSource.textContent = definition.source;
        elements.kpiDefinitionIncluded.innerHTML = (definition.inclusions || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
        elements.kpiDefinitionExcluded.innerHTML = (definition.exclusions || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
        elements.kpiDefinitionFields.innerHTML = (definition.raw_fields || []).map(field => `<code>${escapeHtml(field)}</code>`).join('');
        elements.kpiDefinitionDialog.showModal();
    }

    function setCardStatus(key, metric) {
        const card = document.querySelector(`[data-kpi-card="${key}"]`);
        if (card) card.dataset.status = metric.status || 'unavailable';
    }

    function destroyChart(key) {
        if (state.charts[key]) {
            state.charts[key].destroy();
            delete state.charts[key];
        }
    }

    function setLoading(loading) {
        elements.kpiLoading.hidden = !loading;
        if (elements.kpiRefreshButton && !state.isRefreshing) {
            elements.kpiRefreshButton.disabled = loading;
        }
        if (loading) elements.kpiContent.hidden = true;
    }

    async function startSourceRefresh() {
        if (state.isRefreshing || !elements.kpiRefreshButton) return;
        setRefreshUi({ status: 'pending', progress: { phase: 'Starting Hostaway refresh' } });
        try {
            const response = await fetch('/kpi/api/refresh', {
                method: 'POST',
                headers: { 'Accept': 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ portfolio: state.selectedPortfolio })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || `Refresh could not start (${response.status})`);
            state.refreshJobId = payload.job_id;
            pollSourceRefresh(true);
        } catch (error) {
            console.error('KPI source refresh failed to start', error);
            finishRefreshUi('error', error.message || 'The source refresh could not start.');
        }
    }

    async function restoreActiveRefresh() {
        if (!elements.kpiRefreshButton) return;
        try {
            const response = await fetch('/kpi/api/refresh/active', { headers: { 'Accept': 'application/json' } });
            if (!response.ok) return;
            const payload = await response.json();
            if (payload.status === 'pending' || payload.status === 'running') {
                state.refreshJobId = payload.job_id;
                setRefreshUi(payload);
                scheduleRefreshPoll();
            }
        } catch (error) {
            console.warn('Unable to restore KPI refresh status', error);
        }
    }

    async function pollSourceRefresh(immediate = false) {
        clearRefreshPoll();
        if (!state.refreshJobId) return;
        if (!immediate) {
            state.refreshPollTimer = window.setTimeout(() => pollSourceRefresh(true), 1800);
            return;
        }
        try {
            const response = await fetch(`/kpi/api/refresh/${encodeURIComponent(state.refreshJobId)}`, {
                headers: { 'Accept': 'application/json' }
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || 'Refresh status is unavailable.');
            setRefreshUi(payload);
            if (payload.status === 'pending' || payload.status === 'running') {
                scheduleRefreshPoll();
                return;
            }
            if (payload.status === 'completed') {
                finishRefreshUi('completed', payload.warning || 'The selected portfolio’s Hostaway sources are up to date.');
                await loadKPI();
                return;
            }
            finishRefreshUi('error', payload.error || 'The source refresh did not complete.');
        } catch (error) {
            console.error('KPI source refresh polling failed', error);
            finishRefreshUi('error', error.message || 'Refresh status could not be checked.');
        }
    }

    function scheduleRefreshPoll() {
        clearRefreshPoll();
        state.refreshPollTimer = window.setTimeout(() => pollSourceRefresh(true), 1800);
    }

    function clearRefreshPoll() {
        if (state.refreshPollTimer) {
            window.clearTimeout(state.refreshPollTimer);
            state.refreshPollTimer = null;
        }
    }

    function setRefreshUi(payload) {
        state.isRefreshing = payload.status === 'pending' || payload.status === 'running';
        const progress = payload.progress || {};
        const phase = progress.phase || 'Preparing source refresh';
        const processed = Number(progress.processed || 0);
        const total = Number(progress.total || 0);
        const percentage = total > 0 ? Math.max(0, Math.min(100, Number(progress.percentage || 0))) : 0;

        if (elements.kpiRefreshButton) {
            elements.kpiRefreshButton.disabled = state.isRefreshing;
            elements.kpiRefreshButton.classList.toggle('is-loading', state.isRefreshing);
            elements.kpiRefreshButton.setAttribute('aria-busy', state.isRefreshing ? 'true' : 'false');
        }
        if (elements.kpiRefreshLabel) elements.kpiRefreshLabel.textContent = state.isRefreshing ? 'Refreshing…' : 'Refresh data';
        if (elements.kpiDialogRefreshButton) {
            elements.kpiDialogRefreshButton.disabled = state.isRefreshing;
            elements.kpiDialogRefreshButton.classList.toggle('is-loading', state.isRefreshing);
        }
        if (elements.kpiRefreshStatusTitle) elements.kpiRefreshStatusTitle.textContent = state.isRefreshing ? 'Refreshing source data' : 'Refresh source data';
        if (elements.kpiRefreshStatusText) elements.kpiRefreshStatusText.textContent = state.isRefreshing
            ? `${phase}${progress.current_item ? ` · ${progress.current_item}` : ''}`
            : 'Pull the latest listings, reservations, guest messages, reviews and forward calendar from Hostaway. Stay-outcome classification remains separately controlled.';
        if (elements.kpiRefreshProgress) {
            elements.kpiRefreshProgress.hidden = !state.isRefreshing;
            elements.kpiRefreshProgress.classList.toggle('is-indeterminate', state.isRefreshing && total === 0);
        }
        if (elements.kpiRefreshProgressBar) elements.kpiRefreshProgressBar.style.width = total > 0 ? `${percentage}%` : '';
        if (elements.kpiRefreshProgressMeta) elements.kpiRefreshProgressMeta.textContent = total > 0
            ? `${formatInteger(processed)} / ${formatInteger(total)}`
            : 'Working…';
    }

    function finishRefreshUi(status, message) {
        clearRefreshPoll();
        state.isRefreshing = false;
        state.refreshJobId = null;
        if (elements.kpiRefreshButton) {
            elements.kpiRefreshButton.disabled = false;
            elements.kpiRefreshButton.classList.remove('is-loading');
            elements.kpiRefreshButton.setAttribute('aria-busy', 'false');
        }
        if (elements.kpiRefreshLabel) elements.kpiRefreshLabel.textContent = status === 'completed' ? 'Up to date' : 'Retry refresh';
        if (elements.kpiDialogRefreshButton) {
            elements.kpiDialogRefreshButton.disabled = false;
            elements.kpiDialogRefreshButton.classList.remove('is-loading');
        }
        if (elements.kpiRefreshStatusTitle) elements.kpiRefreshStatusTitle.textContent = status === 'completed' ? 'Refresh complete' : 'Refresh needs attention';
        if (elements.kpiRefreshStatusText) elements.kpiRefreshStatusText.textContent = message;
        if (elements.kpiRefreshProgress) elements.kpiRefreshProgress.hidden = true;
        window.setTimeout(() => {
            if (!state.isRefreshing && elements.kpiRefreshLabel) elements.kpiRefreshLabel.textContent = 'Refresh data';
        }, 3500);
    }

    function showError(message) {
        elements.kpiError.hidden = false;
        elements.kpiErrorMessage.textContent = message;
        elements.kpiContent.hidden = true;
    }

    function hideError() {
        elements.kpiError.hidden = true;
    }

    function updateUrl() {
        const url = new URL(window.location.href);
        if (state.selectedPortfolio === 'all') url.searchParams.delete('portfolio');
        else url.searchParams.set('portfolio', state.selectedPortfolio);
        if (state.selectedPeriod === 'current_month') url.searchParams.delete('period');
        else url.searchParams.set('period', state.selectedPeriod);
        if (state.selectedPeriod === 'custom') {
            url.searchParams.set('from', state.customFrom);
            url.searchParams.set('to', state.customTo);
        } else {
            url.searchParams.delete('from');
            url.searchParams.delete('to');
        }
        window.history.replaceState({}, '', url);
    }

    function selectReportingPeriod(event) {
        state.selectedPeriod = event.target.value || 'current_month';
        clearRangeError();
        if (elements.kpiCustomRange) elements.kpiCustomRange.hidden = state.selectedPeriod !== 'custom';
        if (state.selectedPeriod === 'custom') {
            if (elements.kpiCustomFrom) elements.kpiCustomFrom.value = state.customFrom;
            if (elements.kpiCustomTo) elements.kpiCustomTo.value = state.customTo;
            return;
        }
        loadKPI();
    }

    function applyCustomRange() {
        const from = elements.kpiCustomFrom?.value || '';
        const to = elements.kpiCustomTo?.value || '';
        if (!from || !to) {
            showRangeError('Select both From and To dates.');
            return;
        }
        if (from > to) {
            showRangeError('The From date must be on or before the To date.');
            return;
        }
        if (to > todayIso) {
            showRangeError('The reporting period cannot end in the future.');
            return;
        }
        const fromDate = parseIsoDate(from);
        const toDate = parseIsoDate(to);
        const dayCount = fromDate && toDate ? Math.round((toDate - fromDate) / 86400000) + 1 : 0;
        if (!dayCount || dayCount > 366) {
            showRangeError('The reporting period cannot exceed 366 days.');
            return;
        }
        state.customFrom = from;
        state.customTo = to;
        state.selectedPeriod = 'custom';
        clearRangeError();
        loadKPI();
    }

    function showRangeError(message) {
        if (!elements.kpiRangeError) return;
        elements.kpiRangeError.textContent = message;
        elements.kpiRangeError.hidden = false;
    }

    function clearRangeError() {
        if (!elements.kpiRangeError) return;
        elements.kpiRangeError.textContent = '';
        elements.kpiRangeError.hidden = true;
    }

    function bindEvents() {
        elements.portfolioFilter.addEventListener('change', event => {
            state.selectedPortfolio = event.target.value || 'all';
            loadKPI();
        });
        if (elements.periodFilter) elements.periodFilter.addEventListener('change', selectReportingPeriod);
        if (elements.kpiApplyRange) elements.kpiApplyRange.addEventListener('click', applyCustomRange);
        [elements.kpiCustomFrom, elements.kpiCustomTo].filter(Boolean).forEach(input => {
            input.addEventListener('keydown', event => {
                if (event.key === 'Enter') applyCustomRange();
            });
        });
        if (elements.kpiRefreshButton) elements.kpiRefreshButton.addEventListener('click', startSourceRefresh);
        if (elements.kpiDialogRefreshButton) elements.kpiDialogRefreshButton.addEventListener('click', startSourceRefresh);
        elements.kpiErrorRetry.addEventListener('click', loadKPI);
        elements.kpiFreshnessButton.addEventListener('click', () => elements.kpiFreshnessDialog.showModal());
        elements.kpiFreshnessClose.addEventListener('click', () => elements.kpiFreshnessDialog.close());
        elements.kpiDefinitionClose.addEventListener('click', () => elements.kpiDefinitionDialog.close());
        document.querySelectorAll('.kpi-info-button[data-metric]').forEach(button => {
            button.addEventListener('click', () => openDefinition(button.dataset.metric));
        });
        [elements.kpiDefinitionDialog, elements.kpiFreshnessDialog].forEach(dialog => {
            dialog.addEventListener('click', event => {
                if (event.target === dialog) dialog.close();
            });
        });
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function formatDuration(minutes) {
        const value = Number(minutes);
        if (!Number.isFinite(value)) return '—';
        if (value < 1) return `${Math.max(1, Math.round(value * 60))} sec`;
        if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} min`;
        const hours = Math.floor(value / 60);
        const remainder = Math.round(value % 60);
        return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
    }

    function formatPercent(value) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        return Number.isFinite(number) ? `${number.toFixed(number % 1 === 0 ? 0 : 1)}%` : '—';
    }

    function formatMoney(value, currency) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        if (!Number.isFinite(number) || !currency) return '—';
        try {
            return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 0 }).format(number);
        } catch (_error) {
            return `${currency} ${Math.round(number).toLocaleString()}`;
        }
    }

    function compactMoney(value, currency) {
        if (value === null || value === undefined || value === '') return '—';
        const number = Number(value);
        if (!Number.isFinite(number)) return '—';
        try {
            return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD', notation: 'compact', maximumFractionDigits: 1 }).format(number);
        } catch (_error) {
            return Math.round(number).toLocaleString();
        }
    }

    function formatInteger(value) {
        const number = Number(value);
        return Number.isFinite(number) ? Math.round(number).toLocaleString() : '0';
    }

    function formatWeek(value) {
        const parsed = parseIsoDate(value);
        return parsed ? parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
    }

    function formatShortDate(value) {
        const parsed = parseIsoDate(value);
        return parsed ? parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—';
    }

    function formatDateTime(value) {
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return 'Unknown';
        return parsed.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
    }

    function formatRelativeTime(value) {
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return 'Unknown';
        const seconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
        if (seconds < 90) return 'Just now';
        if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
        if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
        return `${Math.round(seconds / 86400)}d ago`;
    }

    function parseIsoDate(value) {
        if (!value) return null;
        const parts = String(value).split('-').map(Number);
        if (parts.length !== 3 || parts.some(Number.isNaN)) return null;
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function localIsoDate(value) {
        const year = value.getFullYear();
        const month = String(value.getMonth() + 1).padStart(2, '0');
        const day = String(value.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    document.addEventListener('DOMContentLoaded', () => {
        cacheElements();
        bindEvents();
        loadKPI();
        restoreActiveRefresh();
    });
})();
