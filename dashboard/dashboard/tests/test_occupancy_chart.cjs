const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/dashboard-page.js'), 'utf8');
function setup(chartAvailable = true) {
    const nodes = new Map();
    const calls = [];
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {style: {}, innerHTML: '', textContent: '', open: false, getContext: () => ({})});
        return nodes.get(id);
    };
    const context = {window: {}, console, document: {getElementById: node, addEventListener: () => {},
        createElement: () => ({set textContent(value) {this.innerHTML = String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;');}})}};
    if (chartAvailable) context.Chart = function (_, config) {calls.push(config); this.destroy = () => calls.push('destroyed');};
    vm.createContext(context); vm.runInContext(source, context);
    return {node, calls, render: (rows, meta) => context.renderOccupancyChart(rows, meta)};
}
const rows = Array.from({length: 12}, (_, i) => ({month: `${i < 9 ? 2026 : 2027}-${String((i+3)%12+1).padStart(2,'0')}`,
    period: i < 6 ? 'historical' : 'on_books', is_current_month: i === 6,
    booked_nights: i === 0 ? 0 : 15, capacity_nights: 30, occupancy_rate: i === 0 ? 0 : 50}));
test('twelve labels include September, current month, and on-books styling', () => {
    const app = setup(); app.render(rows, {listing_count: 1});
    const config = app.calls[0];
    assert.equal(config.data.labels.length, 12);
    assert.equal(config.data.labels[5], 'Sep 2026');
    assert.deepEqual(Array.from(config.data.labels[6]), ['Oct 2026', 'Current']);
    assert.equal(config.options.scales.x.ticks.autoSkip, false);
    assert.notEqual(config.data.datasets[0].backgroundColor[5], config.data.datasets[0].backgroundColor[6]);
    assert.match(config.options.plugins.tooltip.callbacks.afterLabel({dataIndex: 6}), /15 booked \/ 30 calendar nights/);
    assert.match(app.node('occupancyValues').innerHTML, /0\.00%/);
    assert.equal((app.node('occupancyValues').innerHTML.match(/<tr>/g) || []).length, 12);
});
test('unknown is never coerced to zero and refresh clears the old chart', () => {
    const app = setup(); app.render(rows);
    app.render(rows.map(row => ({...row, occupancy_rate: null, booked_nights: null, capacity_nights: null})));
    assert.equal(app.calls[1], 'destroyed');
    assert.equal(app.calls[2].data.datasets[0].data[0], null);
    assert.equal(app.node('noOccupancyData').style.display, 'block');
    assert.match(app.node('occupancyValues').innerHTML, /Unavailable/);
    app.render([]);
    assert.equal(app.calls[3], 'destroyed');
    assert.equal(app.node('occupancyValues').innerHTML, '');
});
test('table is available when chart library fails and freshness is explicit', () => {
    const app = setup(false); app.render(rows, {syncs: {reservations: {completed_at: '2025-01-01T00:00:00Z'}}});
    assert.equal(app.node('occupancyDetails').open, true);
    assert.match(app.node('occupancySync').textContent, /over 48 hours old/);
    assert.match(app.node('occupancySync').textContent, /listings: sync time unavailable/);
    assert.equal((app.node('occupancyValues').innerHTML.match(/<tr>/g) || []).length, 12);
});
