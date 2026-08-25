const reviewQueueState = {
    reviews: [],
    search: '',
    risk: 'all',
    portfolio: 'all',
    channel: 'all',
    summaryFilter: 'all',
};

const reviewSummaryHeadings = {
    all: 'Open review windows',
    needs_host_review: 'Needs host review',
    guest_submitted: 'Guest submitted',
    priority_watch: 'Priority watch',
};

const reviewRiskFilterOptions = [
    { value: 'bad_high', label: 'High bad-review risk' },
    { value: 'bad_elevated', label: 'Elevated bad-review risk' },
    { value: 'mixed', label: 'Unclear outcome' },
    { value: 'good_likely', label: 'Likely good' },
    { value: 'good_high', label: 'High chance of good' },
];

let allTags = [];
let currentEditingFilterId = null;
let toastTimer = null;
let activeReviewAction = null;
let reviewTemplateProperties = [];

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
        document.getElementById('reviewPortfolioFilter')?.addEventListener('change', (event) => {
            reviewQueueState.portfolio = event.target.value;
            populateQueueRiskFilter();
            renderReviewQueue();
        });
        document.getElementById('reviewChannelFilter')?.addEventListener('change', (event) => {
            reviewQueueState.channel = event.target.value;
            renderReviewQueue();
        });
        document.querySelectorAll('[data-queue-filter]').forEach((metric) => {
            metric.addEventListener('click', () => activateSummaryFilter(metric));
        });
        queueContainer.addEventListener('click', handleQueueClick);
        document.getElementById('reviewActionContent')?.addEventListener('input', updateReviewActionCharacterCount);
        document.getElementById('reviewActionSubmit')?.addEventListener('click', submitReviewAction);
        document.querySelectorAll('[data-close-review-action]').forEach((element) => {
            element.addEventListener('click', closeReviewActionModal);
        });
        document.getElementById('reviewTemplatesBtn')?.addEventListener('click', openReviewTemplatesModal);
        document.getElementById('reviewTemplateProperty')?.addEventListener('change', renderSelectedReviewTemplate);
        document.getElementById('reviewTemplatesForm')?.addEventListener('submit', saveReviewTemplates);
        document.querySelectorAll('[data-close-review-templates]').forEach((element) => {
            element.addEventListener('click', closeReviewTemplatesModal);
        });
        document.addEventListener('keydown', handleReviewModalKeydown);
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
        populateQueueDimensionFilters();
        updateQueueSummary(data.summary || {});
        updateWindowDescription(data.window || {});
        renderReviewQueue();
    } catch (error) {
        console.error('Error loading review queue:', error);
        container.innerHTML = `<div class="review-ops-error">${escapeHtml(error.message)}</div>`;
    }
}

function reviewChannelKey(channelName) {
    const compact = String(channelName || 'direct').trim().toLowerCase().replace(/[^a-z0-9]/g, '');
    if (compact === 'airbnb' || compact === 'airbnbofficial') return 'airbnb';
    if (compact === 'homeaway' || compact === 'vrbo') return 'vrbo';
    if (compact === 'bookingcom' || compact === 'bookingdotcom') return 'bookingcom';
    if (compact === 'bookingengine' || compact === 'direct') return 'direct';
    return compact || 'direct';
}

function reviewChannelLabel(channelName) {
    const key = reviewChannelKey(channelName);
    const knownLabels = {
        airbnb: 'Airbnb',
        vrbo: 'Vrbo',
        bookingcom: 'Booking.com',
        direct: 'Direct booking',
    };
    if (knownLabels[key]) return knownLabels[key];
    return String(channelName || 'Other')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, (character) => character.toUpperCase());
}

function populateQueueDimensionFilters() {
    const portfolioCounts = new Map();
    const channelCounts = new Map();
    reviewQueueState.reviews.forEach((review) => {
        const portfolio = review.portfolio || 'Unmapped';
        portfolioCounts.set(portfolio, (portfolioCounts.get(portfolio) || 0) + 1);

        const channel = reviewChannelKey(review.channel_name);
        const existing = channelCounts.get(channel) || {
            label: reviewChannelLabel(review.channel_name),
            count: 0,
        };
        existing.count += 1;
        channelCounts.set(channel, existing);
    });

    const portfolios = [...portfolioCounts.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([value, count]) => ({ value, label: `${value} (${count})` }));
    const channels = [...channelCounts.entries()]
        .sort(([, left], [, right]) => left.label.localeCompare(right.label))
        .map(([value, item]) => ({ value, label: `${item.label} (${item.count})` }));

    reviewQueueState.portfolio = populateQueueSelect(
        'reviewPortfolioFilter',
        'All portfolios',
        portfolios,
        reviewQueueState.portfolio,
    );
    reviewQueueState.channel = populateQueueSelect(
        'reviewChannelFilter',
        'All OTA channels',
        channels,
        reviewQueueState.channel,
    );
    populateQueueRiskFilter();
}

function populateQueueRiskFilter() {
    const portfolioReviews = reviewQueueState.portfolio === 'all'
        ? reviewQueueState.reviews
        : reviewQueueState.reviews.filter((review) => {
            return (review.portfolio || 'Unmapped') === reviewQueueState.portfolio;
        });
    const riskCounts = new Map(reviewRiskFilterOptions.map((option) => [option.value, 0]));
    portfolioReviews.forEach((review) => {
        const risk = review.risk?.key || 'mixed';
        if (riskCounts.has(risk)) riskCounts.set(risk, riskCounts.get(risk) + 1);
    });
    const risks = reviewRiskFilterOptions.map((option) => ({
        value: option.value,
        label: `${option.label} (${riskCounts.get(option.value) || 0})`,
    }));
    reviewQueueState.risk = populateQueueSelect(
        'reviewRiskFilter',
        `All severities (${portfolioReviews.length})`,
        risks,
        reviewQueueState.risk,
    );
}

function populateQueueSelect(selectId, allLabel, options, selectedValue) {
    const select = document.getElementById(selectId);
    if (!select) return 'all';
    const availableValues = new Set(options.map((option) => option.value));
    const nextValue = selectedValue === 'all' || availableValues.has(selectedValue) ? selectedValue : 'all';
    select.innerHTML = [
        `<option value="all">${escapeHtml(allLabel)}</option>`,
        ...options.map((option) => (
            `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`
        )),
    ].join('');
    select.value = nextValue;
    return nextValue;
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
        const matchesRisk = reviewQueueState.risk === 'all'
            || (review.risk?.key || 'mixed') === reviewQueueState.risk;
        const matchesPortfolio = reviewQueueState.portfolio === 'all'
            || (review.portfolio || 'Unmapped') === reviewQueueState.portfolio;
        const matchesChannel = reviewQueueState.channel === 'all'
            || reviewChannelKey(review.channel_name) === reviewQueueState.channel;
        return matchesSearch
            && matchesRisk
            && matchesPortfolio
            && matchesChannel
            && matchesSummaryFilter(review);
    });

    if (!filtered.length) {
        const message = reviewQueueState.search
            || reviewQueueState.risk !== 'all'
            || reviewQueueState.portfolio !== 'all'
            || reviewQueueState.channel !== 'all'
            || reviewQueueState.summaryFilter !== 'all'
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
        : 'Awaiting guest';
    const hostStatus = hostComplete ? 'Host reviewed' : 'Not posted';
    const chaseButton = review.show_chase_review_action
        ? (review.chase_review_sent
            ? '<button type="button" class="review-card-button review-card-button--success" disabled>✓ Review chased</button>'
            : (review.can_chase_review
                ? `<button type="button" class="review-card-button review-card-button--secondary" data-action="chase_review" data-reservation-id="${review.reservation_id}">Chase review</button>`
                : '<button type="button" class="review-card-button review-card-button--unavailable" aria-label="Chase review unavailable because no Hostaway conversation exists" disabled>Chase review</button>'))
        : '';
    const hostButton = hostComplete
        ? '<button type="button" class="review-card-button review-card-button--success" disabled>✓ Host reviewed</button>'
        : `<button type="button" class="review-card-button review-card-button--primary" data-action="host_review" data-reservation-id="${review.reservation_id}">Prepare host review</button>`;
    const manualButton = hostComplete
        ? ''
        : `<button type="button" class="review-manual-link" data-action="host-reviewed" data-reservation-id="${review.reservation_id}" aria-label="Mark the host review as already posted">Mark complete</button>`;
    const hostawayLink = review.hostaway_url
        ? `<a class="review-hostaway-link" href="${escapeHtml(review.hostaway_url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(review.guest_name || 'guest')} ${review.hostaway_destination === 'conversation' ? 'message thread' : 'booking'} in Hostaway">${review.hostaway_destination === 'conversation' ? 'Messages' : 'Booking'} <span aria-hidden="true">↗</span></a>`
        : '';
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
                <div class="review-status-row ${review.chase_review_sent ? 'is-complete' : ''}">
                    <span class="review-status-label"><span class="review-status-icon">${review.chase_review_sent ? '✓' : '1'}</span> Review chased</span>
                    <span class="review-status-value">${review.chase_review_sent ? 'Chased' : (guestComplete ? 'Not needed' : (review.has_message_conversation ? 'Not chased' : 'No conversation'))}</span>
                </div>
                <div class="review-status-row ${guestComplete ? 'is-complete' : ''}">
                    <span class="review-status-label"><span class="review-status-icon">${guestComplete ? '✓' : '2'}</span> Guest review</span>
                    <span class="review-status-value">${escapeHtml(guestStatus)}</span>
                </div>
                <div class="review-status-row ${hostComplete ? 'is-complete' : ''}">
                    <span class="review-status-label"><span class="review-status-icon">${hostComplete ? '✓' : '3'}</span> Host review</span>
                    <span class="review-status-value">${escapeHtml(hostStatus)}</span>
                </div>
            </div>
            <div class="review-queue-actions">
                <div class="review-action-buttons">
                    ${chaseButton}
                    ${hostButton}
                </div>
                <div class="review-action-meta">
                    <span class="review-channel-pill">${escapeHtml(reviewChannelLabel(review.channel_name))}</span>
                    <span class="review-action-links">
                        ${hostawayLink}
                        ${manualButton}
                    </span>
                </div>
            </div>
        </article>
    `;
}

async function handleQueueClick(event) {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const reservationId = Number(button.dataset.reservationId);
    if (!reservationId) return;

    if (button.dataset.action === 'chase_review' || button.dataset.action === 'host_review') {
        await openReviewActionModal(reservationId, button.dataset.action, button);
        return;
    }
    if (button.dataset.action !== 'host-reviewed') return;

    button.disabled = true;
    const originalText = button.textContent;
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
        button.textContent = originalText;
        showToast(error.message, true);
    }
}

async function openReviewActionModal(reservationId, actionType, triggerButton) {
    const modal = document.getElementById('reviewActionModal');
    const submit = document.getElementById('reviewActionSubmit');
    if (!modal || !submit) return;
    const originalText = triggerButton.textContent;
    triggerButton.disabled = true;
    triggerButton.textContent = 'Preparing…';
    modal.hidden = false;
    document.body.classList.add('review-modal-open');
    setReviewActionLoading(true);
    try {
        const preview = await fetchJson(`/reviews/api/queue/${reservationId}/automation-preview?action=${encodeURIComponent(actionType)}`);
        activeReviewAction = preview;
        const isChase = actionType === 'chase_review';
        setText('reviewActionEyebrow', isChase ? 'Guest outreach' : 'Host review');
        setText('reviewActionTitle', isChase ? 'Chase a great review' : 'Prepare host review');
        setText('reviewActionContext', `${preview.guest_name} · ${preview.listing_name} · ${preview.channel_name}`);
        setText('reviewActionEditorLabel', isChase ? 'Message to guest' : 'Public review of guest');
        const content = document.getElementById('reviewActionContent');
        content.value = preview.content || '';
        content.disabled = false;
        const safety = document.getElementById('reviewActionSafety');
        const isAssistedHostReview = !isChase && preview.assisted_host_review;
        safety.className = `review-action-safety ${isAssistedHostReview ? 'is-assisted' : (preview.simulated ? 'is-simulation' : (preview.execution_enabled ? 'is-live' : 'is-locked'))}`;
        safety.innerHTML = `<strong>${isAssistedHostReview ? 'Human-controlled review' : (preview.simulated ? 'Safe simulation' : (preview.execution_enabled ? 'Live Hostaway action' : 'Publishing locked'))}</strong><span>${escapeHtml(preview.capability_note)}</span>`;
        submit.disabled = isAssistedHostReview ? false : !preview.execution_enabled;
        submit.textContent = isAssistedHostReview
            ? 'Copy review'
            : (preview.simulated ? 'Simulate message' : 'Send via Hostaway');
        renderReviewPlatformDestination(isAssistedHostReview ? preview.review_destination : null);
        updateReviewActionCharacterCount();
        content.focus();
    } catch (error) {
        closeReviewActionModal();
        showToast(error.message, true);
    } finally {
        triggerButton.disabled = false;
        triggerButton.textContent = originalText;
    }
}

function renderReviewPlatformDestination(destination) {
    const container = document.getElementById('reviewPlatformDestination');
    const link = document.getElementById('reviewPlatformLink');
    if (!container || !link) return;
    if (!destination) {
        container.hidden = true;
        return;
    }

    container.hidden = false;
    container.classList.toggle('is-unavailable', !destination.supported);
    setText('reviewPlatformName', destination.platform || 'Booking platform');
    setText(
        'reviewPlatformHeading',
        destination.supported ? 'Finish the review on the booking platform' : 'Host review unavailable',
    );
    setText('reviewPlatformNote', destination.note || '');
    if (destination.supported && destination.url) {
        link.hidden = false;
        link.href = destination.url;
        link.textContent = `${destination.label || `Open ${destination.platform}`} ↗`;
        link.setAttribute('aria-label', `${destination.label || `Open ${destination.platform}`} in a new tab`);
    } else {
        link.hidden = true;
        link.removeAttribute('href');
    }
}

function setReviewActionLoading(isLoading) {
    const content = document.getElementById('reviewActionContent');
    const submit = document.getElementById('reviewActionSubmit');
    if (content) {
        content.value = isLoading ? 'Preparing the property template…' : content.value;
        content.disabled = isLoading;
    }
    if (submit) submit.disabled = true;
    setText('reviewActionSafety', isLoading ? 'Checking the review window and delivery channel…' : '');
}

function updateReviewActionCharacterCount() {
    const content = document.getElementById('reviewActionContent');
    setText('reviewActionCharacterCount', content?.value.length || 0);
}

async function submitReviewAction() {
    if (!activeReviewAction) return;
    const submit = document.getElementById('reviewActionSubmit');
    const content = document.getElementById('reviewActionContent')?.value.trim();
    if (!content || content.length < 20) {
        showToast('Please enter at least 20 characters.', true);
        return;
    }
    if (activeReviewAction.assisted_host_review) {
        try {
            await copyReviewText(content);
            showToast(
                activeReviewAction.review_destination?.supported
                    ? `Review copied. Open ${activeReviewAction.review_destination.platform} to post it.`
                    : 'Review copied. This channel does not support a host review.',
            );
        } catch (error) {
            showToast('Could not copy the review. Select the text and copy it manually.', true);
        }
        return;
    }
    submit.disabled = true;
    const originalText = submit.textContent;
    submit.textContent = activeReviewAction.simulated ? 'Running simulation…' : 'Sending…';
    try {
        const result = await fetchJson(`/reviews/api/queue/${activeReviewAction.reservation_id}/automation`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action_type: activeReviewAction.action_type,
                content,
            }),
        });
        closeReviewActionModal();
        showToast(result.message || 'Review action completed.');
        if (!result.simulated) await loadReviewQueue();
    } catch (error) {
        submit.disabled = false;
        submit.textContent = originalText;
        showToast(error.message, true);
    }
}

async function copyReviewText(content) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
        return;
    }
    const editor = document.getElementById('reviewActionContent');
    editor.focus();
    editor.select();
    if (!document.execCommand('copy')) throw new Error('Copy failed');
}

function closeReviewActionModal() {
    const modal = document.getElementById('reviewActionModal');
    if (modal) modal.hidden = true;
    renderReviewPlatformDestination(null);
    activeReviewAction = null;
    if (document.getElementById('reviewTemplatesModal')?.hidden !== false) {
        document.body.classList.remove('review-modal-open');
    }
}

async function openReviewTemplatesModal() {
    const modal = document.getElementById('reviewTemplatesModal');
    const select = document.getElementById('reviewTemplateProperty');
    if (!modal || !select) return;
    modal.hidden = false;
    document.body.classList.add('review-modal-open');
    select.disabled = true;
    select.innerHTML = '<option>Loading active properties…</option>';
    setText('reviewTemplateStatus', 'Loading templates…');
    try {
        const data = await fetchJson('/reviews/api/templates');
        reviewTemplateProperties = data.properties || [];
        select.innerHTML = reviewTemplateProperties.map((property) => (
            `<option value="${property.listing_id}">${escapeHtml(property.listing_name)}</option>`
        )).join('');
        select.disabled = false;
        renderSelectedReviewTemplate();
        select.focus();
    } catch (error) {
        closeReviewTemplatesModal();
        showToast(error.message, true);
    }
}

function renderSelectedReviewTemplate() {
    const listingId = Number(document.getElementById('reviewTemplateProperty')?.value);
    const property = reviewTemplateProperties.find((item) => Number(item.listing_id) === listingId);
    if (!property) return;
    document.getElementById('reviewTemplateChase').value = property.chase_message_template || '';
    document.getElementById('reviewTemplateHost').value = property.host_review_template || '';
    setText('reviewTemplatePortfolio', `${property.portfolio} portfolio · ${property.is_custom ? 'Custom property templates' : 'Using starter templates'}`);
    setText('reviewTemplateStatus', '');
}

async function saveReviewTemplates(event) {
    event.preventDefault();
    const listingId = Number(document.getElementById('reviewTemplateProperty')?.value);
    const button = document.getElementById('reviewTemplatesSave');
    if (!listingId || !button) return;
    button.disabled = true;
    button.textContent = 'Saving…';
    setText('reviewTemplateStatus', '');
    try {
        const saved = await fetchJson(`/reviews/api/templates/${listingId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chase_message_template: document.getElementById('reviewTemplateChase').value,
                host_review_template: document.getElementById('reviewTemplateHost').value,
            }),
        });
        const index = reviewTemplateProperties.findIndex((property) => Number(property.listing_id) === listingId);
        if (index >= 0) reviewTemplateProperties[index] = { ...reviewTemplateProperties[index], ...saved };
        setText('reviewTemplateStatus', 'Saved for this property');
        showToast('Property review templates saved.');
    } catch (error) {
        setText('reviewTemplateStatus', error.message);
        showToast(error.message, true);
    } finally {
        button.disabled = false;
        button.textContent = 'Save property templates';
    }
}

function closeReviewTemplatesModal() {
    const modal = document.getElementById('reviewTemplatesModal');
    if (modal) modal.hidden = true;
    if (document.getElementById('reviewActionModal')?.hidden !== false) {
        document.body.classList.remove('review-modal-open');
    }
}

function handleReviewModalKeydown(event) {
    if (event.key !== 'Escape') return;
    if (document.getElementById('reviewActionModal')?.hidden === false) closeReviewActionModal();
    if (document.getElementById('reviewTemplatesModal')?.hidden === false) closeReviewTemplatesModal();
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
