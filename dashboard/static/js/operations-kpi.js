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
  let inFlight = false, data = null, metric = null;

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
  function visual(container, m) {
    container.replaceChildren();
    const track = el('span', undefined, 'ops-track');
    const percentage = ['revenue', 'content', 'reviews'].includes(m.key) ? m.value : m.key === 'issues' && m.denominator ? Math.min(100, m.value / m.denominator * 100) : m.key === 'response' && m.value !== null ? Math.min(100, m.value / 5 * 100) : m.key === 'cleaning' && m.value !== null ? Math.min(100, m.value * 100) : null;
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
  function drawTrend() {
    const group = $('trendFilter').value;
    const rows = metric.trend.filter(x => !x.group || x.group === group);
    table($('opsTrend'), rows, 'No verified historical series. Current status is not projected into the past.');
    $('opsTrendChart').replaceChildren();
    if (!rows.length) return;
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
    $('detailValueLabel').textContent = metric.label; $('detailTarget').textContent = metric.target;
    $('detailSummary').textContent = metric.summary; $('opsMethod').textContent = metric.method;
    definitions($('opsFormula'), [{label: metric.numerator_label, value: metric.numerator}, {label: metric.denominator_label, value: metric.denominator}]);
    definitions($('opsDiagnostics'), metric.diagnostics.slice(0, 4));
    $('exceptionCount').textContent = metric.exception_count ? `${metric.exception_count} evidence or action items${metric.exception_count > metric.exceptions.length ? ` · first ${metric.exceptions.length} shown` : ''}. Review source records before changing workflows.` : 'No exceptions returned. Check source completeness before treating this as clear.';
    $('opsExceptions').replaceChildren();
    const actions = [...new Map(metric.exceptions.map(x => [x.action, x])).values()].slice(0, 5);
    actions.forEach(x => { const item = el('div', x.action, 'ops-exception'); if (x.source_id || x.listing_id) item.append(el('small', `${x.listing_id ? `Example listing ${x.listing_id}` : ''}${x.source_id ? ` · Source record ${x.source_id}` : ''}`)); $('opsExceptions').append(item); });
    const groups = [...new Set(metric.trend.map(x => x.group).filter(Boolean))];
    $('trendFilter').replaceChildren(...groups.map(g => { const o = el('option', g); o.value = g; return o; }));
    $('trendFilterLabel').hidden = !groups.length;
    $('trendNotice').textContent = metric.trend.length ? 'Only recorded observations are shown. Unknown values remain gaps; clipped calendar weeks are marked in the table.' : 'History is unavailable until qualifying dated evidence is recorded. No synthetic trend is shown.';
    drawTrend();
    table($('opsBreakdown'), metric.breakdown);
    table($('opsPropertyWindows'), data.property_windows);
    $('opsSources').replaceChildren();
    metric.sources.forEach(s => { const node = el('div', undefined, 'ops-source'); node.append(el('strong', `${s.name} · ${s.stale ? 'stale' : s.status}`), el('p', s.provenance || 'Source unavailable'), el('p', `Source check: ${stamp(s.checked_at)}`)); if (s.analysis_completed_at) node.append(el('p', `Analysis completed: ${stamp(s.analysis_completed_at)} · does not establish ingestion completeness`)); if (s.rows_read !== undefined) node.append(el('p', `${s.rows_read} source rows read`)); if (s.reason) node.append(el('p', s.reason)); $('opsSources').append(node); });
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
  if ($('trendFilter')) $('trendFilter').addEventListener('change', drawTrend);
  if ($('breakdownSearch')) $('breakdownSearch').addEventListener('input', event => { const query = event.target.value.toLowerCase(); table($('opsBreakdown'), metric.breakdown.filter(x => Object.values(x).some(v => String(v ?? '').toLowerCase().includes(query))), 'No matching breakdown rows.'); });
  // Auto-read visible tabs only; never starts a source sync or workflow write.
  setInterval(() => { if (document.visibilityState === 'visible') load({quiet: true}); }, 60000);
  window.addEventListener('pageshow', event => { if (event.persisted) load({quiet: true}); });
  load();
})();
