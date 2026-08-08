const resolutionState = {
    stages: [],
    lanes: [],
    rules: [],
    lookback: {},
    search: '',
    portfolio: '',
    draggedTicketId: null,
};

let resolutionToastTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('resolutionSearch')?.addEventListener('input', (event) => {
        resolutionState.search = event.target.value.trim().toLowerCase();
        renderResolutionBoard();
    });
    document.getElementById('resolutionPortfolioFilter')?.addEventListener('change', (event) => {
        resolutionState.portfolio = event.target.value;
        renderResolutionBoard();
    });
    const board = document.getElementById('resolutionBoard');
    board?.addEventListener('change', handleStageSelect);
    board?.addEventListener('dragstart', handleDragStart);
    board?.addEventListener('dragend', handleDragEnd);
    board?.addEventListener('dragover', handleDragOver);
    board?.addEventListener('dragleave', handleDragLeave);
    board?.addEventListener('drop', handleDrop);
    document.getElementById('resolutionRuleList')?.addEventListener('change', handleRuleChange);
    loadResolutions();
});

async function resolutionFetch(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

async function loadResolutions() {
    const board = document.getElementById('resolutionBoard');
    if (!board) return;
    board.innerHTML = '<div class="review-ops-loading"><span class="review-ops-spinner" aria-hidden="true"></span><span>Loading resolution tickets…</span></div>';
    try {
        const data = await resolutionFetch('/reviews/api/resolutions');
        resolutionState.stages = data.stages || [];
        resolutionState.lanes = data.lanes || [];
        resolutionState.rules = data.rules || [];
        resolutionState.lookback = data.lookback || {};
        document.getElementById('resolutionTotalCount').textContent = data.summary?.total ?? 0;
        document.getElementById('resolutionOpenCount').textContent = data.summary?.open ?? 0;
        document.getElementById('resolutionResolvedCount').textContent = data.summary?.resolved ?? 0;
        renderResolutionRules();
        renderResolutionPortfolioFilter();
        renderResolutionLookback();
        renderResolutionBoard();
    } catch (error) {
        board.innerHTML = `<div class="review-ops-error">${resolutionEscape(error.message)}</div>`;
    }
}

function renderResolutionRules() {
    const container = document.getElementById('resolutionRuleList');
    if (!container) return;
    container.innerHTML = resolutionState.rules.map((rule) => `
        <label class="review-resolution-rule">
            <span class="review-resolution-rule-copy">
                <strong>${resolutionEscape(rule.display_name || rule.portfolio)}</strong>
                <small>${rule.qualifying_count || 0} of ${rule.review_count || 0} six-month reviews qualify</small>
            </span>
            <span class="review-resolution-rule-control">
                <span>Below</span>
                <input
                    type="number"
                    min="1"
                    max="5"
                    step="0.1"
                    value="${Number(rule.bad_review_threshold || 5).toFixed(1)}"
                    data-action="change-rule"
                    data-portfolio="${resolutionEscape(rule.portfolio)}"
                    aria-label="Bad review threshold for ${resolutionEscape(rule.display_name || rule.portfolio)}"
                >
                <span>stars</span>
            </span>
        </label>
    `).join('');
}

function renderResolutionPortfolioFilter() {
    const select = document.getElementById('resolutionPortfolioFilter');
    if (!select) return;

    const counts = new Map();
    resolutionState.lanes.forEach((lane) => {
        (lane.reviews || []).forEach((review) => {
            const portfolio = review.portfolio || 'Unmapped';
            counts.set(portfolio, (counts.get(portfolio) || 0) + 1);
        });
    });
    const displayNames = new Map(
        resolutionState.rules.map((rule) => [rule.portfolio, rule.display_name || rule.portfolio])
    );
    const portfolios = [...counts.entries()].sort(([left], [right]) => {
        if (left === 'Unmapped') return 1;
        if (right === 'Unmapped') return -1;
        return String(displayNames.get(left) || left).localeCompare(String(displayNames.get(right) || right));
    });
    const total = portfolios.reduce((sum, [, count]) => sum + count, 0);

    if (resolutionState.portfolio && !counts.has(resolutionState.portfolio)) {
        resolutionState.portfolio = '';
    }
    select.innerHTML = [
        `<option value="">All portfolios (${total})</option>`,
        ...portfolios.map(([portfolio, count]) => (
            `<option value="${resolutionEscape(portfolio)}">${resolutionEscape(displayNames.get(portfolio) || portfolio)} (${count})</option>`
        )),
    ].join('');
    select.value = resolutionState.portfolio;
}

function renderResolutionLookback() {
    const element = document.getElementById('resolutionLookback');
    if (!element || !resolutionState.lookback.start_date || !resolutionState.lookback.end_date) return;
    element.textContent = `${resolutionFormatDate(resolutionState.lookback.start_date)}–${resolutionFormatDate(resolutionState.lookback.end_date)}`;
}

async function handleRuleChange(event) {
    const input = event.target.closest('[data-action="change-rule"]');
    if (!input) return;
    const threshold = Number(input.value);
    if (!Number.isFinite(threshold) || threshold < 1 || threshold > 5) {
        showResolutionToast('Choose a threshold between 1.0 and 5.0 stars.', true);
        await loadResolutions();
        return;
    }

    input.disabled = true;
    try {
        await resolutionFetch('/reviews/api/resolution-rules', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                portfolio: input.dataset.portfolio,
                bad_review_threshold: threshold,
            }),
        });
        showResolutionToast(`${input.dataset.portfolio} now treats reviews below ${threshold.toFixed(1)} stars as bad.`);
        await loadResolutions();
    } catch (error) {
        input.disabled = false;
        showResolutionToast(error.message, true);
    }
}

function renderResolutionBoard() {
    const board = document.getElementById('resolutionBoard');
    if (!board) return;
    board.innerHTML = resolutionState.stages.map((stage) => {
        const sourceLane = resolutionState.lanes.find((lane) => lane.stage === stage);
        const reviews = (sourceLane?.reviews || []).filter((review) => {
            if (resolutionState.portfolio && review.portfolio !== resolutionState.portfolio) return false;
            if (!resolutionState.search) return true;
            const haystack = `${review.guest_name || ''} ${review.listing_name || ''} ${review.portfolio || ''} ${review.title || ''}`.toLowerCase();
            return haystack.includes(resolutionState.search);
        });
        const emptyMessage = resolutionState.portfolio || resolutionState.search
            ? 'No matching reviews in this stage'
            : 'No reviews in this stage';
        return `
            <section class="review-resolution-lane" data-stage="${resolutionEscape(stage)}">
                <header class="review-resolution-lane-head">
                    <h3>${resolutionEscape(stage)}</h3>
                    <span>${reviews.length}</span>
                </header>
                <div class="review-resolution-lane-list">
                    ${reviews.length ? reviews.map(createResolutionCard).join('') : `<div class="review-resolution-empty">${emptyMessage}</div>`}
                </div>
            </section>
        `;
    }).join('');
}

function createResolutionCard(review) {
    const stageOptions = resolutionState.stages.map((stage) => (
        `<option value="${resolutionEscape(stage)}" ${stage === review.stage ? 'selected' : ''}>${resolutionEscape(stage)}</option>`
    )).join('');
    const rating = review.rating == null ? 'N/A' : `${Number(review.rating).toFixed(1)} ★`;
    const ratingTitle = review.rating_raw == null
        ? 'Rating unavailable'
        : `Normalized from ${Number(review.rating_raw).toFixed(1)} / ${Number(review.rating_source_max || 10).toFixed(0)}`;
    return `
        <article class="review-resolution-card" draggable="true" data-ticket-id="${review.ticket_id}">
            <div class="review-resolution-card-top">
                <span class="review-rating-pill" title="${resolutionEscape(ratingTitle)}">${resolutionEscape(rating)}</span>
                <span class="review-priority-pill">${resolutionEscape(review.priority || 'Medium')}</span>
            </div>
            <h4>${resolutionEscape(review.guest_name || 'Guest')}</h4>
            <p class="review-resolution-meta">${resolutionEscape(review.listing_name || 'Unknown property')} · ${resolutionEscape(review.portfolio || 'Unmapped')}</p>
            <p class="review-resolution-standard">Below ${Number(review.bad_review_threshold || 5).toFixed(1)}-star standard${review.review_date ? ` · ${resolutionFormatDate(review.review_date)}` : ''}</p>
            <p class="review-resolution-excerpt">${resolutionEscape(review.review_text || 'No written review was supplied.')}</p>
            <select data-action="change-stage" data-ticket-id="${review.ticket_id}" aria-label="Resolution stage for ${resolutionEscape(review.guest_name || 'guest')}">
                ${stageOptions}
            </select>
        </article>
    `;
}

async function handleStageSelect(event) {
    const select = event.target.closest('[data-action="change-stage"]');
    if (!select) return;
    await moveResolution(Number(select.dataset.ticketId), select.value);
}

function handleDragStart(event) {
    const card = event.target.closest('.review-resolution-card');
    if (!card) return;
    resolutionState.draggedTicketId = Number(card.dataset.ticketId);
    card.classList.add('is-dragging');
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}

function handleDragEnd(event) {
    event.target.closest('.review-resolution-card')?.classList.remove('is-dragging');
    document.querySelectorAll('.review-resolution-lane.is-drag-over').forEach((lane) => lane.classList.remove('is-drag-over'));
    resolutionState.draggedTicketId = null;
}

function handleDragOver(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (!lane || !resolutionState.draggedTicketId) return;
    event.preventDefault();
    lane.classList.add('is-drag-over');
}

function handleDragLeave(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (lane && !lane.contains(event.relatedTarget)) lane.classList.remove('is-drag-over');
}

async function handleDrop(event) {
    const lane = event.target.closest('.review-resolution-lane');
    if (!lane || !resolutionState.draggedTicketId) return;
    event.preventDefault();
    lane.classList.remove('is-drag-over');
    await moveResolution(resolutionState.draggedTicketId, lane.dataset.stage);
}

async function moveResolution(ticketId, stage) {
    const previous = resolutionState.lanes.map((lane) => ({ ...lane, reviews: [...lane.reviews] }));
    const card = previous.flatMap((lane) => lane.reviews).find((review) => Number(review.ticket_id) === Number(ticketId));
    if (!card || card.stage === stage) return;

    resolutionState.lanes.forEach((lane) => {
        lane.reviews = lane.reviews.filter((review) => Number(review.ticket_id) !== Number(ticketId));
    });
    card.stage = stage;
    const target = resolutionState.lanes.find((lane) => lane.stage === stage);
    if (target) target.reviews.unshift(card);
    renderResolutionBoard();

    try {
        await resolutionFetch(`/reviews/api/resolutions/${ticketId}/stage`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stage }),
        });
        showResolutionToast(`Moved to ${stage}.`);
        await loadResolutions();
    } catch (error) {
        resolutionState.lanes = previous;
        renderResolutionBoard();
        showResolutionToast(error.message, true);
    }
}

function showResolutionToast(message, isError = false) {
    const toast = document.getElementById('reviewToast');
    if (!toast) return;
    window.clearTimeout(resolutionToastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.classList.add('is-visible');
    resolutionToastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 4200);
}

function resolutionEscape(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function resolutionFormatDate(value) {
    if (!value) return '';
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    return Number.isNaN(parsed.getTime())
        ? String(value)
        : parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}
