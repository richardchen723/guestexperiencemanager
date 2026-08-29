const publishedReviewState = {
    loading: false,
    data: null,
    pendingPortfolio: '',
    detailReturnFocus: null,
};

let publishedToastTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    hydratePublishedFiltersFromUrl();
    document.getElementById('publishedReviewFilters')?.addEventListener('submit', (event) => {
        event.preventDefault();
        loadPublishedReviews();
    });
    document.getElementById('publishedResetFilters')?.addEventListener('click', resetPublishedFilters);
    document.getElementById('publishedRatingAll')?.addEventListener('click', () => {
        document.querySelectorAll('input[name="ratings"]').forEach((input) => {
            input.checked = false;
        });
        updatePublishedRatingAllState();
    });
    document.querySelectorAll('input[name="ratings"]').forEach((input) => {
        input.addEventListener('change', updatePublishedRatingAllState);
    });
    document.getElementById('publishedStartDate')?.addEventListener('input', clearPublishedFilterError);
    document.getElementById('publishedEndDate')?.addEventListener('input', clearPublishedFilterError);
    document.getElementById('publishedReviews')?.addEventListener('click', handlePublishedReviewClick);
    document.getElementById('publishedReviewDetail')?.addEventListener('click', handlePublishedReviewDetailClick);
    document.addEventListener('keydown', handlePublishedReviewDetailKeydown);
    updatePublishedRatingAllState();
    loadPublishedReviews();
});

function hydratePublishedFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search);
    document.getElementById('publishedStartDate').value = params.get('start_date') || '';
    document.getElementById('publishedEndDate').value = params.get('end_date') || '';
    document.getElementById('publishedSort').value = params.get('sort') || 'newest';
    publishedReviewState.pendingPortfolio = params.get('portfolio') || '';
    const ratings = new Set((params.get('ratings') || '').split(',').filter(Boolean));
    document.querySelectorAll('input[name="ratings"]').forEach((input) => {
        input.checked = ratings.has(input.value);
    });
}

function publishedFilterValues() {
    return {
        startDate: document.getElementById('publishedStartDate')?.value || '',
        endDate: document.getElementById('publishedEndDate')?.value || '',
        portfolio: document.getElementById('publishedPortfolio')?.value || publishedReviewState.pendingPortfolio || '',
        ratings: [...document.querySelectorAll('input[name="ratings"]:checked')].map((input) => Number(input.value)),
        sort: document.getElementById('publishedSort')?.value || 'newest',
    };
}

function publishedDateRangeError(startDate, endDate) {
    if (!startDate && !endDate) return '';
    if (!startDate || !endDate) return 'Choose both a From date and a To date.';
    if (endDate < startDate) return 'To date cannot be earlier than From date.';
    return '';
}

async function loadPublishedReviews() {
    if (publishedReviewState.loading) return;
    const filters = publishedFilterValues();
    const validationError = publishedDateRangeError(filters.startDate, filters.endDate);
    if (validationError) {
        setPublishedFilterError(validationError);
        return;
    }

    const params = new URLSearchParams();
    if (filters.startDate && filters.endDate) {
        params.set('start_date', filters.startDate);
        params.set('end_date', filters.endDate);
    }
    if (filters.portfolio) params.set('portfolio', filters.portfolio);
    if (filters.ratings.length) params.set('ratings', filters.ratings.join(','));
    if (filters.sort !== 'newest') params.set('sort', filters.sort);

    setPublishedLoading(true);
    clearPublishedFilterError();
    try {
        const query = params.toString();
        const data = await publishedFetch(`/reviews/api/published${query ? `?${query}` : ''}`);
        publishedReviewState.data = data;
        publishedReviewState.pendingPortfolio = '';
        syncPublishedFilterControls(data);
        renderPublishedSummary(data);
        renderPublishedRatingCounts(data.filter_options || {});
        renderPublishedActiveFilters(data);
        renderPublishedReviews(data.reviews || []);
        updatePublishedUrl(data);
    } catch (error) {
        setPublishedFilterError(error.message);
        renderPublishedLoadError(error.message);
        showPublishedToast(error.message, true);
    } finally {
        setPublishedLoading(false);
    }
}

async function publishedFetch(url) {
    const response = await fetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
}

function syncPublishedFilterControls(data) {
    const range = data.range || {};
    const filters = data.filters || {};
    const options = data.filter_options || {};
    const startInput = document.getElementById('publishedStartDate');
    const endInput = document.getElementById('publishedEndDate');
    if (startInput && range.start_date) startInput.value = range.start_date;
    if (endInput && range.end_date) endInput.value = range.end_date;

    const portfolioSelect = document.getElementById('publishedPortfolio');
    if (portfolioSelect) {
        const selected = filters.portfolio || '';
        const portfolioOptions = [...(options.portfolios || [])];
        if (selected && !portfolioOptions.some((option) => option.portfolio === selected)) {
            portfolioOptions.push({ portfolio: selected, display_name: selected, count: 0 });
        }
        portfolioSelect.innerHTML = [
            `<option value="">All portfolios (${Number(options.range_total || 0).toLocaleString()})</option>`,
            ...portfolioOptions.map((option) => (
                `<option value="${publishedEscape(option.portfolio)}">${publishedEscape(option.display_name || option.portfolio)} (${Number(option.count || 0).toLocaleString()})</option>`
            )),
        ].join('');
        portfolioSelect.value = selected;
    }
    document.getElementById('publishedSort').value = filters.sort || 'newest';
}

function renderPublishedSummary(data) {
    const summary = data.summary || {};
    const range = data.range || {};
    const total = Number(summary.total || 0);
    const average = summary.average_rating == null ? '—' : Number(summary.average_rating).toFixed(2).replace(/0$/, '');
    const rangeLabel = `${publishedFormatDate(range.start_date)} – ${publishedFormatDate(range.end_date)}`;
    const fiveStarCount = Number(summary.five_star_count || 0);
    const fiveStarPercent = total ? Math.round((fiveStarCount / total) * 100) : 0;

    publishedSetText('publishedHeroTotal', total.toLocaleString());
    publishedSetText('publishedHeroAverage', average);
    publishedSetText('publishedHeroRange', rangeLabel);
    publishedSetText('publishedMetricTotal', total.toLocaleString());
    publishedSetText('publishedMetricAverage', average);
    publishedSetText('publishedMetricFiveStar', fiveStarCount.toLocaleString());
    publishedSetText('publishedMetricPortfolios', Number(summary.portfolio_count || 0).toLocaleString());
    publishedSetText('publishedMetricTotalCaption', `${range.is_custom ? 'Custom' : 'Default'} inclusive date range`);
    publishedSetText('publishedMetricFiveStarCaption', `${fiveStarPercent}% of filtered reviews`);
    publishedSetText('publishedRangeBadge', range.is_custom ? `Custom · ${rangeLabel}` : `Default ${range.default_days || 90}-day view`);
}

function renderPublishedRatingCounts(filterOptions) {
    publishedSetText('publishedRatingAllCount', Number(filterOptions.range_total || 0).toLocaleString());
    (filterOptions.ratings || []).forEach((option) => {
        publishedSetText(`publishedRating${option.rating}Count`, Number(option.count || 0).toLocaleString());
    });
}

function renderPublishedActiveFilters(data) {
    const container = document.getElementById('publishedActiveFilters');
    if (!container) return;
    const filters = data.filters || {};
    const range = data.range || {};
    const sortLabels = {
        oldest: 'Oldest first',
        rating_desc: 'Highest rating',
        rating_asc: 'Lowest rating',
    };
    const chips = [
        `${publishedFormatDate(range.start_date)} – ${publishedFormatDate(range.end_date)}`,
        filters.portfolio || 'All portfolios',
        filters.ratings?.length ? `${filters.ratings.slice().sort((a, b) => b - a).join(', ')} star bands` : 'All ratings',
    ];
    if (sortLabels[filters.sort]) chips.push(sortLabels[filters.sort]);
    container.innerHTML = chips.map((chip) => `<span class="published-filter-chip">${publishedEscape(chip)}</span>`).join('');
}

function renderPublishedReviews(reviews) {
    const container = document.getElementById('publishedReviews');
    if (!container) return;
    closePublishedReviewDetail(false);
    const count = reviews.length;
    const sortLabel = {
        newest: 'newest published first',
        oldest: 'oldest published first',
        rating_desc: 'highest rating first',
        rating_asc: 'lowest rating first',
    }[publishedReviewState.data?.filters?.sort || 'newest'];
    publishedSetText(
        'publishedResultsCaption',
        `${count.toLocaleString()} ${count === 1 ? 'review' : 'reviews'} · ${sortLabel}`,
    );

    if (!count) {
        container.innerHTML = `
            <div class="published-empty-state">
                <div>
                    <span class="published-empty-state__icon" aria-hidden="true">☆</span>
                    <h3>No published reviews match</h3>
                    <p>Try a wider date range, another portfolio, or clear the selected star ratings.</p>
                </div>
            </div>
        `;
        return;
    }

    container.innerHTML = reviews.map(createPublishedReportingCard).join('');
}

function createPublishedReportingCard(review) {
    const rating = review.rating == null ? null : Number(review.rating);
    const ratingBucket = Number(review.rating_bucket || 0);
    const stars = ratingBucket
        ? `${'★'.repeat(ratingBucket)}${'☆'.repeat(5 - ratingBucket)}`
        : '☆☆☆☆☆';
    const ratingLabel = rating == null ? 'N/A' : rating.toFixed(1);
    const reviewText = review.review_text || 'No written feedback was included with this review.';
    const portfolio = review.portfolio_display_name || review.portfolio || 'Unmapped';
    return `
        <article class="published-review-card" data-review-id="${Number(review.review_id)}" data-rating="${ratingBucket || 0}">
            <div class="published-review-card__top">
                <div class="published-review-score" title="${publishedEscape(rating == null ? 'Rating unavailable' : `${ratingLabel} out of 5 stars`)}">
                    <strong>${publishedEscape(ratingLabel)}</strong>
                    <span class="published-review-stars" aria-hidden="true">${stars}</span>
                </div>
                <time class="published-review-date" datetime="${publishedEscape(review.publication_date || '')}">Published ${publishedFormatDate(review.publication_date)}</time>
            </div>
            <h3 class="published-review-property">${publishedEscape(review.listing_name || 'Unknown property')}</h3>
            <p class="published-review-context">${publishedEscape(portfolio)}</p>
            <div class="published-review-quote">
                <p class="published-review-text">${publishedEscape(reviewText)}</p>
            </div>
            <footer class="published-review-card__footer">
                <div class="published-review-guest">
                    <span class="published-review-avatar" aria-hidden="true">${publishedEscape(publishedInitials(review.guest_name))}</span>
                    <span>${publishedEscape(review.guest_name || 'Guest')}</span>
                </div>
                <span class="published-channel-badge">${publishedEscape(publishedChannelLabel(review.channel_name))}</span>
            </footer>
            <button
                class="published-review-card__open"
                type="button"
                data-action="open-review"
                data-review-id="${Number(review.review_id)}"
                aria-label="Open full review for ${publishedEscape(review.listing_name || 'unknown property')}"
            ></button>
        </article>
    `;
}

function handlePublishedReviewClick(event) {
    const button = event.target.closest('[data-action="open-review"]');
    if (!button) return;
    const review = (publishedReviewState.data?.reviews || []).find(
        (item) => String(item.review_id) === String(button.dataset.reviewId),
    );
    if (review) openPublishedReviewDetail(review, button);
}

function openPublishedReviewDetail(review, trigger) {
    const detail = document.getElementById('publishedReviewDetail');
    const body = document.getElementById('publishedReviewDetailBody');
    if (!detail || !body) return;

    const rating = review.rating == null ? null : Number(review.rating);
    const ratingBucket = Number(review.rating_bucket || 0);
    const ratingLabel = rating == null ? 'N/A' : rating.toFixed(1);
    const stars = ratingBucket
        ? `${'★'.repeat(ratingBucket)}${'☆'.repeat(5 - ratingBucket)}`
        : '☆☆☆☆☆';
    const property = review.listing_name || 'Unknown property';
    const portfolio = review.portfolio_display_name || review.portfolio || 'Unmapped';
    const reviewText = review.review_text || 'No written feedback was included with this review.';
    const stayDate = review.departure_date ? publishedFormatDate(review.departure_date) : 'Not available';

    publishedSetText('publishedReviewDetailTitle', property);
    publishedSetText('publishedReviewDetailSubtitle', `${portfolio} · Published ${publishedFormatDate(review.publication_date)}`);
    body.innerHTML = `
        <div class="published-review-detail__score-row">
            <div class="published-review-detail__score">
                <strong>${publishedEscape(ratingLabel)}</strong>
                <span aria-hidden="true">${stars}</span>
                <small>${rating == null ? 'Rating unavailable' : 'out of 5 stars'}</small>
            </div>
            <span class="published-channel-badge published-channel-badge--detail">${publishedEscape(publishedChannelLabel(review.channel_name))}</span>
        </div>
        <blockquote class="published-review-detail__quote">${publishedEscape(reviewText)}</blockquote>
        <dl class="published-review-detail__facts">
            ${publishedDetailFact('Guest', review.guest_name || 'Guest')}
            ${publishedDetailFact('Portfolio', portfolio)}
            ${publishedDetailFact('Property', property)}
            ${publishedDetailFact('Published', publishedFormatDate(review.publication_date))}
            ${publishedDetailFact('Stay departure', stayDate)}
            ${publishedDetailFact('Listing ID', review.listing_id || 'Not available')}
        </dl>
    `;

    publishedReviewState.detailReturnFocus = trigger || null;
    detail.hidden = false;
    document.body.classList.add('review-modal-open');
    window.requestAnimationFrame(() => detail.querySelector('.published-review-detail__close')?.focus());
}

function publishedDetailFact(label, value) {
    return `
        <div>
            <dt>${publishedEscape(label)}</dt>
            <dd>${publishedEscape(value)}</dd>
        </div>
    `;
}

function handlePublishedReviewDetailClick(event) {
    if (event.target.closest('[data-action="close-review-detail"]')) closePublishedReviewDetail();
}

function handlePublishedReviewDetailKeydown(event) {
    if (event.key !== 'Escape') return;
    const detail = document.getElementById('publishedReviewDetail');
    if (detail && !detail.hidden) closePublishedReviewDetail();
}

function closePublishedReviewDetail(restoreFocus = true) {
    const detail = document.getElementById('publishedReviewDetail');
    if (!detail || detail.hidden) return;
    detail.hidden = true;
    document.body.classList.remove('review-modal-open');
    if (restoreFocus) publishedReviewState.detailReturnFocus?.focus();
    publishedReviewState.detailReturnFocus = null;
}

function updatePublishedRatingAllState() {
    const hasSelectedRating = Boolean(document.querySelector('input[name="ratings"]:checked'));
    const button = document.getElementById('publishedRatingAll');
    button?.classList.toggle('is-active', !hasSelectedRating);
    button?.setAttribute('aria-pressed', String(!hasSelectedRating));
}

function resetPublishedFilters() {
    document.getElementById('publishedStartDate').value = '';
    document.getElementById('publishedEndDate').value = '';
    document.getElementById('publishedPortfolio').value = '';
    document.getElementById('publishedSort').value = 'newest';
    document.querySelectorAll('input[name="ratings"]').forEach((input) => {
        input.checked = false;
    });
    publishedReviewState.pendingPortfolio = '';
    updatePublishedRatingAllState();
    clearPublishedFilterError();
    loadPublishedReviews();
}

function updatePublishedUrl(data) {
    const params = new URLSearchParams();
    const range = data.range || {};
    const filters = data.filters || {};
    if (range.is_custom) {
        params.set('start_date', range.start_date);
        params.set('end_date', range.end_date);
    }
    if (filters.portfolio) params.set('portfolio', filters.portfolio);
    if (filters.ratings?.length) params.set('ratings', filters.ratings.join(','));
    if (filters.sort && filters.sort !== 'newest') params.set('sort', filters.sort);
    const query = params.toString();
    window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}`);
}

function setPublishedLoading(isLoading) {
    publishedReviewState.loading = isLoading;
    const container = document.getElementById('publishedReviews');
    const applyButton = document.getElementById('publishedApplyFilters');
    const resetButton = document.getElementById('publishedResetFilters');
    if (container) container.setAttribute('aria-busy', String(isLoading));
    if (applyButton) {
        applyButton.disabled = isLoading;
        applyButton.querySelector('span:first-child').textContent = isLoading ? 'Loading…' : 'View results';
    }
    if (resetButton) resetButton.disabled = isLoading;
}

function renderPublishedLoadError(message) {
    const container = document.getElementById('publishedReviews');
    if (!container) return;
    container.innerHTML = `
        <div class="published-empty-state">
            <div>
                <span class="published-empty-state__icon" aria-hidden="true">!</span>
                <h3>Could not load published reviews</h3>
                <p>${publishedEscape(message)}</p>
            </div>
        </div>
    `;
}

function setPublishedFilterError(message) {
    const element = document.getElementById('publishedFilterError');
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
}

function clearPublishedFilterError() {
    const element = document.getElementById('publishedFilterError');
    if (!element) return;
    element.textContent = '';
    element.hidden = true;
}

function showPublishedToast(message, isError = false) {
    const toast = document.getElementById('publishedReviewToast');
    if (!toast) return;
    window.clearTimeout(publishedToastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.classList.add('is-visible');
    publishedToastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 4200);
}

function publishedFormatDate(value) {
    if (!value) return 'Date unavailable';
    const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    return Number.isNaN(parsed.getTime())
        ? 'Date unavailable'
        : parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function publishedChannelLabel(channelName) {
    const rawName = String(channelName || 'Direct').trim();
    const compact = rawName.toLowerCase().replace(/[^a-z0-9]/g, '');
    const labels = {
        airbnb: 'Airbnb',
        airbnbofficial: 'Airbnb',
        homeaway: 'Vrbo',
        vrbo: 'Vrbo',
        bookingcom: 'Booking.com',
        bookingdotcom: 'Booking.com',
        bookingengine: 'Direct booking',
        direct: 'Direct booking',
    };
    return labels[compact] || rawName
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/[-_]+/g, ' ')
        .replace(/\b\w/g, (character) => character.toUpperCase());
}

function publishedInitials(name) {
    return String(name || 'Guest').trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || 'G';
}

function publishedSetText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
}

function publishedEscape(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}
