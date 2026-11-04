/* Read-only operational scorecard. Real links own routing and browser history. */
(() => {
  'use strict';
  const root = document.getElementById('opsPage');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const shown = value => value === null || value === undefined ? '— · unknown' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
  const label = key => key.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
  const stamp = value => value ? new Date(value).toLocaleString(undefined, {timeZoneName: 'short'}) : 'Unknown';
  const usd = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'});
  const signedPercent = new Intl.NumberFormat('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero'});
  const axisUsd = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 2});
  const shortDate = value => new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {month: 'short', day: 'numeric', timeZone: 'UTC'});
  let inFlight = false, data = null, metric = null;
  const filterStorageKey = `operational-kpi-trend:${root.dataset.metric || 'overview'}`;
  let trendSelection = null;
  try { trendSelection = sessionStorage.getItem(filterStorageKey); } catch (_) { /* A blocked preference store must not block the dashboard. */ }
  const reviewChannel = value => ({airbnb: 'Airbnb', vrbo: 'Vrbo', homeaway: 'Vrbo', booking: 'Booking.com', bookingcom: 'Booking.com'})[String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '')] || value || 'Unknown';
  const reviewChannels = ['Airbnb', 'Vrbo', 'Booking.com'];
  const reviewNumber = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', {maximumFractionDigits: 2}).format(value) : 'N/A';

  function badge(node, m) {
    node.textContent = m.data_status === 'verified' ? (m.goal_status === 'met' ? 'Verified · on target' : 'Verified · off target') : label(m.data_status);
    node.dataset.state = m.data_status === 'verified' && m.goal_status === 'off_target' ? 'off_target' : m.data_status;
  }
  function table(container, rows, empty = 'No verified observations are available for this breakdown.') {
    container.replaceChildren();
    if (!rows.length) { container.append(el('div', empty, 'ops-empty')); return; }
    const columns = [...new Set(rows.flatMap(row => Object.keys(row)))];
    const t = el('table'), head = el('thead'), hr = el('tr'), body = el('tbody');
    columns.forEach(c => { const th = el('th', label(c)); th.scope = 'col'; hr.append(th); });
    head.append(hr);
    rows.forEach(row => { const tr = el('tr'); columns.forEach(c => tr.append(el('td', shown(row[c])))); body.append(tr); });
    t.append(head, body); container.append(t);
  }
  function definitions(container, items) {
    container.replaceChildren();
    if (!items.length) { container.append(el('div', 'No verified supporting observations.', 'ops-empty')); return; }
    items.forEach(x => { const row = el('div'); row.append(el('dt', x.label), el('dd', shown(x.value))); container.append(row); });
  }
  function revenueTable(container, rows, compatible) {
    container.replaceChildren();
    const t = el('table', undefined, 'ops-revenue-trend'), head = el('thead'), hr = el('tr'), body = el('tbody');
    const caption = el('caption', compatible ? metric.trend_scope : 'Weekly YoY comparison unavailable in this saved snapshot. Reloading only reads saved results.', 'ops-sr-only');
    container.tabIndex = 0; container.setAttribute('role', 'region'); container.setAttribute('aria-label', 'Weekly booking-value comparison. Scroll horizontally if needed.');
    ['Date', 'Booking value in USD', 'Booking value same week last year in USD', 'YoY change'].forEach(text => { const th = el('th', text); th.scope = 'col'; hr.append(th); });
    head.append(hr);
    if (!compatible || !rows.length) {
      const cell = el('td', compatible ? 'No usable common-listing comparison is available.' : 'Unavailable until a saved calculation contains both comparison periods.');
      cell.colSpan = 4; const row = el('tr'); row.append(cell); body.append(row);
    } else rows.forEach(row => {
      const tr = el('tr'), date = el('td', row.date);
      const days = row.current_days_per_listing || [];
      if (row.partial_week) date.append(el('small', `Partial week · ${days.join('–')} days/listing`, 'ops-date-note'));
      if (row.yoy_unavailable_reason === 'Different calendar day counts') date.append(el('small', 'Unequal leap-year spans', 'ops-date-note'));
      if (row.coverage && ['current', 'prior'].some(period => row.coverage[period].observed_listings < row.coverage[period].expected_listings)) date.append(el('small', `Observed listings: ${row.coverage.current.observed_listings}/${row.coverage.current.expected_listings} current · ${row.coverage.prior.observed_listings}/${row.coverage.prior.expected_listings} prior`, 'ops-date-note'));
      const span = `Current: ${(row.current_spans || []).join('; ')}. Prior: ${(row.prior_spans || []).join('; ')}.`;
      date.title = span; date.setAttribute('aria-label', `${date.textContent}. ${span}`);
      tr.append(date);
      [row.booking_value_usd, row.prior_booking_value_usd].forEach(value => tr.append(el('td', Number.isFinite(value) ? usd.format(value) : 'N/A', 'ops-revenue-value')));
      const change = el('td', Number.isFinite(row.yoy_change_pct) ? `${signedPercent.format(row.yoy_change_pct)}%` : 'N/A', 'ops-revenue-value');
      if (row.yoy_unavailable_reason) { change.title = row.yoy_unavailable_reason; change.setAttribute('aria-label', `N/A. ${row.yoy_unavailable_reason}`); }
      tr.append(change); body.append(tr);
    });
    t.append(caption, head, body); container.append(el('p', 'Scroll sideways to compare all four columns.', 'ops-table-scroll-hint'), t);
  }
  function reviewTable(container, rows) {
    container.replaceChildren();
    container.tabIndex = 0; container.setAttribute('role', 'region'); container.setAttribute('aria-label', 'Weekly checkout review rates by channel. Scroll horizontally if needed.');
    const t = el('table'), head = el('thead'), hr = el('tr'), body = el('tbody');
    t.append(el('caption', 'Week starting: Monday checkout buckets, not individual departure dates. Provisional review rates include still-open opportunities.', 'ops-sr-only'));
    ['Stays', 'Checkout date', 'Channel', 'Reviews', 'Review rate (%)', 'Target (%)'].forEach(text => { const th = el('th', text); th.scope = 'col'; hr.append(th); });
    head.append(hr);
    rows.forEach(row => {
      const tr = el('tr');
      [reviewNumber(row.checkout_opportunities ?? row.eligible), row.date, reviewChannel(row.group), reviewNumber(row.reviewed), reviewNumber(row.value), reviewNumber(row.target)].forEach(value => tr.append(el('td', value)));
      body.append(tr);
    });
    if (!rows.length) { const tr = el('tr'), cell = el('td', 'No weekly data for the selected channel in this saved snapshot.'); cell.colSpan = 6; tr.append(cell); body.append(tr); }
    t.append(head, body); container.append(el('p', 'Scroll sideways to compare all six columns.', 'ops-table-scroll-hint'), t);
  }
  const cleaningEvidence = () => metric.cleaning_weekly?.schema === 'affected-stay-share.v3' ? metric.cleaning_weekly : null;
  function cleaningTable(container, rows, property = false) {
    container.replaceChildren();
    const t = el('table'), head = el('thead'), hr = el('tr'), body = el('tbody');
    [property ? 'Property' : 'Week starting', 'Total stays', 'Stays with cleaning issues', 'Affected stays (%)'].forEach(text => { const th = el('th', text); th.scope = 'col'; hr.append(th); });
    head.append(hr);
    rows.forEach(row => {
      const tr = el('tr'), first = el('td', property ? row.name : row.date);
      if (!property && row.partial_week) first.append(el('small', 'Partial week', 'ops-date-note'));
      tr.append(first, el('td', reviewNumber(row.total_stays)), el('td', reviewNumber(row.affected_stays)), el('td', reviewNumber(property ? row.percentage : row.value)));
      body.append(tr);
    });
    if (!rows.length) { const tr = el('tr'), td = el('td', cleaningEvidence() ? 'No matching stays in this saved window.' : 'Unavailable in this saved snapshot. A background calculation is required.'); td.colSpan = 4; tr.append(td); body.append(tr); }
    t.append(head, body); container.tabIndex = 0; container.setAttribute('role', 'region'); container.setAttribute('aria-label', property ? 'Cleaning issues by property' : 'Weekly affected-stay percentages'); container.append(el('p', 'Scroll sideways to see all four columns.', 'ops-table-scroll-hint'), t);
  }
  function cleaningBreakdown() {
    const query = $('breakdownSearch').value.toLowerCase();
    cleaningTable($('opsBreakdown'), (cleaningEvidence()?.properties || []).filter(row => row.name.toLowerCase().includes(query)), true);
  }
  function drawCleaningTrend(rows) {
    const container = $('opsTrendChart');
    if (!rows.some(row => Number.isFinite(row.value))) { container.append(el('div', 'No affected-stay percentage is available for these weeks.', 'ops-empty')); return; }
    const ns = 'http://www.w3.org/2000/svg';
    const shape = (tag, attrs = {}, text) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (text !== undefined) node.textContent = text; return node; };
    const dates = [], start = new Date(`${rows[0].date}T00:00:00Z`), end = new Date(`${rows[rows.length - 1].date}T00:00:00Z`);
    for (let d = start; d <= end; d = new Date(d.getTime() + 7 * 86400000)) dates.push(d.toISOString().slice(0, 10));
    const width = Math.max(280, container.clientWidth || root.clientWidth - 70), height = 220, left = 52, right = width - 16, top = 24, bottom = height - 42;
    const step = Math.max(1, Math.ceil(Math.max(...rows.filter(row => Number.isFinite(row.value)).map(row => row.value)) / 4 / 5) * 5), maximum = step * 4;
    const x = index => left + index / Math.max(1, dates.length - 1) * (right - left), y = value => bottom - value / maximum * (bottom - top);
    const svg = shape('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'ops-chart ops-cleaning-chart', 'data-y-max': maximum});
    svg.append(shape('title', {}, 'Stays with recorded cleaning issues as a percentage of total stays. Weeks without a denominator remain gaps.'), shape('text', {x: left, y: 14}, 'Affected stays (%)'));
    for (let value = 0; value <= maximum; value += step) svg.append(shape('line', {x1: left, x2: right, y1: y(value), y2: y(value), class: 'ops-gridline'}), shape('text', {x: left - 7, y: y(value) + 4, 'text-anchor': 'end', class: 'ops-y-tick'}, `${reviewNumber(value)}%`));
    const ticks = Math.min(dates.length, Math.max(2, Math.floor((right - left) / 72)));
    new Set(Array.from({length: ticks}, (_, i) => Math.round(i / Math.max(1, ticks - 1) * (dates.length - 1)))).forEach(index => svg.append(shape('text', {x: x(index), y: height - 15, 'text-anchor': index === 0 ? 'start' : index === dates.length - 1 ? 'end' : 'middle', class: 'ops-x-tick'}, shortDate(dates[index]))));
    const observations = new Map(rows.map(row => [row.date, row])), tooltip = el('div', undefined, 'ops-chart-tooltip'); tooltip.hidden = true;
    let segment = [];
    const line = () => { if (segment.length) svg.append(shape('polyline', {points: segment.join(' '), class: 'ops-cleaning-line'})); segment = []; };
    dates.forEach((date, index) => {
      const row = observations.get(date);
      if (!row || !Number.isFinite(row.value)) { line(); return; }
      segment.push(`${x(index)},${y(row.value)}`);
      const text = `Week starting ${date}${row.partial_week ? ' · Partial week' : ''}: ${reviewNumber(row.affected_stays)} of ${reviewNumber(row.total_stays)} stays had cleaning issues (${reviewNumber(row.value)}%).`;
      const dot = shape('circle', {cx: x(index), cy: y(row.value), r: 5, tabindex: 0, role: 'img', 'aria-label': text, class: 'ops-cleaning-dot', 'data-date': date, 'data-value': row.value});
      dot.append(shape('title', {}, text));
      const show = () => { tooltip.textContent = text; tooltip.hidden = false; };
      dot.addEventListener('focus', show); dot.addEventListener('pointerenter', show); dot.addEventListener('click', show);
      dot.addEventListener('blur', () => { tooltip.hidden = true; }); dot.addEventListener('pointerleave', () => { if (document.activeElement !== dot) tooltip.hidden = true; }); svg.append(dot);
    }); line(); container.append(svg, tooltip);
  }
  function drawReviewTrend(rows) {
    const container = $('opsTrendChart');
    if (!rows.some(row => Number.isFinite(row.value))) return;
    const channels = [...new Set(rows.map(row => reviewChannel(row.group)))];
    const dates = [];
    const first = new Date(`${rows[0].date}T00:00:00Z`), end = new Date(`${rows[rows.length - 1].date}T00:00:00Z`);
    for (let d = first; d <= end; d = new Date(d.getTime() + 7 * 86400000)) dates.push(d.toISOString().slice(0, 10));
    const styles = ['current', 'prior', 'booking'];
    const legend = el('div', undefined, 'ops-chart-legend');
    channels.forEach(name => { const item = el('span'), cls = styles[Math.max(0, reviewChannels.indexOf(name))]; item.append(el('i', undefined, `ops-series-swatch ${cls}`), el('span', name)); legend.append(item); });
    const ns = 'http://www.w3.org/2000/svg';
    const shape = (tag, attrs = {}, text) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (text !== undefined) node.textContent = text; return node; };
    const width = Math.max(280, container.clientWidth || root.clientWidth - 70), height = 200, left = 46, right = width - 16, top = 20, bottom = height - 40;
    const maximum = Math.max(100, ...rows.filter(row => Number.isFinite(row.value)).map(row => Math.ceil(row.value / 25) * 25));
    const x = index => left + index / Math.max(1, dates.length - 1) * (right - left), y = value => bottom - value / maximum * (bottom - top);
    const svg = shape('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'ops-chart ops-review-chart'});
    svg.append(shape('title', {}, 'Provisional weekly checkout review rates by channel. Missing weeks are gaps, not zero reviews. Each channel target is shown in the table.'));
    for (let value = 0; value <= maximum; value += 25) { svg.append(shape('line', {x1: left, x2: right, y1: y(value), y2: y(value), class: 'ops-gridline'}), shape('text', {x: left - 6, y: y(value) + 4, 'text-anchor': 'end'}, `${value}%`)); }
    const tickCount = Math.min(dates.length, Math.max(2, Math.floor((right - left) / 72)));
    new Set(Array.from({length: tickCount}, (_, i) => Math.round(i / Math.max(1, tickCount - 1) * (dates.length - 1)))).forEach(index => svg.append(shape('text', {x: x(index), y: height - 14, 'text-anchor': index === 0 ? 'start' : index === dates.length - 1 ? 'end' : 'middle'}, shortDate(dates[index]))));
    channels.forEach(name => {
      const cls = styles[Math.max(0, reviewChannels.indexOf(name))], observations = new Map(rows.filter(row => reviewChannel(row.group) === name).map(row => [row.date, row]));
      let segment = [];
      const line = () => { if (segment.length) svg.append(shape('polyline', {points: segment.join(' '), class: `ops-review-line ${cls}`, 'data-channel': name})); segment = []; };
      dates.forEach((date, index) => {
        const row = observations.get(date);
        if (!row || !Number.isFinite(row.value)) { line(); return; }
        segment.push(`${x(index)},${y(row.value)}`);
        const dot = shape('circle', {cx: x(index), cy: y(row.value), r: 3, class: `ops-review-dot ${cls}`, 'data-channel': name, 'data-value': row.value});
        dot.append(shape('title', {}, `${name} · week starting ${date}: ${reviewNumber(row.value)}%; ${reviewNumber(row.reviewed)} reviews / ${reviewNumber(row.checkout_opportunities ?? row.eligible)} stays; target ${reviewNumber(row.target)}%.`)); svg.append(dot);
      }); line();
    });
    container.append(legend, svg);
  }
  function renderLateReplies() {
    if (!$('opsLateReplies')) return;
    const late = metric.late_replies_30m;
    if (!late || late.schema !== 'matched-replies-30m.v1') {
      $('lateReplyCount').textContent = 'Unavailable';
      $('lateReplyFraction').textContent = 'This saved snapshot does not contain the ≥30-minute metric. A background calculation is required; reloading does not calculate it.';
      $('lateReplyUnmatched').textContent = 'Unmatched requests remain separate from confirmed late replies.';
      $('lateReplyQuality').textContent = 'No ≥30-minute result is inferred from an older saved snapshot.';
      $('lateReplyListNotice').textContent = '';
      $('lateReplyTable').replaceChildren(); return;
    }
    $('lateReplyCount').textContent = Number.isFinite(late.count) ? String(late.count) : 'N/A';
    $('lateReplyFraction').textContent = `${reviewNumber(late.count)} / ${reviewNumber(late.matched_pairs)} matched request/reply pairs · ${Number.isFinite(late.percentage) ? `${reviewNumber(late.percentage)}%` : 'percentage unavailable'}`;
    $('lateReplyQuality').textContent = late.basis === 'verified_meaningful_pairs' ? 'Verified matched-pair evidence; incomplete history still prevents population-level conclusions.' : 'Heuristic matches · original send/dispatch unverified. This does not prove meaningful-answer compliance.';
    $('lateReplyUnmatched').textContent = `${reviewNumber(late.unmatched_request_candidates)} unmatched in-window request candidates (not confirmed late replies).${late.missing_timestamp_messages ? ` ${late.missing_timestamp_messages} messages lack valid timestamps; ${reviewNumber(late.unplaced_request_candidates)} request candidates cannot be placed in the window.` : ''}`;
    $('lateReplyListNotice').textContent = late.records.length ? `Longest observed matched pairs · showing ${late.records.length} of ${late.count}. Recorded timestamps are displayed in the property timezone.` : 'No matched late-pair records in this saved sample. Check coverage before assessing the target.';
    const localTime = (value, zone) => { if (!value) return 'N/A'; if (!zone) return value; try { return new Date(value).toLocaleString(undefined, {timeZone: zone, timeZoneName: 'short'}); } catch (_) { return value; } };
    $('lateReplyTable').tabIndex = 0; $('lateReplyTable').setAttribute('role', 'region'); $('lateReplyTable').setAttribute('aria-label', 'Matched reply diagnostics. Scroll horizontally if needed.');
    table($('lateReplyTable'), late.records.map(row => ({property: row.property, thread: row.thread_reference, recorded_request_time: localTime(row.request_at, row.property_timezone), recorded_reply_time: localTime(row.reply_at, row.property_timezone), elapsed_minutes: row.elapsed_minutes})), late.matched_pairs ? 'No observed matched replies ≥30 minutes.' : 'No matched-pair denominator is available.');
  }
  function visual(container, m) {
    container.replaceChildren();
    const track = el('span', undefined, 'ops-track');
    const percentage = ['revenue', 'content', 'reviews'].includes(m.key) ? m.value : m.key === 'issues' && m.denominator ? Math.min(100, m.value / m.denominator * 100) : m.key === 'response' && m.value !== null ? Math.min(100, m.value / 5 * 100) : m.key === 'cleaning' && m.cleaning_weekly?.schema === 'affected-stay-share.v3' ? m.cleaning_weekly.counts.percentage : null;
    if (percentage === null) track.classList.add('is-unknown');
    else { const fill = el('span'); fill.style.width = `${Math.max(0, Math.min(100, percentage))}%`; track.dataset.verified = String(m.data_status === 'verified'); track.append(fill); }
    const text = m.visual_label || (percentage === null ? (m.data_status === 'deferred' ? m.summary : 'Evidence gap · target comparison pending') : `${m.display} ${m.label}`);
    container.setAttribute('aria-label', text); container.append(track, el('small', text));
  }
  function renderCards() {
    data.metrics.forEach(m => {
      const card = root.querySelector(`[data-goal="${m.key}"]`);
      if (!card) return;
      const field = name => card.querySelector(`[data-field="${name}"]`);
      badge(field('status'), m); field('value').textContent = m.display;
      field('label').textContent = m.label; field('target').textContent = m.target;
      visual(field('visual'), m); field('note').textContent = m.summary;
      field('observation').hidden = !m.observation; field('observation').textContent = m.observation || '';
      const checked = m.sources.map(s => s.checked_at).filter(Boolean).sort();
      field('freshness').textContent = m.data_status === 'deferred' ? 'Tracking deferred' : m.source_freshness === 'stale_at_calculation' ? 'Upstream source stale at calculation' : !m.sources.length ? 'Awaiting saved calculation' : m.sources.some(s => !s.checked_at) ? 'Source freshness incomplete' : `Oldest source check ${stamp(checked[0])}`;
    });
    $('opsGrid').setAttribute('aria-busy', 'false');
  }
  function drawRevenueTrend(rows) {
    const container = $('opsTrendChart');
    const values = rows.flatMap(row => [row.booking_value_usd, row.prior_booking_value_usd]).filter(Number.isFinite);
    if (!values.length) { container.append(el('div', 'No observed booking values are available for this comparison.', 'ops-empty')); return; }
    const legend = el('div', undefined, 'ops-chart-legend');
    const series = [{key: 'booking_value_usd', name: 'This year', cls: 'current'}, {key: 'prior_booking_value_usd', name: 'Previous year', cls: 'prior'}];
    series.forEach(s => { const item = el('span'); item.append(el('i', undefined, `ops-series-swatch ${s.cls}`), el('span', s.name)); legend.append(item); });
    if (rows.some(row => row.partial_week)) legend.append(el('span', '* Partial week'));
    if (rows.some(row => row.yoy_unavailable_reason === 'Different calendar day counts')) legend.append(el('span', '† Unequal calendar lengths'));
    container.append(legend);
    const ns = 'http://www.w3.org/2000/svg';
    const shape = (tag, attrs = {}, text) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); if (text !== undefined) node.textContent = text; return node; };
    const width = Math.max(280, container.clientWidth || root.clientWidth - 70), height = 260;
    const left = 66, right = width - 16, top = 28, bottom = height - 48;
    const maximum = Math.max(1, ...values), roughStep = maximum / 4;
    const power = 10 ** Math.floor(Math.log10(roughStep));
    const step = [1, 2, 2.5, 5, 10].find(n => n >= roughStep / power) * power;
    const yMax = Math.ceil((maximum || 1) / step) * step;
    const x = index => left + index / Math.max(1, rows.length - 1) * (right - left);
    const y = value => bottom - value / yMax * (bottom - top);
    const svg = shape('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', class: 'ops-chart ops-revenue-chart', 'data-y-max': yMax});
    svg.append(shape('title', {}, 'Estimated booking value in USD, this year and previous year, for the same common-listing cohort. Missing observations remain gaps; the exact comparison is in the table.'));
    svg.append(shape('text', {x: 4, y: 15, class: 'ops-axis-label'}, 'USD'));
    for (let i = 0; i <= Math.round(yMax / step); i++) {
      const value = i * step, yy = y(value);
      svg.append(shape('line', {x1: left, x2: right, y1: yy, y2: yy, class: 'ops-gridline'}));
      const tick = shape('text', {x: left - 8, y: yy + 4, 'text-anchor': 'end', class: 'ops-y-tick', 'data-value': value}, axisUsd.format(value));
      tick.setAttribute('aria-label', usd.format(value)); svg.append(tick);
    }
    svg.append(shape('line', {x1: left, x2: left, y1: top, y2: bottom, class: 'ops-axis'}));
    const tickCount = Math.min(rows.length, Math.max(2, Math.floor((right - left) / 72)));
    const indexes = new Set(Array.from({length: tickCount}, (_, i) => Math.round(i / Math.max(1, tickCount - 1) * (rows.length - 1))));
    indexes.forEach(index => svg.append(shape('text', {x: x(index), y: height - 26, 'text-anchor': index === 0 ? 'start' : index === rows.length - 1 ? 'end' : 'middle', class: 'ops-x-tick'}, shortDate(rows[index].date) + (rows[index].partial_week ? '*' : ''))));
    svg.append(shape('text', {x: left, y: height - 6, class: 'ops-axis-label'}, 'Week commencing · property local'));
    series.forEach(s => {
      let segment = [];
      const line = () => { if (segment.length) svg.append(shape('polyline', {points: segment.join(' '), class: `ops-revenue-line ${s.cls}`})); segment = []; };
      rows.forEach((row, index) => {
        const value = row[s.key];
        if (!Number.isFinite(value)) { line(); return; }
        segment.push(`${x(index)},${y(value)}`);
      }); line();
      rows.forEach((row, index) => {
        if (Number.isFinite(row[s.key])) svg.append(shape('circle', {cx: x(index), cy: y(row[s.key]), r: 3.5, class: `ops-revenue-dot ${s.cls}`, 'data-series': s.cls, 'data-date': row.date, 'data-value': row[s.key]}));
      });
    });
    rows.forEach((row, index) => { if (row.yoy_unavailable_reason === 'Different calendar day counts') svg.append(shape('text', {x: x(index), y: top - 8, 'text-anchor': 'middle', class: 'ops-span-warning'}, '†')); });
    const tooltip = el('div', undefined, 'ops-chart-tooltip'); tooltip.hidden = true;
    rows.forEach((row, index) => {
      const current = Number.isFinite(row.booking_value_usd) ? usd.format(row.booking_value_usd) : 'N/A';
      const prior = Number.isFinite(row.prior_booking_value_usd) ? usd.format(row.prior_booking_value_usd) : 'N/A';
      const change = Number.isFinite(row.yoy_change_pct) ? `${signedPercent.format(row.yoy_change_pct)}%` : `N/A${row.yoy_unavailable_reason ? ` (${row.yoy_unavailable_reason})` : ''}`;
      const text = `Week of ${row.date}${row.partial_week ? ' · Partial week' : ''}\nThis year ${current} · Previous year ${prior} · YoY ${change}\nCurrent dates: ${(row.current_spans || []).join('; ')}\nPrior dates: ${(row.prior_spans || []).join('; ')}`;
      const half = (right - left) / Math.max(1, rows.length - 1) / 2;
      const hit = shape('g', {tabindex: 0, role: 'img', 'aria-label': text, class: 'ops-chart-hit', 'data-date': row.date});
      hit.append(shape('title', {}, text), shape('rect', {x: Math.max(left, x(index) - half), y: top, width: rows.length === 1 ? right - left : Math.min(right, x(index) + half) - Math.max(left, x(index) - half), height: bottom - top, fill: 'transparent'}));
      const show = () => { tooltip.textContent = text; tooltip.hidden = false; };
      hit.addEventListener('pointerenter', show); hit.addEventListener('focus', show); hit.addEventListener('click', show);
      hit.addEventListener('pointerleave', () => { if (document.activeElement !== hit) tooltip.hidden = true; });
      hit.addEventListener('blur', () => { tooltip.hidden = true; }); svg.append(hit);
    });
    container.append(svg, tooltip);
  }
  function drawTrend() {
    const group = $('trendFilter').value;
    let rows = metric.key === 'cleaning' ? (cleaningEvidence() ? group === '__all__' ? metric.trend : cleaningEvidence().property_rows.filter(row => String(row.listing_id) === group) : []) : metric.trend.filter(x => metric.key === 'reviews' ? group === '__all__' || reviewChannel(x.group) === group : !x.group || x.group === group);
    if (metric.key === 'reviews') rows.sort((a, b) => a.date.localeCompare(b.date) || reviewChannels.indexOf(reviewChannel(a.group)) - reviewChannels.indexOf(reviewChannel(b.group)));
    if (metric.key === 'revenue') {
      const compatible = metric.trend_schema === 'revenue-weekly-yoy.v1';
      revenueTable($('opsTrend'), rows, compatible);
      if (!compatible) rows = [];
    } else if (metric.key === 'reviews') reviewTable($('opsTrend'), rows);
    else if (metric.key === 'cleaning') cleaningTable($('opsTrend'), rows);
    else table($('opsTrend'), rows, 'No verified historical series. Current status is not projected into the past.');
    $('opsTrendChart').replaceChildren();
    if (!rows.length) return;
    if (metric.key === 'revenue') { drawRevenueTrend(rows); return; }
    if (metric.key === 'reviews') { drawReviewTrend(rows); return; }
    if (metric.key === 'cleaning') { drawCleaningTrend(rows); return; }
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 780 150'); svg.setAttribute('role', 'img'); svg.classList.add('ops-chart');
    const title = document.createElementNS(ns, 'title'); title.textContent = `${label(metric.data_status)} observations; gaps are unknown. Values and assumptions are in the table below.`; svg.append(title);
    const values = rows.map(x => x.value).filter(x => x !== null && x !== undefined);
    if (!values.length) return;
    const max = Math.max(1, ...values), min = Math.min(0, ...values);
    const xy = (x, i) => [30 + i / Math.max(1, rows.length - 1) * 720, 120 - (x.value - min) / (max - min) * 100];
    let segment = [];
    const line = () => { if (!segment.length) return; const p = document.createElementNS(ns, 'polyline'); p.setAttribute('points', segment.join(' ')); svg.append(p); segment = []; };
    rows.forEach((x, i) => { if (x.value === null || x.value === undefined) { line(); return; } const [cx, cy] = xy(x, i); segment.push(`${cx},${cy}`); const dot = document.createElementNS(ns, 'circle'); dot.setAttribute('cx', cx); dot.setAttribute('cy', cy); dot.setAttribute('r', '3'); svg.append(dot); }); line();
    [[30, rows[0].date], [650, rows[rows.length - 1].date]].forEach(([x, value]) => { const t = document.createElementNS(ns, 'text'); t.setAttribute('x', x); t.setAttribute('y', '145'); t.textContent = value; svg.append(t); });
    $('opsTrendChart').append(svg);
  }
  function renderDetail() {
    metric = data.metrics.find(m => m.key === root.dataset.metric);
    if (!metric) throw new Error('This goal was not returned by the data service.');
    badge($('detailStatus'), metric); $('detailValue').textContent = metric.display;
    $('detailValueLabel').textContent = metric.label; if ($('detailTarget')) $('detailTarget').textContent = metric.target;
    $('detailSummary').textContent = metric.summary;
    if ($('opsFormula')) definitions($('opsFormula'), [{label: metric.numerator_label, value: metric.numerator}, {label: metric.denominator_label, value: metric.denominator}]);
    const diagnostics = metric.diagnostics.slice(0, 4).map(item => metric.key === 'revenue' && !metric.booking_estimate_policy && item.label === 'Bookings excluded for incomplete financial fields / USD' ? {...item, label: 'Earlier-policy financial exclusions (not window scoped)'} : item);
    if ($('opsDiagnostics')) definitions($('opsDiagnostics'), diagnostics);
    if ($('exceptionCount')) {
    $('exceptionCount').textContent = metric.exception_count ? `${metric.exception_count} evidence or action items${metric.exception_count > metric.exceptions.length ? ` · first ${metric.exceptions.length} shown` : ''}. Review source records before changing workflows.` : 'No exceptions returned. Check source completeness before treating this as clear.';
    $('opsExceptions').replaceChildren();
    const actions = [...new Map(metric.exceptions.map(x => [x.action, x])).values()].slice(0, 5);
    actions.forEach(x => { const item = el('div', x.action, 'ops-exception'); if (x.source_id || x.listing_id) item.append(el('small', `${x.listing_id ? `Example listing ${x.listing_id}` : ''}${x.source_id ? ` · Source record ${x.source_id}` : ''}`)); $('opsExceptions').append(item); });
    }
    if (metric.key === 'cleaning') {
      const counts = cleaningEvidence()?.counts;
      const legacyAffectedStays = !counts && metric.label?.includes('affected booked check-ins') && Number.isFinite(metric.numerator) && metric.denominator > 0;
      $('detailValue').textContent = counts ? counts.total_stays === 0 ? 'No stays' : Number.isFinite(counts.affected_stays) && Number.isFinite(counts.total_stays) ? `${counts.affected_stays} of ${counts.total_stays}` : 'Unavailable' : legacyAffectedStays ? `${metric.numerator} of ${metric.denominator}` : 'Unavailable';
      $('detailValueLabel').textContent = 'stays had cleaning issues';
      $('cleaningShare').textContent = Number.isFinite(counts?.percentage) ? `${reviewNumber(counts.percentage)}%` : 'N/A';
      $('detailSummary').textContent = 'Each stay counts once, even with multiple cleaning issues, based on recorded guest issues.';
      const checked = metric.sources.map(source => source.checked_at).filter(Boolean).sort();
      $('cleaningFreshness').textContent = metric.source_freshness === 'stale_at_calculation' ? 'Source data was stale at calculation.' : metric.sources.some(source => !source.checked_at) || !checked.length ? 'Source freshness incomplete.' : `Oldest source check ${stamp(checked[0])}`;
    }
    const observedGroups = [...new Set(metric.trend.map(x => x.group).filter(Boolean).map(g => metric.key === 'reviews' ? reviewChannel(g) : g))];
    const groups = metric.key === 'reviews' ? ['__all__', ...reviewChannels, ...observedGroups.filter(g => !reviewChannels.includes(g))] : metric.key === 'cleaning' ? ['__all__', ...(cleaningEvidence()?.properties || []).map(row => String(row.listing_id))] : observedGroups;
    const missingChannels = reviewChannels.filter(name => !observedGroups.includes(name));
    const previousSelection = trendSelection ?? $('trendFilter').value;
    $('trendFilter').replaceChildren(...groups.map(g => { const name = metric.key === 'cleaning' ? g === '__all__' ? 'All properties' : cleaningEvidence().properties.find(row => String(row.listing_id) === g).name : g === '__all__' ? 'All channels' : missingChannels.includes(g) ? `${g} · no weekly data` : g; const o = el('option', name); o.value = g; return o; }));
    if (groups.includes(previousSelection)) $('trendFilter').value = previousSelection;
    trendSelection = $('trendFilter').value;
    $('trendFilterLabel').hidden = !groups.length;
    $('trendNotice').textContent = metric.key === 'revenue' ? (metric.trend_schema === 'revenue-weekly-yoy.v1' ? 'Same calendar dates last year (not ISO weeks). Partial weeks match clipped dates; YoY is N/A for missing/zero baselines or unequal leap-day spans.' : 'This saved snapshot does not include the weekly comparison. The other saved metrics remain available.') : metric.key === 'reviews' ? 'Week starting · Monday checkout buckets, not individual departure dates. Provisional rates include still-open review opportunities.' : metric.trend.length ? 'Only recorded observations are shown. Unknown values remain gaps; clipped calendar weeks are marked in the table.' : 'History is unavailable until qualifying dated evidence is recorded. No synthetic trend is shown.';
    if (metric.key === 'cleaning') $('trendNotice').textContent = cleaningEvidence() ? 'Week starting · stays grouped by check-in. Weeks without stays have no percentage.' : 'Weekly counts and percentages are unavailable in this older snapshot; no values are inferred.';
    $('revenueQualityNote').hidden = metric.key !== 'revenue' || metric.data_status === 'verified';
    $('revenuePolicyNote').hidden = metric.key !== 'revenue' || metric.data_status === 'verified' || !data.as_of;
    $('revenuePolicyNote').textContent = metric.key === 'revenue' && metric.booking_estimate_policy === 'known-components-unknown-tax.v2' ? 'Blank-tax bookings are included as estimates; unknown tax may remain in their values.' : 'This saved result uses the earlier blank-tax exclusion policy; it has not been recomputed under the new rule.';
    $('reviewChannelNotice').hidden = metric.key !== 'reviews' || !missingChannels.length;
    $('reviewChannelNotice').textContent = metric.key === 'reviews' && missingChannels.length ? `Weekly data unavailable for ${missingChannels.join(', ')} in this saved snapshot. No zero rate is inferred.` : '';
    $('opsTrendScope').hidden = metric.key !== 'revenue' || !metric.trend_scope;
    $('opsTrendScope').textContent = metric.key === 'revenue' ? metric.trend_scope || '' : '';
    drawTrend();
    renderLateReplies();
    if ($('opsBreakdown')) { if (metric.key === 'cleaning') cleaningBreakdown(); else table($('opsBreakdown'), metric.breakdown); }
    if ($('opsSources')) {
    $('opsSources').replaceChildren();
    metric.sources.forEach(s => { const node = el('div', undefined, 'ops-source'); node.append(el('strong', `${s.name} · ${s.stale ? 'stale' : s.status}`), el('p', s.provenance || 'Source unavailable'), el('p', `Source check: ${stamp(s.checked_at)}`)); if (s.analysis_completed_at) node.append(el('p', `Analysis completed: ${stamp(s.analysis_completed_at)} · does not establish ingestion completeness`)); if (s.rows_read !== undefined) node.append(el('p', `${s.rows_read} source rows read`)); if (s.reason) node.append(el('p', s.reason)); $('opsSources').append(node); });
    }
    $('opsDetail').hidden = false;
  }
  async function load({quiet = false} = {}) {
    if (inFlight) return;
    inFlight = true; $('opsRefresh').disabled = true; $('opsRetry').disabled = true;
    if (!quiet) $('opsLoading').hidden = false;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 45000);
      let response;
      try { response = await fetch(root.dataset.endpoint, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: {'Accept': 'application/json'}}); } finally { clearTimeout(timer); }
      if (!response.ok) throw new Error(`Data request failed (${response.status}).`);
      const next = await response.json();
      if (!Array.isArray(next.metrics) || next.metrics.length !== 6) throw new Error('The six-goal response is incomplete.');
      data = next; $('opsError').hidden = true;
      if ($('opsGrid')) $('opsGrid').hidden = false;
      const ranges = data.window.ranges;
      const saved = data.snapshot_state || {};
      $('opsWindow').textContent = ranges.length === 1 ? `${ranges[0].start_inclusive} → ${ranges[0].end_exclusive} (exclusive) · ${ranges[0].days} days · property-local` : ranges.length ? 'Property-local dates differ · see each detail page' : saved.status && saved.status !== 'available' ? 'Window available after the first saved calculation' : 'Property-local dates unavailable · timezone evidence missing';
      $('opsAsOf').textContent = `Calculation cutoff ${stamp(saved.window_as_of || data.as_of)}`;
      $('opsChecked').textContent = `Calculated ${stamp(saved.calculated_at || data.generated_at)} · next due ${stamp(saved.next_due_at)} · every 48h`;
      const notices = [];
      if (saved.status === 'storage_unavailable') notices.push('Saved results storage is unavailable. No calculation was triggered.');
      else if (saved.status === 'contract_update_pending') notices.push('Waiting for the scheduled job to publish the current metric definition.');
      else if (saved.status === 'not_yet_calculated') notices.push('Not calculated yet. The scheduled background job will publish the first scorecard; page reloads do not calculate metrics.');
      if (saved.refresh_status === 'failed' || saved.refresh_status === 'interrupted') notices.push(saved.calculated_at ? 'The latest calculation did not finish successfully. Showing the previous saved results.' : 'The latest calculation did not finish successfully; no previous results are available.');
      if (saved.refresh_status === 'running') notices.push('The scheduled calculation is running. Saved results stay visible until all six goals are published.');
      if (saved.schedule_status === 'overdue' && saved.refresh_status !== 'running') notices.push('The scheduled calculation is overdue. Source-quality warnings below are separate.');
      $('opsSnapshotNotice').hidden = !notices.length; $('opsSnapshotNotice').textContent = notices.join(' ');
      if (root.dataset.metric) renderDetail(); else renderCards();
    } catch (error) {
      $('opsError').hidden = false;
      $('opsErrorMessage').textContent = data ? `Latest read failed. Previous values are hidden until a successful read. ${error.message}` : error.message;
      if ($('opsGrid')) $('opsGrid').hidden = true;
      if ($('opsDetail')) $('opsDetail').hidden = true;
    } finally { inFlight = false; $('opsLoading').hidden = true; $('opsRefresh').disabled = false; $('opsRetry').disabled = false; }
  }
  $('opsRefresh').addEventListener('click', () => load()); $('opsRetry').addEventListener('click', () => load());
  if ($('trendFilter')) $('trendFilter').addEventListener('change', () => { trendSelection = $('trendFilter').value; try { sessionStorage.setItem(filterStorageKey, trendSelection); } catch (_) {} drawTrend(); });
  if ($('breakdownSearch')) $('breakdownSearch').addEventListener('input', event => { if (metric.key === 'cleaning') { cleaningBreakdown(); return; } const query = event.target.value.toLowerCase(); table($('opsBreakdown'), metric.breakdown.filter(x => Object.values(x).some(v => String(v ?? '').toLowerCase().includes(query))), 'No matching breakdown rows.'); });
  // Auto-read visible tabs only; never starts a source sync or workflow write.
  setInterval(() => { if (document.visibilityState === 'visible') load({quiet: true}); }, 60000);
  window.addEventListener('pageshow', event => { if (event.persisted) load({quiet: true}); });
  let chartWidth = 0;
  new ResizeObserver(entries => { const width = Math.round(entries[0].contentRect.width); if (width > 0 && width !== chartWidth) { chartWidth = width; if (metric && ['revenue', 'reviews', 'cleaning'].includes(metric.key)) drawTrend(); } }).observe($('opsTrendChart') || root);
  load();
})();
