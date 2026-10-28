// Run with: node --test dashboard/reviews/tests/test_resolution_views.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../static/js/review-resolutions.js', import.meta.url), 'utf8');

function setup({ storedView = null, storageBlocked = false, fetch } = {}) {
    const elements = new Map();
    const ready = [];
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            innerHTML: '', textContent: '', hidden: false, value: '', dataset: {}, attributes: {},
            listeners: {}, style: { setProperty() {} },
            classList: { add() {}, remove() {}, toggle() {} },
            addEventListener(type, handler) { this.listeners[type] = handler; },
            setAttribute(key, value) { this.attributes[key] = value; },
            querySelector() { return null; },
        });
        return elements.get(id);
    }
    const context = vm.createContext({
        document: {
            addEventListener(type, handler) { if (type === 'DOMContentLoaded') ready.push(handler); },
            getElementById: element,
            querySelectorAll() { return []; },
            createElement() {
                return { textContent: '', get innerHTML() { return this.textContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } };
            },
        },
        localStorage: {
            getItem() { if (storageBlocked) throw new Error('blocked'); return storedView; },
            setItem(key, value) { if (storageBlocked) throw new Error('blocked'); storedView = value; },
        },
        window: { clearTimeout() {}, setTimeout() {} },
        URLSearchParams,
        fetch: fetch || (async () => ({ ok: true, json: async () => ({ stages: ['New', 'Resolved'], lanes: [] }) })),
    });
    const run = (code) => vm.runInContext(code, context);
    run(source);
    return { run, element, ready, getStoredView: () => storedView };
}

test('both views use the same portfolio, status, and readable source search', () => {
    const { run } = setup();
    run(`resolutionState.lanes = [{stage: 'New', reviews: [
        {ticket_id: 1, stage: 'New', portfolio: null, channel_name: 'bookingcom', guest_name: 'Alex'},
        {ticket_id: 2, stage: 'New', portfolio: 'North', channel_name: 'airbnbofficial', guest_name: 'Sam'},
    ]}, {stage: 'Resolved', reviews: [
        {ticket_id: 3, stage: 'Resolved', portfolio: 'North', channel_name: 'airbnbofficial', guest_name: 'Sam'},
    ]}];`);
    const ids = () => run('JSON.stringify(resolutionState.lanes.flatMap(lane => lane.reviews).filter(resolutionMatchesFilters).map(review => review.ticket_id))');
    run(`resolutionState.portfolio = 'Unmapped'; resolutionState.search = 'booking.com';`);
    assert.equal(ids(), '[1]');
    run(`resolutionState.portfolio = 'North'; resolutionState.search = 'airbnb'; resolutionState.stage = 'Resolved';`);
    assert.equal(ids(), '[3]');
    run(`resolutionState.view = 'table';`);
    assert.equal(ids(), '[3]');
});

test('table sorting is numeric for ratings and keeps missing values last', () => {
    const { run } = setup();
    run(`resolutionState.sort = 'rating'; var reviews = [{ticket_id: 1, rating: null}, {ticket_id: 2, rating: 4}, {ticket_id: 3, rating: 2}];`);
    assert.equal(run('JSON.stringify(resolutionSortedReviews(reviews).map(r => r.ticket_id))'), '[2,3,1]');
    run(`resolutionState.sortDirection = 'asc';`);
    assert.equal(run('JSON.stringify(resolutionSortedReviews(reviews).map(r => r.ticket_id))'), '[3,2,1]');
    assert.equal(run('reviews[0].ticket_id'), 1, 'sorting must not mutate the shared review list');
});

test('failed inline status changes restore the original lane and selected status', async () => {
    const requests = [];
    const { run, element } = setup({ fetch: async (url, options) => {
        requests.push({ url, options });
        return { ok: false, status: 503, json: async () => ({ error: 'Unable to save' }) };
    } });
    run(`resolutionState.loading = false; resolutionState.view = 'table';
        resolutionState.stages = ['New', 'Resolved'];
        resolutionState.lanes = [{stage: 'New', reviews: [{ticket_id: 7, stage: 'New', guest_name: 'Alex'}]}, {stage: 'Resolved', reviews: []}];`);
    await run(`moveResolution(7, 'Resolved')`);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/reviews/api/resolutions/7/stage');
    assert.deepEqual(JSON.parse(requests[0].options.body), { stage: 'Resolved' });
    assert.equal(run('resolutionState.lanes[0].reviews[0].stage'), 'New');
    assert.equal(run('resolutionState.lanes[1].reviews.length'), 0);
    assert.equal(run('resolutionState.movingTicketId'), null);
    assert.match(element('resolutionTable').innerHTML, /value="New" selected/);
});

test('saved view restores on load and toggles without fetching again', async () => {
    let requests = 0;
    const { run, element, ready, getStoredView } = setup({ storedView: 'table', fetch: async () => {
        requests++;
        return { ok: true, json: async () => ({ stages: [], lanes: [] }) };
    } });
    ready[0]();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(run('resolutionState.view'), 'table');
    assert.equal(element('resolutionBoard').hidden, true);
    element('resolutionViewToggle').listeners.click({ target: { closest: () => ({ dataset: { resolutionView: 'swimlane' } }) } });
    assert.equal(getStoredView(), 'swimlane');
    assert.equal(element('resolutionTable').hidden, true);
    assert.equal(requests, 1);
});

test('storage failure does not block loading or changing the view', async () => {
    const { run, element, ready } = setup({ storageBlocked: true });
    ready[0]();
    await new Promise(resolve => setImmediate(resolve));
    element('resolutionViewToggle').listeners.click({ target: { closest: () => ({ dataset: { resolutionView: 'table' } }) } });
    assert.equal(run('resolutionState.view'), 'table');
    assert.equal(run('resolutionState.loading'), false);
    assert.equal(element('resolutionTable').hidden, false);
});

test('load errors remain visible when switching views instead of exposing stale cases', async () => {
    const { run, element } = setup({ fetch: async () => { throw new Error('Network unavailable'); } });
    await run('loadResolutions()');
    run(`resolutionState.view = 'table'; renderResolutionBoard();`);
    assert.match(element('resolutionTable').innerHTML, /Network unavailable/);
    assert.equal(element('resolutionBoard').hidden, true);
});

test('custom reporting dates are forwarded on every reload', async () => {
    let requestedUrl;
    const { run } = setup({ fetch: async (url) => {
        requestedUrl = url;
        return { ok: true, json: async () => ({ stages: [], lanes: [] }) };
    } });
    run(`resolutionState.view = 'table'; resolutionState.startDate = '2026-10-01'; resolutionState.endDate = '2026-10-28';`);
    await run('loadResolutions()');
    assert.equal(requestedUrl, '/reviews/api/resolutions?start_date=2026-10-01&end_date=2026-10-28');
});

test('adding or reordering workflow statuses cannot swap Disputed and Resolved colors', () => {
    const { run } = setup();
    run(`resolutionState.stages = ['Resolved', 'New', 'Disputed', 'No Response'];`);
    assert.match(run(`createResolutionRow({ticket_id: 1, stage: 'Resolved'})`), /data-stage-tone="positive"/);
    assert.match(run(`createResolutionRow({ticket_id: 2, stage: 'Disputed'})`), /data-stage-tone="disputed"/);
    assert.match(run(`createResolutionRow({ticket_id: 3, stage: 'No Response'})`), /data-stage-tone="neutral"/);
});
