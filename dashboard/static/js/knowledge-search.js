/**
 * Knowledge Search Component
 * Handles full-text search of documents with results display
 */

class KnowledgeSearch {
    constructor(container, resultsContainer, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        this.resultsContainer = typeof resultsContainer === 'string' ? document.querySelector(resultsContainer) : resultsContainer;
        
        if (!this.container) {
            console.error('KnowledgeSearch: Container element not found');
            return;
        }
        
        if (!this.resultsContainer) {
            console.error('KnowledgeSearch: Results container element not found');
            return;
        }
        
        this.options = {
            onResultClick: options.onResultClick || ((docId) => {
                if (typeof DocumentViewer !== 'undefined') {
                    DocumentViewer.open(docId);
                } else {
                    window.open(`/knowledge/api/documents/${docId}/file`, '_blank');
                }
            }),
            debounceMs: options.debounceMs || 500
        };
        
        // Search state tracking
        this.searchState = {
            query: '',
            selectedListingIds: [],
            selectedTagIds: []
        };
        
        this.searchTimeout = null;
        this.listingMultiSelect = null;
        this.tagInput = null;
        this.filterChipsContainer = null;
        this.render();
    }
    
    render() {
        this.container.innerHTML = '';
        this.container.className = 'knowledge-search-container';
        
        // Filter chips container (for displaying active filters)
        this.filterChipsContainer = document.createElement('div');
        this.filterChipsContainer.className = 'filter-chips-container';
        this.filterChipsContainer.style.display = 'flex';
        this.filterChipsContainer.style.flexWrap = 'wrap';
        this.filterChipsContainer.style.gap = '0.5rem';
        this.filterChipsContainer.style.marginBottom = '1rem';
        this.container.appendChild(this.filterChipsContainer);
        
        // Filters section
        const filtersWrapper = document.createElement('div');
        filtersWrapper.className = 'search-filters';
        filtersWrapper.style.marginBottom = '1rem';
        
        // Listing filter
        const listingFilterWrapper = document.createElement('div');
        listingFilterWrapper.className = 'filter-group';
        listingFilterWrapper.style.marginBottom = '1rem';
        
        const listingLabel = document.createElement('label');
        listingLabel.textContent = 'Filter by Properties:';
        listingLabel.style.display = 'block';
        listingLabel.style.marginBottom = '0.5rem';
        listingLabel.style.fontWeight = '500';
        listingFilterWrapper.appendChild(listingLabel);
        
        const listingContainer = document.createElement('div');
        listingContainer.id = 'knowledgeListingFilter';
        listingFilterWrapper.appendChild(listingContainer);
        filtersWrapper.appendChild(listingFilterWrapper);
        
        // Tag filter
        const tagFilterWrapper = document.createElement('div');
        tagFilterWrapper.className = 'filter-group';
        
        const tagLabel = document.createElement('label');
        tagLabel.textContent = 'Filter by Tags:';
        tagLabel.style.display = 'block';
        tagLabel.style.marginBottom = '0.5rem';
        tagLabel.style.fontWeight = '500';
        tagFilterWrapper.appendChild(tagLabel);
        
        const tagContainer = document.createElement('div');
        tagContainer.id = 'knowledgeTagFilter';
        tagFilterWrapper.appendChild(tagContainer);
        filtersWrapper.appendChild(tagFilterWrapper);
        
        this.container.appendChild(filtersWrapper);
        
        // Search input
        const searchWrapper = document.createElement('div');
        searchWrapper.className = 'search-input-wrapper';
        searchWrapper.style.display = 'flex';
        searchWrapper.style.gap = '0.5rem';
        
        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'search-input';
        searchInput.style.flex = '1';
        searchInput.style.padding = '0.75rem';
        searchInput.style.border = '1px solid var(--gray-300)';
        searchInput.style.borderRadius = 'var(--radius)';
        searchInput.placeholder = 'Search documents...';
        searchInput.addEventListener('input', (e) => this.handleSearch(e));
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.runSearch();
            }
        });
        this.searchInput = searchInput;
        searchWrapper.appendChild(searchInput);
        
        const searchButton = document.createElement('button');
        searchButton.type = 'button';
        searchButton.className = 'btn btn-primary';
        searchButton.textContent = 'Search';
        searchButton.addEventListener('click', () => {
            this.runSearch();
        });
        searchWrapper.appendChild(searchButton);
        
        this.container.appendChild(searchWrapper);
        
        // Initialize filter components after a short delay to ensure DOM is ready
        setTimeout(() => {
            this.initializeFilters();
        }, 100);
    }
    
    initializeFilters() {
        // Initialize ListingMultiSelect
        if (typeof ListingMultiSelect !== 'undefined') {
            const listingContainer = document.getElementById('knowledgeListingFilter');
            if (listingContainer) {
                // Load listings first
                fetch('/api/listings')
                    .then(response => response.json())
                    .then(listings => {
                        this.listingMultiSelect = new ListingMultiSelect(listingContainer, {
                            listings: listings || [],
                            placeholder: 'Select properties...',
                            onSelectionChange: (selectedIds) => {
                                this.searchState.selectedListingIds = selectedIds || [];
                                this.updateFilterChips();
                                // Update "All Documents" section and run search if there's a query
                                this.runSearch();
                            }
                        });
                    })
                    .catch(error => {
                        console.error('Error loading listings:', error);
                    });
            }
        }
        
        // Initialize TagInput
        if (typeof TagInput !== 'undefined') {
            const tagContainer = document.getElementById('knowledgeTagFilter');
            if (tagContainer) {
                this.tagInput = new TagInput(tagContainer, {
                    endpoint: '/api/tags/autocomplete',
                    placeholder: 'Type to add tags...',
                    onTagsChange: (selectedTags) => {
                        this.searchState.selectedTagIds = (selectedTags || []).map(t => t.tag_id || t.id || t);
                        this.updateFilterChips();
                        // Update "All Documents" section and run search if there's a query
                        this.runSearch();
                    }
                });
            }
        }
    }
    
    updateFilterChips() {
        if (!this.filterChipsContainer) return;
        
        this.filterChipsContainer.innerHTML = '';
        
        // Add listing chips
        if (this.searchState.selectedListingIds.length > 0 && this.listingMultiSelect) {
            const allListings = this.listingMultiSelect.options ? this.listingMultiSelect.options.listings : [];
            this.searchState.selectedListingIds.forEach(listingId => {
                let listing = null;
                if (listingId === 0) {
                    listing = { listing_id: 0, name: 'General', internal_listing_name: 'General (No Property)' };
                } else {
                    listing = allListings.find(l => (l.listing_id || l.id) == listingId);
                }
                
                if (listing) {
                    const chip = this.createFilterChip(
                        'listing',
                        listingId,
                        listing.internal_listing_name || listing.name || `Listing ${listingId}`,
                        () => {
                            if (this.listingMultiSelect && this.listingMultiSelect.toggleListing) {
                                this.listingMultiSelect.toggleListing(listingId);
                            }
                        }
                    );
                    this.filterChipsContainer.appendChild(chip);
                } else {
                    // Fallback: show chip even if listing not found
                    const chip = this.createFilterChip(
                        'listing',
                        listingId,
                        listingId === 0 ? 'General' : `Listing ${listingId}`,
                        () => {
                            if (this.listingMultiSelect && this.listingMultiSelect.toggleListing) {
                                this.listingMultiSelect.toggleListing(listingId);
                            }
                        }
                    );
                    this.filterChipsContainer.appendChild(chip);
                }
            });
        }
        
        // Add tag chips
        if (this.searchState.selectedTagIds.length > 0 && this.tagInput) {
            const allTags = this.tagInput.getTags ? this.tagInput.getTags() : [];
            this.searchState.selectedTagIds.forEach(tagId => {
                const tag = allTags.find(t => (t.tag_id || t.id) == tagId);
                if (tag) {
                    const chip = this.createFilterChip(
                        'tag',
                        tagId,
                        tag.name || `Tag ${tagId}`,
                        () => {
                            if (this.tagInput) {
                                const tagIndex = allTags.findIndex(t => (t.tag_id || t.id) == tagId);
                                if (tagIndex >= 0 && this.tagInput.removeTag) {
                                    this.tagInput.removeTag(tagIndex);
                                }
                            }
                        }
                    );
                    this.filterChipsContainer.appendChild(chip);
                } else {
                    // Fallback: show chip even if tag not found
                    const chip = this.createFilterChip(
                        'tag',
                        tagId,
                        `Tag ${tagId}`,
                        () => {
                            // Try to find and remove by ID
                            if (this.tagInput && this.tagInput.tags) {
                                const tagIndex = this.tagInput.tags.findIndex(t => (t.tag_id || t.id) == tagId);
                                if (tagIndex >= 0 && this.tagInput.removeTag) {
                                    this.tagInput.removeTag(tagIndex);
                                }
                            }
                        }
                    );
                    this.filterChipsContainer.appendChild(chip);
                }
            });
        }
        
        // Show/hide container based on whether there are any filters
        this.filterChipsContainer.style.display = 
            (this.searchState.selectedListingIds.length > 0 || this.searchState.selectedTagIds.length > 0) 
                ? 'flex' : 'none';
    }
    
    createFilterChip(type, value, label, onRemove) {
        const chip = document.createElement('div');
        chip.className = 'filter-chip';
        chip.style.display = 'inline-flex';
        chip.style.alignItems = 'center';
        chip.style.gap = '0.5rem';
        chip.style.padding = '0.375rem 0.75rem';
        chip.style.background = 'var(--primary-color)';
        chip.style.color = 'var(--white)';
        chip.style.borderRadius = '9999px';
        chip.style.fontSize = '0.8125rem';
        chip.style.fontWeight = '500';
        
        const labelSpan = document.createElement('span');
        labelSpan.textContent = label;
        chip.appendChild(labelSpan);
        
        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.textContent = '×';
        removeBtn.style.background = 'rgba(255, 255, 255, 0.2)';
        removeBtn.style.border = 'none';
        removeBtn.style.color = 'var(--white)';
        removeBtn.style.width = '18px';
        removeBtn.style.height = '18px';
        removeBtn.style.borderRadius = '50%';
        removeBtn.style.cursor = 'pointer';
        removeBtn.style.display = 'flex';
        removeBtn.style.alignItems = 'center';
        removeBtn.style.justifyContent = 'center';
        removeBtn.style.fontSize = '14px';
        removeBtn.style.lineHeight = '1';
        removeBtn.style.padding = '0';
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            onRemove();
        });
        chip.appendChild(removeBtn);
        
        return chip;
    }
    
    handleSearch(e) {
        const query = e.target.value.trim();
        this.searchState.query = query;
        
        // Clear existing timeout
        if (this.searchTimeout) {
            clearTimeout(this.searchTimeout);
        }
        
        // Always update "All Documents" section when query changes
        this.updateAllDocumentsSection();
        
        // Debounce search
        if (query.length >= 3) {
            this.searchTimeout = setTimeout(() => {
                this.runSearch();
            }, this.options.debounceMs);
        } else {
            // Clear search results if query is too short
            this.clearResults();
        }
    }
    
    runSearch() {
        const query = this.searchState.query.trim();
        
        // Update "All Documents" section with current filters (always)
        this.updateAllDocumentsSection();
        
        // If no query or query is less than 3 chars, clear search results
        if (!query || query.length < 3) {
            this.clearResults();
            return;
        }
        
        // Show loading for search results
        this.showLoading();
        
        // Build request body for full-text search
        const requestBody = {
            query: query
        };
        
        if (this.searchState.selectedListingIds.length > 0) {
            requestBody.listing_ids = this.searchState.selectedListingIds;
        }
        
        if (this.searchState.selectedTagIds.length > 0) {
            requestBody.tag_ids = this.searchState.selectedTagIds;
        }
        
        // Perform full-text search with filters
        this.performSearch(requestBody);
    }
    
    updateAllDocumentsSection() {
        // Call the global loadDocumentsList function with current filters
        if (typeof loadDocumentsList === 'function') {
            loadDocumentsList(this.searchState.selectedListingIds, this.searchState.selectedTagIds);
        }
    }
    
    async performSearch(requestBody) {
        try {
            const response = await fetch('/knowledge/api/documents/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });
            
            if (!response.ok) {
                throw new Error('Search failed');
            }
            
            const data = await response.json();
            this.displayResults(data.results || []);
            
        } catch (error) {
            console.error('Search error:', error);
            this.showError('Failed to search documents. Please try again.');
        }
    }
    
    
    displayResults(results) {
        if (!this.resultsContainer) return;
        
        if (results.length === 0) {
            const hasFilters = this.searchState.selectedListingIds.length > 0 || this.searchState.selectedTagIds.length > 0;
            const hasQuery = this.searchState.query.trim().length >= 3;
            
            let message = 'No documents found';
            if (hasQuery && hasFilters) {
                message = 'No documents match this search and filter combination.';
            } else if (hasQuery) {
                message = 'No documents found matching your search.';
            } else if (hasFilters) {
                message = 'No documents match the selected filters.';
            }
            
            this.resultsContainer.innerHTML = `<p class="no-results">${this.escapeHtml(message)}</p>`;
            return;
        }
        
        this.resultsContainer.innerHTML = results.map(result => {
            const relevance = result.relevance_score ? `Relevance: ${(result.relevance_score * 100).toFixed(1)}%` : '';
            const listings = result.listings && result.listings.length > 0 ? ` • ${result.listings.length} property(ies)` : '';
            const tags = result.tags && result.tags.length > 0 ? ` • ${result.tags.length} tag(s)` : '';
            
            return `
                <div class="search-result-item" onclick="this.dispatchEvent(new CustomEvent('resultClick', {detail: {documentId: ${result.document_id}}}))">
                    <div class="search-result-title">${this.escapeHtml(result.title)}</div>
                    ${result.snippet ? `<div class="search-result-snippet">${result.snippet}</div>` : ''}
                    <div class="search-result-meta">
                        ${relevance}${listings}${tags}
                    </div>
                </div>
            `;
        }).join('');
        
        // Add click handlers
        this.resultsContainer.querySelectorAll('.search-result-item').forEach(item => {
            item.addEventListener('resultClick', (e) => {
                if (this.options.onResultClick) {
                    this.options.onResultClick(e.detail.documentId);
                }
            });
        });
    }
    
    showLoading() {
        if (!this.resultsContainer) return;
        this.resultsContainer.innerHTML = '<p class="loading">Searching...</p>';
    }
    
    showError(message) {
        if (!this.resultsContainer) return;
        this.resultsContainer.innerHTML = `<p class="error">${this.escapeHtml(message)}</p>`;
    }
    
    clearResults() {
        if (!this.resultsContainer) return;
        this.resultsContainer.innerHTML = '';
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

