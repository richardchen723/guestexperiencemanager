(function () {
    'use strict';

    const page = document.getElementById('bookingHealthPage');
    const dataElement = document.getElementById('bookingHealthData');
    if (!page || !dataElement) return;

    let data;
    try {
        data = JSON.parse(dataElement.textContent || '{}');
    } catch (error) {
        console.error('Unable to read booking-health data.', error);
        return;
    }

    const list = document.getElementById('bookingHealthList');
    const empty = document.getElementById('bookingHealthEmpty');
    const summaryCount = document.getElementById('bookingHealthSummaryCount');
    const summaryText = document.getElementById('bookingHealthSummaryText');
    const benchmarkNote = document.getElementById('bookingHealthBenchmarkNote');
    const portfolio = document.getElementById('bookingHealthPortfolio');
    let horizon = Number(data.default_horizon || 14);

    if (portfolio) {
        portfolio.addEventListener('change', function () {
            portfolio.form.submit();
        });
    }

    page.querySelectorAll('[data-booking-horizon]').forEach(function (button) {
        button.addEventListener('click', function () {
            horizon = Number(button.dataset.bookingHorizon || 14);
            page.querySelectorAll('[data-booking-horizon]').forEach(function (option) {
                option.setAttribute('aria-pressed', String(option === button));
            });
            render();
        });
    });

    function formatPercent(value) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
        return `${Math.round(Number(value))}%`;
    }

    function formatGap(value, suffix) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
        const rounded = Math.round(Number(value));
        return `${rounded > 0 ? '+' : ''}${rounded} ${suffix}`;
    }

    function dateRange(calendarDays) {
        if (!calendarDays.length) return 'No calendar dates';
        const first = new Date(`${calendarDays[0].date}T00:00:00`);
        const last = new Date(`${calendarDays[calendarDays.length - 1].date}T00:00:00`);
        const firstLabel = first.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        const lastLabel = last.toLocaleDateString(undefined, { month: first.getMonth() === last.getMonth() ? undefined : 'short', day: 'numeric' });
        return `${firstLabel}–${lastLabel}`;
    }

    function benchmark(label, value, className) {
        const row = document.createElement('div');
        row.className = 'booking-health-benchmark';

        const labelElement = document.createElement('span');
        labelElement.textContent = label;
        row.appendChild(labelElement);

        const track = document.createElement('div');
        track.className = 'booking-health-benchmark-track';
        track.setAttribute('role', 'progressbar');
        track.setAttribute('aria-label', `${label} occupancy`);
        track.setAttribute('aria-valuemin', '0');
        track.setAttribute('aria-valuemax', '100');
        if (value !== null && value !== undefined) track.setAttribute('aria-valuenow', String(value));
        const fill = document.createElement('div');
        fill.className = `booking-health-benchmark-fill ${className || ''}`.trim();
        fill.style.width = value === null || value === undefined ? '0' : `${Math.max(0, Math.min(100, Number(value)))}%`;
        track.appendChild(fill);
        row.appendChild(track);

        const valueElement = document.createElement('span');
        valueElement.className = 'booking-health-benchmark-value';
        valueElement.textContent = formatPercent(value);
        row.appendChild(valueElement);
        return row;
    }

    function listingRow(item, index) {
        const comparison = item.horizons[String(horizon)];
        const article = document.createElement('article');
        article.className = 'booking-health-row';

        const identity = document.createElement('div');
        identity.className = 'booking-health-identity';
        const title = document.createElement('h3');
        title.textContent = `${index + 1}. ${item.listing_name}`;
        identity.appendChild(title);
        const location = document.createElement('span');
        location.textContent = item.location;
        identity.appendChild(location);
        const gaps = document.createElement('div');
        gaps.className = 'booking-health-gaps';
        [
            { value: comparison.market_gap, suffix: 'vs market' },
            { value: comparison.last_year_gap, suffix: 'vs LY' }
        ].forEach(function (comparisonGap) {
            const label = formatGap(comparisonGap.value, comparisonGap.suffix);
            if (!label) return;
            const gap = document.createElement('span');
            const numericGap = Number(comparisonGap.value);
            const tone = numericGap < 0 ? 'is-behind' : numericGap > 0 ? 'is-ahead' : 'is-neutral';
            gap.className = `booking-health-gap ${tone}`;
            gap.textContent = label;
            gaps.appendChild(gap);
        });
        identity.appendChild(gaps);
        article.appendChild(identity);

        const calendarWrap = document.createElement('div');
        const calendarDays = item.calendar.slice(0, horizon);
        const calendarMeta = document.createElement('div');
        calendarMeta.className = 'booking-health-calendar-meta';
        const range = document.createElement('span');
        range.textContent = dateRange(calendarDays);
        const open = document.createElement('span');
        open.textContent = `${comparison.open_nights} open`;
        calendarMeta.append(range, open);
        calendarWrap.appendChild(calendarMeta);
        const calendar = document.createElement('div');
        calendar.className = 'booking-health-calendar';
        calendar.dataset.horizon = String(horizon);
        calendar.setAttribute('aria-label', `${item.listing_name}, next ${horizon} nights`);
        calendarDays.forEach(function (day) {
            const night = document.createElement('span');
            night.className = `booking-health-night is-${day.state}`;
            night.textContent = String(day.day);
            night.setAttribute('aria-label', `${day.weekday} ${day.date}: ${day.state}`);
            calendar.appendChild(night);
        });
        calendarWrap.appendChild(calendar);
        article.appendChild(calendarWrap);

        const benchmarks = document.createElement('div');
        benchmarks.className = 'booking-health-benchmarks';
        const marketLabel = comparison.market_horizon_days && comparison.market_horizon_days !== horizon
            ? `Market ${comparison.market_horizon_days}d`
            : 'Market';
        benchmarks.appendChild(benchmark('Unit', comparison.unit_occupancy, ''));
        benchmarks.appendChild(benchmark(marketLabel, comparison.market_occupancy, 'is-market'));
        benchmarks.appendChild(benchmark('Last year', comparison.last_year_occupancy, 'is-last-year'));
        article.appendChild(benchmarks);

        const action = document.createElement('div');
        action.className = 'booking-health-row-action';
        const link = document.createElement('a');
        link.className = 'booking-health-pricelabs';
        link.href = item.pricelabs_url || data.pricelabs_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Open in PriceLabs ↗';
        link.setAttribute('aria-label', 'Open PriceLabs in a new tab');
        action.appendChild(link);
        article.appendChild(action);
        return article;
    }

    function render() {
        if (!list) return;
        const visible = (data.items || [])
            .filter(function (item) {
                const comparison = item.horizons && item.horizons[String(horizon)];
                return comparison && comparison.underperforming;
            })
            .sort(function (left, right) {
                const leftGap = left.horizons[String(horizon)].worst_gap;
                const rightGap = right.horizons[String(horizon)].worst_gap;
                return Number(leftGap || 0) - Number(rightGap || 0) || left.listing_name.localeCompare(right.listing_name);
            });

        list.replaceChildren();
        visible.forEach(function (item, index) {
            list.appendChild(listingRow(item, index));
        });
        list.hidden = visible.length === 0;
        if (empty) empty.hidden = visible.length !== 0;

        const summary = (data.summary_by_horizon || {})[String(horizon)] || {};
        if (summaryCount) summaryCount.textContent = `${visible.length} of ${data.scope.property_count}`;
        if (summaryText) summaryText.textContent = visible.length === 1 ? 'listing is underperforming' : 'listings are underperforming';

        const usesFifteenDayMarket = visible.some(function (item) {
            return item.horizons[String(horizon)].market_horizon_days === 15;
        });
        if (benchmarkNote) {
            benchmarkNote.textContent = usesFifteenDayMarket
                ? 'PriceLabs provides a 15-day market benchmark, shown as the closest comparison to the 14-day calendar.'
                : `${Number(summary.comparable_count || 0)} listings have a current unit comparison and at least one benchmark.`;
        }
    }

    if (data.has_data) render();
})();
