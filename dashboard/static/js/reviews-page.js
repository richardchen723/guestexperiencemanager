const reviewQueueState = {
    reviews: [],
    search: '',
    risk: 'all',
    summaryFilter: 'all',
};

const reviewSummaryHeadings = {
    all: 'Open review windows',
    needs_host_review: 'Needs host review',
    guest_submitted: 'Guest submitted',
    priority_watch: 'Priority watch',
};

let allTags = [];
let currentEditingFilterId = null;
let toastTimer = null;

document.addEventListener('DOMContentLoaded', async () => {
    const queueContainer = document.getElementById('reviewQueueContainer');
    if (queueContainer) {
        document.getElementById('reviewQueueSearch')?.addEventListener('input', (event) => {
            reviewQueueState.search = event.target.value.trim().toLowerCase();
            renderReviewQueue();
        });
        document.getElementById('reviewRiskFilter')?.addEventListener('change', (event) => {
            reviewQueueState.risk = event.target.value;
            renderReviewQueue();
        });
        document.querySelectorAll('[data-queue-filter]').forEach((metric) => {
            metric.addEventListener('click', () => activateSummaryFilter(metric));
        });
        queueContainer.addEventListener('click', handleQueueClick);
        loadReviewQueue();
    }

    await loadTags();
    loadFilters();
    document.getElementById('filterForm')?.addEventListener('submit', handleFilterSubmit);
    document.getElementById('addFilterBtn')?.addEventListener('click', () => openFilterModal());
});

async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || `Request failed (${response.status})`);
    }
    return data;
}

async function loadReviewQueue() {
    const container = document.getElementById('reviewQueueContainer');
    if (!container) return;
    container.innerHTML = loadingState('Loading review windows…');
    try {
        const data = await fetchJson('/reviews/api/queue');
        reviewQueueState.reviews = data.reviews || [];
        updateQueueSummary(data.summary || {});
        updateWindowDescription(data.window || {});
        renderReviewQueue();
    } catch (error) {
        console.error('Error loading review queue:', error);
        container.innerHTML = `<div class="review-ops-error">${escapeHtml(error.message)}</div>`;
    }
}

function updateQueueSummary(summary) {
    setText('reviewMetricTotal', summary.total ?? 0);
    setText('reviewMetricHost', summary.needs_host_review ?? 0);
    setText('reviewMetricGuest', summary.guest_reviewed ?? 0);
    setText('reviewMetricRisk', summary.high_risk ?? 0);
}

function updateWindowDescription(windowData) {
    const element = document.getElementById('reviewWindowDescription');
    if (!element || !windowData.start_date || !windowData.end_date) return;
    element.textContent = `${formatDate(windowData.start_date, { month: 'short', day: 'numeric' })}–${formatDate(windowData.end_date, { month: 'short', day: 'numeric', year: 'numeric' })} · Highest concern first within each portfolio`;
}

function activateSummaryFilter(metric) {
    reviewQueueState.summaryFilter = metric.dataset.queueFilter || 'all';
    document.querySelectorAll('[data-queue-filter]').forEach((item) => {
        const isActive = item === metric;
        item.classList.toggle('is-active', isActive);
        item.setAttribute('aria-pressed', String(isActive));
    });

    const heading = document.getElementById('reviewQueueHeading');
    if (heading) {
        heading.textContent = reviewSummaryHeadings[reviewQueueState.summaryFilter] || reviewSummaryHeadings.all;
    }
    renderReviewQueue();
}

function matchesSummaryFilter(review) {
    switch (reviewQueueState.summaryFilter) {
        case 'needs_host_review':
            return !review.host_reviewed;
        case 'guest_submitted':
            return Boolean(review.guest_reviewed);
        case 'priority_watch':
            return (review.risk?.order ?? 2) <= 1;
        default:
            return true;
    }
}

function renderReviewQueue() {
    const container = document.getElementById('reviewQueueContainer');
    if (!container) return;
    const filtered = reviewQueueState.reviews.filter((review) => {
        const haystack = `${review.guest_name || ''} ${review.listing_name || ''} ${review.portfolio || ''}`.toLowerCase();
        const matchesSearch = !reviewQueueState.search || haystack.includes(reviewQueueState.search);
        const matchesRisk = reviewQueueState.risk === 'all' || review.risk?.key === reviewQueueState.risk;
        return matchesSearch && matchesRisk && matchesSummaryFilter(review);
    });

    if (!filtered.length) {
        const message = reviewQueueState.search || reviewQueueState.risk !== 'all' || reviewQueueState.summaryFilter !== 'all'
            ? 'No review windows match the current filters.'
            : 'No open review windows. Five-star matches close automatically; lower ratings move to resolution.';
        container.innerHTML = `<div class="review-ops-empty">${escapeHtml(message)}</div>`;
        return;
    }

    const portfolios = new Map();
    filtered.forEach((review) => {
        const name = review.portfolio || 'Unmapped';
        if (!portfolios.has(name)) portfolios.set(name, []);
        portfolios.get(name).push(review);
    });

    const sortedPortfolios = [...portfolios.entries()].sort(([left], [right]) => {
        if (left === 'Unmapped') return 1;
        if (right === 'Unmapped') return -1;
        return left.localeCompare(right);
    });
    container.innerHTML = sortedPortfolios.map(([name, reviews]) => createPortfolioGroup(name, reviews)).join('');
}

function createPortfolioGroup(name, reviews) {
    const highRiskCount = reviews.filter((review) => (review.risk?.order ?? 2) <= 1).length;
    const sorted = [...reviews].sort((left, right) => {
        return (left.risk?.order ?? 2) - (right.risk?.order ?? 2)
            || left.days_remaining - right.days_remaining
            || (left.guest_name || '').localeCompare(right.guest_name || '');
    });
    const initials = name.split(/\s+/).slice(0, 2).map((word) => word[0]).join('').toUpperCase();
    return `
        <section class="review-portfolio-group" data-portfolio="${escapeHtml(name)}">
            <header class="review-portfolio-head">
                <div class="review-portfolio-title">
                    <span class="review-portfolio-monogram" aria-hidden="true">${escapeHtml(initials || '—')}</span>
                    <div>
                        <h3>${escapeHtml(name)}</h3>
                        <small>Sorted from highest concern to highest positive confidence</small>
                    </div>
                </div>
                <div class="review-portfolio-counts">
                    <span>${reviews.length} open</span>
                    ${highRiskCount ? `<span class="has-risk">${highRiskCount} priority watch</span>` : ''}
                </div>
            </header>
            <div class="review-portfolio-cards">
                ${sorted.map(createQueueCard).join('')}
            </div>
        </section>
    `;
}

function createQueueCard(review) {
    const risk = review.risk || { key: 'mixed', short_label: 'Unclear', confidence: 'low', reasons: [] };
    const guestComplete = Boolean(review.guest_reviewed);
    const hostComplete = Boolean(review.host_reviewed);
    const guestStatus = guestComplete
        ? (review.guest_review_rating != null ? `${Number(review.guest_review_rating).toFixed(1)} stars submitted` : 'Review submitted')
        : 'Waiting for guest';
    const hostStatus = hostComplete ? 'Host reviewed' : 'Not yet posted';
    const hostButton = hostComplete
        ? '<button type="button" class="review-host-button is-reviewed" disabled>✓ Host reviewed</button>'
        : `<button type="button" class="review-host-button" data-action="host-reviewed" data-reservation-id="${review.reservation_id}">Mark host reviewed</button>`;
    const riskReason = risk.reasons?.[0] || 'No strong sentiment signals in recent guest messages';
    const guestInitials = initialsFor(review.guest_name);

    return `
        <article class="review-queue-card review-risk-${escapeHtml(risk.key)}" data-risk="${escapeHtml(risk.key)}">
            <div class="review-queue-card-top">
                <span class="review-risk-badge">${escapeHtml(risk.short_label)}</span>
                <span class="review-confidence">${escapeHtml(risk.confidence)} signal confidence</span>
            </div>
            <div class="review-queue-identity">
                <span class="review-guest-avatar" aria-hidden="true">${escapeHtml(guestInitials)}</span>
                <div>
                    <h4>${escapeHtml(review.guest_name || 'Guest')}</h4>
                    <p>${escapeHtml(review.listing_name || 'Unknown property')}</p>
                </div>
            </div>
            <div class="review-queue-timing">
                <div>
                    <span>Checked out</span>
                    <strong>${formatDate(review.departure_date, { month: 'short', day: 'numeric' })}</strong>
                </div>
                <div class="review-deadline">
                    <span>Review window</span>
                    <strong>${review.days_remaining} day${review.days_remaining === 1 ? '' : 's'} left</strong>
                </div>
            </div>
            <p class="review-risk-reason">${escapeHtml(riskReason)}</p>
            <div class="review-status-stack">
                <div class="review-status-row ${guestComplete ? 'is-complete' : ''}">
                    <span class="review-status-label"><span class="review-status-icon">${guestComplete ? '✓' : '1'}</span> Guest side</span>
                    <span class="review-status-value">${escapeHtml(guestStatus)}</span>
                </div>
                <div class="review-status-row ${hostComplete ? 'is-complete' : ''}">
                    <span class="review-status-label"><span class="review-status-icon">${hostComplete ? '✓' : '2'}</span> Host side</span>
                    <span class="review-status-value">${escapeHtml(hostStatus)}</span>
                </div>
            </div>
            <div class="review-queue-actions">
                <span class="review-channel-pill">${escapeHtml(review.channel_name || 'Direct')}</span>
                ${hostButton}
            </div>
        </article>
    `;
}

async function handleQueueClick(event) {
    const button = event.target.closest('[data-action="host-reviewed"]');
    if (!button) return;
    const reservationId = Number(button.dataset.reservationId);
    if (!reservationId) return;

    button.disabled = true;
    button.textContent = 'Saving…';
    try {
        const result = await fetchJson(`/reviews/api/queue/${reservationId}/host-reviewed`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (result.outcome === 'closed') {
            showToast('Both sides are complete with a 5-star review. The card is closed.');
        } else if (result.outcome === 'resolution') {
            showToast('Both sides are complete. The lower rating moved to Review resolutions.');
        } else {
            showToast('Host review marked complete.');
        }
        await loadReviewQueue();
    } catch (error) {
        button.disabled = false;
        button.textContent = 'Mark host reviewed';
        showToast(error.message, true);
    }
}

async function loadTags() {
    const tagSelect = document.getElementById('filterTags');
    if (!tagSelect) return;
    try {
        const data = await fetchJson('/api/tags');
        allTags = Array.isArray(data) ? data : (data.tags || []);
        tagSelect.innerHTML = allTags.map((tag) => (
            `<option value="${Number(tag.tag_id)}">${escapeHtml(tag.name)}</option>`
        )).join('');
    } catch (error) {
        console.error('Error loading tags:', error);
    }
}

async function loadFilters() {
    const container = document.getElementById('filtersContainer');
    if (!container) return;
    container.innerHTML = '<div class="loading-spinner">Loading filters...</div>';
    try {
        const data = await fetchJson('/reviews/api/filters');
        if (!data.filters?.length) {
            container.innerHTML = '<div class="empty-state">No saved filters yet.</div>';
            return;
        }
        container.innerHTML = data.filters.map(createFilterSection).join('');
        data.filters.forEach((filter) => loadFilterReviews(filter.filter_id));
    } catch (error) {
        container.innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
}

function createFilterSection(filter) {
    const criteria = [];
    if (filter.tag_ids?.length) {
        const tagNames = filter.tag_ids.map((tagId) => {
            const tag = allTags.find((item) => Number(item.tag_id) === Number(tagId));
            return tag ? tag.name : `Tag ${tagId}`;
        });
        criteria.push(`Tags: ${tagNames.join(', ')}`);
    }
    if (filter.max_rating != null) criteria.push(`Rating ≤ ${filter.max_rating} stars`);
    if (filter.months_back != null) criteria.push(`Last ${filter.months_back} month${filter.months_back === 1 ? '' : 's'}`);
    return `
        <section class="filter-section" data-filter-id="${filter.filter_id}">
            <div class="filter-section-header">
                <div class="filter-section-title">
                    <h4>${escapeHtml(filter.name || `Filter #${filter.filter_id}`)}</h4>
                    <p class="filter-criteria">${escapeHtml(criteria.join(' · ') || 'No criteria set')}</p>
                </div>
                <div class="filter-section-actions">
                    <select class="sort-select" aria-label="Sort reviews" onchange="handleSortChange(${filter.filter_id}, this.value)">
                        <option value="date_desc">Most recent</option>
                        <option value="date_asc">Oldest first</option>
                        <option value="rating_asc">Lowest rating</option>
                        <option value="rating_desc">Highest rating</option>
                    </select>
                    <button type="button" class="btn-secondary btn-sm" onclick="editFilter(${filter.filter_id})">Edit</button>
                    <button type="button" class="btn-danger btn-sm" onclick="deleteFilter(${filter.filter_id})">Delete</button>
                </div>
            </div>
            <div class="filter-reviews-container" id="filterReviews_${filter.filter_id}">
                <div class="loading-spinner">Loading reviews...</div>
            </div>
        </section>
    `;
}

async function loadFilterReviews(filterId, sortBy = 'date_desc') {
    const container = document.getElementById(`filterReviews_${filterId}`);
    if (!container) return;
    const sortMap = {
        date_desc: ['review_date', 'desc'],
        date_asc: ['review_date', 'asc'],
        rating_asc: ['overall_rating', 'asc'],
        rating_desc: ['overall_rating', 'desc'],
    };
    const [sortField, sortOrder] = sortMap[sortBy] || sortMap.date_desc;
    try {
        const data = await fetchJson(`/reviews/api/filters/${filterId}/reviews?sort_by=${sortField}&sort_order=${sortOrder}`);
        container.innerHTML = data.reviews?.length
            ? data.reviews.map(createPublishedReviewCard).join('')
            : '<div class="empty-state">No reviews match this filter.</div>';
    } catch (error) {
        container.innerHTML = `<div class="error-state">${escapeHtml(error.message)}</div>`;
    }
}

function createPublishedReviewCard(review) {
    const rating = review.normalized_rating != null ? Number(review.normalized_rating) : null;
    const stars = rating == null ? '☆☆☆☆☆' : `${'★'.repeat(Math.max(0, Math.min(5, Math.floor(rating))))}${'☆'.repeat(Math.max(0, 5 - Math.floor(rating)))}`;
    const tags = (review.tags || []).map((tag) => `<span class="tag-chip">${escapeHtml(tag.name)}</span>`).join('');
    return `
        <article class="review-card">
            <div class="review-card-header">
                <div class="review-card-title">
                    <h4>${escapeHtml(review.listing_name || 'Unknown property')}</h4>
                    <span class="review-rating">${stars} ${rating == null ? 'N/A' : rating.toFixed(1)}</span>
                </div>
                <div class="review-card-meta">
                    <span>${escapeHtml(review.reviewer_name || 'Guest')} · ${formatDate(review.review_date)}</span>
                </div>
            </div>
            <div class="review-card-body">
                <p class="review-text">${escapeHtml(review.review_text || 'No review text')}</p>
                ${tags ? `<div class="review-tags">${tags}</div>` : ''}
            </div>
        </article>
    `;
}

function handleSortChange(filterId, value) {
    loadFilterReviews(filterId, value);
}

function openFilterModal(filterId = null) {
    currentEditingFilterId = filterId;
    const modal = document.getElementById('filterModal');
    const form = document.getElementById('filterForm');
    if (!modal || !form) return;
    form.reset();
    document.getElementById('filterId').value = '';
    document.getElementById('filterModalTitle').textContent = filterId ? 'Edit review filter' : 'Create review filter';
    modal.style.display = 'block';
    if (filterId) loadFilterForEdit(filterId);
}

async function loadFilterForEdit(filterId) {
    try {
        const data = await fetchJson('/reviews/api/filters');
        const filter = data.filters?.find((item) => Number(item.filter_id) === Number(filterId));
        if (!filter) return;
        document.getElementById('filterId').value = filter.filter_id;
        document.getElementById('filterName').value = filter.name || '';
        document.getElementById('filterMaxRating').value = filter.max_rating ?? 4;
        document.getElementById('filterMonthsBack').value = filter.months_back ?? 2;
        const selected = new Set((filter.tag_ids || []).map(Number));
        [...document.getElementById('filterTags').options].forEach((option) => {
            option.selected = selected.has(Number(option.value));
        });
    } catch (error) {
        showToast(error.message, true);
    }
}

function editFilter(filterId) {
    openFilterModal(filterId);
}

async function deleteFilter(filterId) {
    if (!window.confirm('Delete this saved review filter?')) return;
    try {
        await fetchJson(`/reviews/api/filters/${filterId}`, { method: 'DELETE' });
        loadFilters();
    } catch (error) {
        showToast(error.message, true);
    }
}

async function handleFilterSubmit(event) {
    event.preventDefault();
    const filterId = document.getElementById('filterId').value;
    const payload = {
        name: document.getElementById('filterName').value.trim() || null,
        tag_ids: [...document.getElementById('filterTags').selectedOptions].map((option) => Number(option.value)),
        max_rating: Number(document.getElementById('filterMaxRating').value) || null,
        months_back: Number(document.getElementById('filterMonthsBack').value) || null,
    };
    try {
        await fetchJson(filterId ? `/reviews/api/filters/${filterId}` : '/reviews/api/filters', {
            method: filterId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        closeFilterModal();
        loadFilters();
    } catch (error) {
        showToast(error.message, true);
    }
}

function closeFilterModal() {
    const modal = document.getElementById('filterModal');
    if (modal) modal.style.display = 'none';
    currentEditingFilterId = null;
}

function showToast(message, isError = false) {
    const toast = document.getElementById('reviewToast');
    if (!toast) return;
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.classList.add('is-visible');
    toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 4200);
}

function loadingState(message) {
    return `<div class="review-ops-loading"><span class="review-ops-spinner" aria-hidden="true"></span><span>${escapeHtml(message)}</span></div>`;
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
}

function initialsFor(name) {
    return String(name || 'Guest').trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join('').toUpperCase() || 'G';
}

function formatDate(value, options = { month: 'short', day: 'numeric', year: 'numeric' }) {
    if (!value) return 'N/A';
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    return Number.isNaN(parsed.getTime()) ? 'N/A' : parsed.toLocaleDateString('en-US', options);
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

window.addEventListener('click', (event) => {
    const modal = document.getElementById('filterModal');
    if (modal && event.target === modal) closeFilterModal();
});
