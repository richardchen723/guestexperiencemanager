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
        
        this.searchTimeout = null;
        this.render();
    }
    
    render() {
        this.container.innerHTML = '';
        this.container.className = 'knowledge-search-container';
        
        // Search input
        const searchWrapper = document.createElement('div');
        searchWrapper.className = 'search-input-wrapper';
        
        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'search-input';
        searchInput.placeholder = 'Search documents...';
        searchInput.addEventListener('input', (e) => this.handleSearch(e));
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.performSearch(e.target.value);
            }
        });
        this.searchInput = searchInput;
        searchWrapper.appendChild(searchInput);
        
        const searchButton = document.createElement('button');
        searchButton.type = 'button';
        searchButton.className = 'btn btn-primary';
        searchButton.textContent = 'Search';
        searchButton.addEventListener('click', () => {
            this.performSearch(this.searchInput.value);
        });
        searchWrapper.appendChild(searchButton);
        
        this.container.appendChild(searchWrapper);
        
        // Filters (optional - can be expanded)
        const filtersWrapper = document.createElement('div');
        filtersWrapper.className = 'search-filters';
        filtersWrapper.style.display = 'none'; // Hidden for now
        this.container.appendChild(filtersWrapper);
    }
    
    handleSearch(e) {
        const query = e.target.value.trim();
        
        // Clear existing timeout
        if (this.searchTimeout) {
            clearTimeout(this.searchTimeout);
        }
        
        // Debounce search
        if (query.length >= 3) {
            this.searchTimeout = setTimeout(() => {
                this.performSearch(query);
            }, this.options.debounceMs);
        } else if (query.length === 0) {
            // Clear results if query is empty
            this.clearResults();
        }
    }
    
    async performSearch(query) {
        if (!query || query.trim().length < 3) {
            this.clearResults();
            return;
        }
        
        const searchQuery = query.trim();
        
        // Show loading
        this.showLoading();
        
        try {
            const response = await fetch('/knowledge/api/documents/search', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    query: searchQuery
                })
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
            this.resultsContainer.innerHTML = '<p class="no-results">No documents found matching your search.</p>';
            return;
        }
        
        this.resultsContainer.innerHTML = results.map(result => `
            <div class="search-result-item" onclick="this.dispatchEvent(new CustomEvent('resultClick', {detail: {documentId: ${result.document_id}}}))">
                <div class="search-result-title">${this.escapeHtml(result.title)}</div>
                <div class="search-result-snippet">${result.snippet || ''}</div>
                <div class="search-result-meta">
                    Relevance: ${(result.relevance_score * 100).toFixed(1)}%
                    ${result.listings && result.listings.length > 0 ? ` • ${result.listings.length} property(ies)` : ''}
                    ${result.tags && result.tags.length > 0 ? ` • ${result.tags.length} tag(s)` : ''}
                </div>
            </div>
        `).join('');
        
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

