// Reviews Page JavaScript

let allTags = [];
let currentEditingFilterId = null;
let unrespondedTagFilter = null;
let selectedUnrespondedTagIds = [];

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadTags();
    
    // Initialize tag filter for unresponded reviews
    try {
        unrespondedTagFilter = new TagFilter('#unrespondedTagFilterContainer', {
            endpoint: '/api/tags',
            onFilterChange: (tagIds, logic) => {
                selectedUnrespondedTagIds = tagIds;
                loadUnrespondedReviews(tagIds);
            }
        });
    } catch (error) {
        console.error('Error initializing unresponded tag filter:', error);
        unrespondedTagFilter = null;
    }
    
    loadUnrespondedReviews();
    loadFilters();
    
    // Set up filter form submission
    document.getElementById('filterForm').addEventListener('submit', handleFilterSubmit);
    document.getElementById('addFilterBtn').addEventListener('click', openFilterModal);
});

// Load all tags for filter form
async function loadTags() {
    try {
        const response = await fetch('/api/tags');
        const data = await response.json();
        // API returns tags array directly or wrapped in tags property
        allTags = Array.isArray(data) ? data : (data.tags || []);
        
        // Populate tag select in filter form
        const tagSelect = document.getElementById('filterTags');
        tagSelect.innerHTML = '';
        allTags.forEach(tag => {
            const option = document.createElement('option');
            option.value = tag.tag_id;
            option.textContent = tag.name;
            tagSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading tags:', error);
    }
}

// Load unresponded reviews
async function loadUnrespondedReviews(tagIds = null) {
    const container = document.getElementById('unrespondedReviewsContainer');
    container.innerHTML = '<div class="loading-spinner">Loading reviews...</div>';
    
    try {
        // Build URL with tag_ids parameter if provided
        let url = '/reviews/api/unresponded';
        if (tagIds && tagIds.length > 0) {
            const tagIdsParam = JSON.stringify(tagIds);
            url += `?tag_ids=${encodeURIComponent(tagIdsParam)}`;
        }
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.reviews && data.reviews.length > 0) {
            renderReviews(container, data.reviews);
        } else {
            container.innerHTML = '<div class="empty-state">No unresponded reviews found.</div>';
        }
    } catch (error) {
        console.error('Error loading unresponded reviews:', error);
        container.innerHTML = '<div class="error-state">Error loading reviews. Please try again.</div>';
    }
}

// Load filters
async function loadFilters() {
    const container = document.getElementById('filtersContainer');
    container.innerHTML = '<div class="loading-spinner">Loading filters...</div>';
    
    try {
        const response = await fetch('/reviews/api/filters');
        const data = await response.json();
        
        if (data.filters && data.filters.length > 0) {
            renderFilters(container, data.filters);
        } else {
            container.innerHTML = '<div class="empty-state">No filters created yet. Click "Add Filter" to create one.</div>';
        }
    } catch (error) {
        console.error('Error loading filters:', error);
        container.innerHTML = '<div class="error-state">Error loading filters. Please try again.</div>';
    }
}

// Render reviews
function renderReviews(container, reviews) {
    container.innerHTML = '';
    
    if (reviews.length === 0) {
        container.innerHTML = '<div class="empty-state">No reviews found.</div>';
        return;
    }
    
    reviews.forEach(review => {
        const reviewCard = createReviewCard(review);
        container.appendChild(reviewCard);
    });
}

// Create review card element
function createReviewCard(review) {
    const card = document.createElement('div');
    card.className = 'review-card';
    
    // Rating stars
    // Note: overall_rating is in 10-point scale (0-10), but we display in 5-star scale (0-5)
    const rating = review.overall_rating || 0;
    const ratingStars = rating / 2.0; // Convert from 10-point to 5-star scale
    const filledStars = Math.max(0, Math.min(5, Math.floor(ratingStars))); // Clamp between 0 and 5
    const emptyStars = Math.max(0, Math.min(5, 5 - filledStars)); // Clamp between 0 and 5
    const stars = '★'.repeat(filledStars) + '☆'.repeat(emptyStars);
    
    // Tags
    const tagsHtml = review.tags && review.tags.length > 0
        ? review.tags.map(tag => `<span class="tag-badge" style="background-color: ${tag.color || '#6b7280'}; color: ${tag.color ? '#ffffff' : '#ffffff'}">${escapeHtml(tag.name)}</span>`).join('')
        : '';
    
    // Review text preview (first 150 chars for more compact cards)
    const reviewText = review.review_text || 'No review text';
    const reviewPreview = reviewText.length > 150 ? reviewText.substring(0, 150) + '...' : reviewText;
    
    // Review date
    const reviewDate = review.review_date ? new Date(review.review_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : 'N/A';
    
    // Departure date
    const departureDate = review.departure_date ? new Date(review.departure_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'N/A';
    
    // Guest name
    const guestName = review.reviewer_name || 'Anonymous';
    
    // Channel name
    const channelName = review.channel_name || 'N/A';
    
    card.innerHTML = `
        <div class="review-card-header">
            <div class="review-card-title">
                <h4>${escapeHtml(review.listing_name || 'Unknown Listing')}</h4>
                <span class="review-rating">${stars} ${rating > 0 ? ratingStars.toFixed(1) : 'N/A'}</span>
            </div>
            <div class="review-card-meta">
                <div class="review-guest-info">
                    <span class="review-guest-name">${escapeHtml(guestName)}</span>
                    <span class="review-departure-date">• ${departureDate}</span>
                    <span class="review-channel">• ${escapeHtml(channelName)}</span>
                </div>
                <span class="review-date">${reviewDate}</span>
            </div>
        </div>
        <div class="review-card-body">
            <p class="review-text">${escapeHtml(reviewPreview)}</p>
            ${tagsHtml ? `<div class="review-tags">${tagsHtml}</div>` : ''}
        </div>
    `;
    
    return card;
}

// Render filters
function renderFilters(container, filters) {
    container.innerHTML = '';
    
    filters.forEach(filter => {
        const filterSection = createFilterSection(filter);
        container.appendChild(filterSection);
        // Load reviews AFTER section is appended to DOM so getElementById can find the container
        loadFilterReviews(filter.filter_id);
    });
}

// Create filter section element
function createFilterSection(filter) {
    const section = document.createElement('div');
    section.className = 'filter-section';
    section.dataset.filterId = filter.filter_id;
    
    // Build criteria summary
    const criteria = [];
    if (filter.tag_ids && filter.tag_ids.length > 0) {
        const tagNames = filter.tag_ids.map(tagId => {
            const tag = allTags.find(t => t.tag_id === tagId);
            return tag ? tag.name : `Tag ${tagId}`;
        }).join(', ');
        criteria.push(`Tags: ${tagNames}`);
    }
    if (filter.max_rating !== null && filter.max_rating !== undefined) {
        criteria.push(`Rating: ≤${filter.max_rating} stars`);
    }
    if (filter.months_back !== null && filter.months_back !== undefined) {
        criteria.push(`Period: Last ${filter.months_back} month${filter.months_back !== 1 ? 's' : ''}`);
    }
    
    const criteriaText = criteria.length > 0 ? criteria.join(', ') : 'No criteria set';
    const filterName = filter.name || `Filter #${filter.filter_id}`;
    
    section.innerHTML = `
        <div class="filter-section-header">
            <div class="filter-section-title">
                <h4>${escapeHtml(filterName)}</h4>
                <p class="filter-criteria">${escapeHtml(criteriaText)}</p>
            </div>
            <div class="filter-section-actions">
                <button type="button" class="btn-secondary btn-sm" onclick="editFilter(${filter.filter_id})">Edit</button>
                <button type="button" class="btn-danger btn-sm" onclick="deleteFilter(${filter.filter_id})">Delete</button>
            </div>
        </div>
        <div class="filter-section-body">
            <div class="filter-reviews-container" id="filterReviews_${filter.filter_id}">
                <div class="loading-spinner">Loading reviews...</div>
            </div>
        </div>
    `;
    
    // Note: loadFilterReviews is called in renderFilters AFTER section is appended to DOM
    
    return section;
}

// Load reviews for a specific filter
async function loadFilterReviews(filterId) {
    const container = document.getElementById(`filterReviews_${filterId}`);
    if (!container) {
        return;
    }
    
    container.innerHTML = '<div class="loading-spinner">Loading reviews...</div>';
    
    try {
        const response = await fetch(`/reviews/api/filters/${filterId}/reviews`);
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
            throw new Error(errorData.error || `HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        if (data.reviews && data.reviews.length > 0) {
            renderReviews(container, data.reviews);
        } else {
            container.innerHTML = '<div class="empty-state">No reviews match this filter criteria.</div>';
        }
    } catch (error) {
        console.error(`Error loading reviews for filter ${filterId}:`, error);
        container.innerHTML = `<div class="error-state">Error loading reviews: ${escapeHtml(error.message)}. Please try again.</div>`;
    }
}

// Open filter modal for creating new filter
function openFilterModal(filterId = null) {
    currentEditingFilterId = filterId;
    const modal = document.getElementById('filterModal');
    const form = document.getElementById('filterForm');
    const title = document.getElementById('filterModalTitle');
    
    if (filterId) {
        // Editing existing filter - load filter data
        title.textContent = 'Edit Review Filter';
        loadFilterForEdit(filterId);
    } else {
        // Creating new filter - reset form
        title.textContent = 'Create Review Filter';
        form.reset();
        document.getElementById('filterId').value = '';
        document.getElementById('filterMaxRating').value = '4.0';
        document.getElementById('filterMonthsBack').value = '2';
        // Clear tag selections
        const tagSelect = document.getElementById('filterTags');
        Array.from(tagSelect.options).forEach(option => option.selected = false);
    }
    
    modal.style.display = 'block';
}

// Load filter data for editing
async function loadFilterForEdit(filterId) {
    try {
        const response = await fetch('/reviews/api/filters');
        const data = await response.json();
        const filter = data.filters.find(f => f.filter_id === filterId);
        
        if (filter) {
            document.getElementById('filterId').value = filter.filter_id;
            document.getElementById('filterName').value = filter.name || '';
            document.getElementById('filterMaxRating').value = filter.max_rating || '4.0';
            document.getElementById('filterMonthsBack').value = filter.months_back || '2';
            
            // Select tags
            const tagSelect = document.getElementById('filterTags');
            Array.from(tagSelect.options).forEach(option => {
                option.selected = filter.tag_ids && filter.tag_ids.includes(parseInt(option.value));
            });
        }
    } catch (error) {
        console.error('Error loading filter for edit:', error);
        alert('Error loading filter data. Please try again.');
    }
}

// Edit filter
function editFilter(filterId) {
    openFilterModal(filterId);
}

// Delete filter
async function deleteFilter(filterId) {
    if (!confirm('Are you sure you want to delete this filter?')) {
        return;
    }
    
    try {
        const response = await fetch(`/reviews/api/filters/${filterId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            loadFilters(); // Reload filters
        } else {
            const data = await response.json();
            alert(`Error deleting filter: ${data.error || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error deleting filter:', error);
        alert('Error deleting filter. Please try again.');
    }
}

// Handle filter form submission
async function handleFilterSubmit(e) {
    e.preventDefault();
    
    const form = e.target;
    const filterId = document.getElementById('filterId').value;
    const formData = {
        name: document.getElementById('filterName').value.trim() || null,
        tag_ids: Array.from(document.getElementById('filterTags').selectedOptions).map(opt => parseInt(opt.value)),
        max_rating: parseFloat(document.getElementById('filterMaxRating').value) || null,
        months_back: parseInt(document.getElementById('filterMonthsBack').value) || null
    };
    
    try {
        let response;
        if (filterId) {
            // Update existing filter
            response = await fetch(`/reviews/api/filters/${filterId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });
        } else {
            // Create new filter
            response = await fetch('/reviews/api/filters', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });
        }
        
        if (response.ok) {
            closeFilterModal();
            loadFilters(); // Reload filters
        } else {
            const data = await response.json();
            alert(`Error saving filter: ${data.error || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error saving filter:', error);
        alert('Error saving filter. Please try again.');
    }
}

// Close filter modal
function closeFilterModal() {
    const modal = document.getElementById('filterModal');
    modal.style.display = 'none';
    currentEditingFilterId = null;
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Close modal when clicking outside
window.onclick = function(event) {
    const modal = document.getElementById('filterModal');
    if (event.target === modal) {
        closeFilterModal();
    }
}

