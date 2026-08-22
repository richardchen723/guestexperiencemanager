let tagFilter = null;
let selectedTagIds = [];
let tagLogic = 'AND';
let loadedListings = [];
let propertyCatalogNeedsRefresh = false;

const listingTagInputs = {};

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const listingsContainer = document.getElementById('listingsContainer');

    searchInput.addEventListener('input', filterListings);
    listingsContainer.addEventListener('click', event => {
        const editButton = event.target.closest('[data-action="edit-tags"]');
        if (editButton) {
            toggleTagEdit(Number(editButton.dataset.listingId));
        }
    });

    try {
        tagFilter = new TagFilter('#tagFilterContainer', {
            onFilterChange: (tagIds, logic) => {
                selectedTagIds = tagIds;
                tagLogic = logic;
                loadListings();
            }
        });
    } catch (error) {
        console.error('Error initializing tag filter:', error);
    }

    loadListings();
});

async function loadListings() {
    const container = document.getElementById('listingsContainer');
    const loading = document.getElementById('loading');
    const noResults = document.getElementById('noResults');
    loading.hidden = false;
    noResults.hidden = true;
    container.innerHTML = '';

    const params = new URLSearchParams();
    if (selectedTagIds.length && tagFilter?.allTags) {
        const selectedTags = tagFilter.allTags.filter(tag => selectedTagIds.includes(tag.tag_id));
        if (selectedTags.length) {
            params.set('tags', selectedTags.map(tag => tag.name).join(','));
            params.set('tag_logic', tagLogic);
        }
    }

    try {
        const response = await fetch(`/api/listings?${params.toString()}`);
        if (!response.ok) throw new Error(`Request failed (${response.status})`);
        loadedListings = await response.json();
        propertyCatalogNeedsRefresh = false;
        renderPortfolios(loadedListings);
        filterListings();
    } catch (error) {
        container.innerHTML = `<div class="property-error">Unable to load properties. ${escapeHtml(error.message)}</div>`;
        updatePropertyCount(0, 0, false);
    } finally {
        loading.hidden = true;
    }
}

function renderPortfolios(listings) {
    const container = document.getElementById('listingsContainer');
    Object.keys(listingTagInputs).forEach(listingId => delete listingTagInputs[listingId]);
    if (!listings.length) {
        container.innerHTML = '';
        document.getElementById('noResults').hidden = false;
        updatePropertyCount(0, 0, false);
        return;
    }

    const portfolios = new Map();
    listings.forEach(listing => {
        const portfolioName = listing.portfolio || 'Unassigned';
        if (!portfolios.has(portfolioName)) portfolios.set(portfolioName, []);
        portfolios.get(portfolioName).push(listing);
    });

    container.innerHTML = Array.from(portfolios.entries()).map(([portfolioName, properties], index) =>
        renderPortfolio(portfolioName, properties, index)
    ).join('');

    listings.forEach(listing => initializeListingTags(listing.listing_id, listing.tags || []));
    container.querySelectorAll('.property-card-image').forEach(image => {
        image.addEventListener('error', () => {
            image.hidden = true;
            image.nextElementSibling.hidden = false;
        }, { once: true });
    });
}

function renderPortfolio(portfolioName, properties, index) {
    const reviewCount = properties.reduce((sum, property) => sum + (Number(property.review_count) || 0), 0);
    const ratedReviewCount = properties.reduce((sum, property) => sum + (Number(property.rated_review_count) || 0), 0);
    const weightedRating = properties.reduce((sum, property) => {
        const count = Number(property.rated_review_count) || 0;
        const rating = Number(property.average_review_rating) || 0;
        return sum + (count * rating);
    }, 0);
    const averageRating = ratedReviewCount ? weightedRating / ratedReviewCount : null;
    const portfolioLabel = displayPortfolioName(portfolioName);
    const reviewSummary = averageRating === null
        ? 'No guest reviews yet'
        : `${averageRating.toFixed(2)} average · ${formatCount(reviewCount, 'review')}`;

    return `
        <details class="property-portfolio" data-portfolio="${escapeHtml(portfolioName.toLowerCase())}" open>
            <summary class="property-portfolio-summary">
                <span class="portfolio-mark portfolio-mark-${(index % 5) + 1}" aria-hidden="true">${escapeHtml(initials(portfolioLabel))}</span>
                <span class="portfolio-heading">
                    <span class="portfolio-eyebrow">Portfolio</span>
                    <span class="portfolio-name">${escapeHtml(portfolioLabel)}</span>
                </span>
                <span class="portfolio-rollup">
                    <span>${formatCount(properties.length, 'property', 'properties')}</span>
                    <span class="portfolio-rollup-separator" aria-hidden="true"></span>
                    <span class="portfolio-review-rollup">
                        <svg aria-hidden="true" viewBox="0 0 16 16"><path d="m8 1.4 1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.2l-3.8 2 .7-4.3-3.1-3 4.3-.6L8 1.4Z" fill="currentColor"/></svg>
                        ${escapeHtml(reviewSummary)}
                    </span>
                </span>
                <svg class="portfolio-chevron" aria-hidden="true" viewBox="0 0 20 20"><path d="m6 8 4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </summary>
            <div class="property-card-grid">
                ${properties.map(renderPropertyCard).join('')}
            </div>
        </details>`;
}

function renderPropertyCard(listing) {
    const name = listing.internal_listing_name || listing.name || `Property ${listing.listing_id}`;
    const location = [listing.city, listing.state].filter(Boolean).join(', ') || listing.address || 'Location unavailable';
    const searchText = [
        name,
        listing.name,
        listing.address,
        listing.city,
        listing.state,
        listing.portfolio,
        ...(listing.tags || []).map(tag => tag.name),
    ].filter(Boolean).join(' ').toLowerCase();
    const quality = ['Good', 'Fair', 'Poor'].includes(listing.quality_rating) ? listing.quality_rating : null;
    const facts = propertyFacts(listing);
    const image = listing.thumbnail_url
        ? `<img class="property-card-image" src="${escapeHtml(listing.thumbnail_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : '';

    return `
        <article class="property-card" data-name="${escapeHtml(name.toLowerCase())}" data-address="${escapeHtml((listing.address || '').toLowerCase())}" data-search="${escapeHtml(searchText)}">
            <div class="property-card-main">
                <div class="property-card-media">
                    ${image}
                    <span class="property-card-fallback" ${image ? 'hidden' : ''} aria-hidden="true">${escapeHtml(initials(name))}</span>
                </div>
                <div class="property-card-identity">
                    <div class="property-card-kicker">
                        <span class="property-status-dot ${isActiveStatus(listing.status) ? 'is-active' : ''}"></span>
                        <span>#${escapeHtml(String(listing.listing_id))}</span>
                        ${quality ? `<span class="property-quality property-quality-${quality.toLowerCase()}">${escapeHtml(quality)}</span>` : ''}
                    </div>
                    <h3 class="property-card-title" title="${escapeHtml(name)}">${escapeHtml(name)}</h3>
                    <p class="property-card-location" title="${escapeHtml(listing.address || location)}">
                        <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 14s4.2-4.7 4.2-8A4.2 4.2 0 1 0 3.8 6c0 3.3 4.2 8 4.2 8Z" fill="none" stroke="currentColor" stroke-width="1.35"/><circle cx="8" cy="6" r="1.4" fill="currentColor"/></svg>
                        ${escapeHtml(location)}
                    </p>
                </div>
            </div>

            <div class="property-review-row">
                ${renderReviewSummary(listing)}
            </div>

            <div class="property-facts" aria-label="Property capacity">
                ${facts.length ? facts.join('') : '<span class="property-fact property-fact-muted">Capacity not set</span>'}
            </div>

            <div class="property-tags-row">
                <div id="tagsDisplay-${listing.listing_id}" class="property-tags-display"></div>
                <div id="tagsInput-${listing.listing_id}" class="property-tags-input" hidden></div>
                <button type="button" class="property-tags-edit" data-action="edit-tags" data-listing-id="${listing.listing_id}" aria-label="Edit tags for ${escapeHtml(name)}">
                    <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M3 11.9V13h1.1l7.4-7.4-1.1-1.1L3 11.9Zm9.4-7.2.8-.8a.8.8 0 0 0 0-1.1.8.8 0 0 0-1.1 0l-.8.8 1.1 1.1Z" fill="currentColor"/></svg>
                    <span id="tagEditBtn-${listing.listing_id}">Edit</span>
                </button>
            </div>
        </article>`;
}

function renderReviewSummary(listing) {
    const count = Number(listing.review_count) || 0;
    const rating = listing.average_review_rating == null ? null : Number(listing.average_review_rating);
    if (!count || rating === null || Number.isNaN(rating)) {
        return `
            <span class="property-review-empty">
                <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M2.5 3.5h11v7h-7l-3 2v-2h-1v-7Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
                No guest reviews yet
            </span>`;
    }

    const latest = listing.latest_review_date ? formatReviewDate(listing.latest_review_date) : null;
    return `
        <span class="property-rating" aria-label="${rating.toFixed(2)} out of 5 stars">
            <svg aria-hidden="true" viewBox="0 0 16 16"><path d="m8 1.4 1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.2l-3.8 2 .7-4.3-3.1-3 4.3-.6L8 1.4Z" fill="currentColor"/></svg>
            <strong>${rating.toFixed(2)}</strong>
        </span>
        <span class="property-review-count">${formatCount(count, 'guest review')}</span>
        ${latest ? `<span class="property-review-latest">Latest ${escapeHtml(latest)}</span>` : ''}`;
}

function propertyFacts(listing) {
    const facts = [];
    if (listing.bedrooms != null) facts.push(factIcon('bedroom', `${formatNumber(listing.bedrooms)} bd`));
    if (listing.bathrooms != null) facts.push(factIcon('bathroom', `${formatNumber(listing.bathrooms)} ba`));
    if (listing.accommodates != null) facts.push(factIcon('guests', `Sleeps ${formatNumber(listing.accommodates)}`));
    if (listing.base_price != null) facts.push(`<span class="property-fact property-base-rate">${escapeHtml(formatMoney(listing.base_price, listing.currency))} base</span>`);
    return facts;
}

function factIcon(type, label) {
    const icons = {
        bedroom: '<path d="M2 11V7.5h12V11M3.5 7.5V5h4v2.5M1.5 11h13v2M3 13v1M13 13v1" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/>',
        bathroom: '<path d="M2 8.5h12v1a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3v-1ZM4 8.5V4.2A2.2 2.2 0 0 1 6.2 2c1 0 1.8.6 2.1 1.4M3.5 12.5V14M12.5 12.5V14" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/>',
        guests: '<circle cx="6" cy="5" r="2.1" fill="none" stroke="currentColor" stroke-width="1.25"/><path d="M2.5 13c.2-2.5 1.4-3.8 3.5-3.8s3.3 1.3 3.5 3.8M10 4.2a2 2 0 0 1 0 3.6M10.8 9.5c1.6.3 2.5 1.5 2.7 3.5" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/>'
    };
    return `<span class="property-fact"><svg aria-hidden="true" viewBox="0 0 16 16">${icons[type]}</svg>${escapeHtml(label)}</span>`;
}

function filterListings() {
    const query = document.getElementById('searchInput').value.trim().toLowerCase();
    const cards = document.querySelectorAll('.property-card');
    const groups = document.querySelectorAll('.property-portfolio');
    let visibleCount = 0;
    let visiblePortfolioCount = 0;

    cards.forEach(card => {
        const matches = !query || (card.dataset.search || '').includes(query);
        card.hidden = !matches;
        if (matches) visibleCount += 1;
    });

    groups.forEach(group => {
        const visibleCards = Array.from(group.querySelectorAll('.property-card')).filter(card => !card.hidden);
        group.hidden = visibleCards.length === 0;
        if (visibleCards.length) {
            visiblePortfolioCount += 1;
            if (query) group.open = true;
        }
    });

    document.getElementById('noResults').hidden = visibleCount !== 0;
    updatePropertyCount(visibleCount, visiblePortfolioCount, Boolean(query));
}

function updatePropertyCount(propertyCount, portfolioCount, isFiltered) {
    const count = document.getElementById('propertyCount');
    if (!count) return;
    if (isFiltered) {
        count.textContent = `${formatCount(propertyCount, 'match', 'matches')} across ${formatCount(portfolioCount, 'portfolio')}`;
    } else {
        count.textContent = `${formatCount(propertyCount, 'property', 'properties')} across ${formatCount(portfolioCount, 'portfolio')}`;
    }
}

function initializeListingTags(listingId, tags) {
    window.listingTagsMap ||= {};
    window.listingTagsMap[listingId] = tags;
    const displayContainer = document.getElementById(`tagsDisplay-${listingId}`);
    if (displayContainer) renderReadOnlyTags(displayContainer, tags);

    const inputContainer = document.getElementById(`tagsInput-${listingId}`);
    if (!inputContainer || listingTagInputs[listingId]) return;
    inputContainer.hidden = false;
    inputContainer.style.position = 'absolute';
    inputContainer.style.visibility = 'hidden';
    listingTagInputs[listingId] = new TagInput(inputContainer, {
        existingTags: tags,
        onTagsChange: newTags => syncListingTags(listingId, newTags),
    });
    inputContainer.hidden = true;
    inputContainer.style.position = '';
    inputContainer.style.visibility = '';
}

function toggleTagEdit(listingId) {
    const inputContainer = document.getElementById(`tagsInput-${listingId}`);
    const displayContainer = document.getElementById(`tagsDisplay-${listingId}`);
    const editButton = document.getElementById(`tagEditBtn-${listingId}`);
    const card = displayContainer?.closest('.property-card');
    if (!inputContainer || !displayContainer || !card) return;

    const isEditing = card.classList.toggle('is-editing');
    inputContainer.hidden = !isEditing;
    displayContainer.hidden = isEditing;
    editButton.textContent = isEditing ? 'Done' : 'Edit';

    if (isEditing) {
        requestAnimationFrame(() => listingTagInputs[listingId]?.input?.focus());
    } else if (propertyCatalogNeedsRefresh) {
        loadListings();
    }
}

async function syncListingTags(listingId, newTags) {
    const currentTags = window.listingTagsMap?.[listingId] || [];
    const currentIds = new Set(currentTags.map(tag => tag.tag_id));
    const nextIds = new Set(newTags.map(tag => tag.tag_id));
    const added = newTags.filter(tag => !currentIds.has(tag.tag_id));
    const removed = currentTags.filter(tag => !nextIds.has(tag.tag_id));

    window.listingTagsMap[listingId] = newTags;
    try {
        if (added.length) {
            const response = await fetch(`/api/listings/${listingId}/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tags: added.map(tag => tag.name) }),
            });
            if (!response.ok) throw new Error('Unable to add tag');
        }
        for (const tag of removed) {
            const response = await fetch(`/api/listings/${listingId}/tags/${tag.tag_id}`, { method: 'DELETE' });
            if (!response.ok) throw new Error('Unable to remove tag');
        }
        if (added.length || removed.length) propertyCatalogNeedsRefresh = true;
        await loadListingTags(listingId);
    } catch (error) {
        console.error('Error updating listing tags:', error);
        alert(error.message || 'Unable to update tags');
        window.listingTagsMap[listingId] = currentTags;
        listingTagInputs[listingId]?.setTags(currentTags);
    }
}

async function loadListingTags(listingId) {
    const response = await fetch(`/api/listings/${listingId}/tags`);
    if (!response.ok) return;
    const tags = await response.json();
    window.listingTagsMap[listingId] = tags;
    const displayContainer = document.getElementById(`tagsDisplay-${listingId}`);
    if (displayContainer) renderReadOnlyTags(displayContainer, tags);
    listingTagInputs[listingId]?.setTags(tags);
}

function renderReadOnlyTags(container, tags) {
    container.innerHTML = '';
    container.className = 'property-tags-display tags-display';
    tags.forEach(tag => {
        const chip = document.createElement('span');
        chip.className = 'tag-chip';
        if (tag.color) {
            chip.style.backgroundColor = tag.color;
            chip.style.borderColor = tag.color;
            chip.classList.add('has-color');
        }
        const text = document.createElement('span');
        text.className = 'tag-chip-text';
        text.textContent = tag.name;
        chip.appendChild(text);
        container.appendChild(chip);
    });
}

function displayPortfolioName(name) {
    if (!name) return 'Unassigned';
    return name === name.toLowerCase()
        ? name.replace(/\b\w/g, character => character.toUpperCase())
        : name;
}

function initials(value) {
    return String(value || 'P').split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase();
}

function isActiveStatus(status) {
    return !status || ['active', 'listed', 'published'].includes(String(status).toLowerCase());
}

function formatReviewDate(value) {
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(date);
}

function formatMoney(value, currency) {
    try {
        return new Intl.NumberFormat('en-US', {
            style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0,
        }).format(Number(value));
    } catch (_error) {
        return `$${Math.round(Number(value))}`;
    }
}

function formatNumber(value) {
    const numeric = Number(value);
    return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1).replace(/\.0$/, '');
}

function formatCount(count, singular, plural = `${singular}s`) {
    return `${count} ${Number(count) === 1 ? singular : plural}`;
}

function escapeHtml(value) {
    return (value == null ? '' : String(value)).replace(/[&<>"']/g, character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    })[character]);
}
