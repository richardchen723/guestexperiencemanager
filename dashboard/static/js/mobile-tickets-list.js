/**
 * Mobile Tickets List Enhancements
 * Handles filter bottom sheet and mobile card rendering
 */

class MobileTicketsList {
    constructor() {
        this.isMobile = window.innerWidth < 768;
        this.filterSheet = null;
        this.init();
    }
    
    init() {
        if (this.isMobile) {
            this.setupMobileFilters();
            this.setupMobileCardRendering();
        }
        
        // Listen for resize
        window.addEventListener('resize', () => {
            const wasMobile = this.isMobile;
            this.isMobile = window.innerWidth < 768;
            
            if (wasMobile !== this.isMobile) {
                if (this.isMobile) {
                    this.setupMobileFilters();
                    this.setupMobileCardRendering();
                } else {
                    this.teardownMobileFilters();
                }
            }
        });
    }
    
    /**
     * Setup mobile filter button and bottom sheet
     */
    setupMobileFilters() {
        // Hide desktop filter panel
        const filterPanel = document.querySelector('.tickets-filters-modern');
        if (filterPanel) {
            filterPanel.style.display = 'none';
        }
        
        // Create mobile filter button
        this.createFilterButton();
        
        // Initialize filter sheet
        this.initFilterSheet();
    }
    
    /**
     * Create mobile filter button
     */
    createFilterButton() {
        // Check if button already exists
        if (document.getElementById('mobileFilterBtn')) {
            return;
        }
        
        const searchBar = document.querySelector('.tickets-search-bar');
        if (!searchBar) return;
        
        const filterBtn = document.createElement('button');
        filterBtn.id = 'mobileFilterBtn';
        filterBtn.className = 'mobile-filter-btn mobile-only';
        filterBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M3 4.5H17M3 10H17M3 15.5H17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <span>Filters</span>
            <span id="mobileFilterBadge" class="mobile-filter-badge" style="display: none;"></span>
        `;
        filterBtn.addEventListener('click', () => {
            if (this.filterSheet) {
                this.filterSheet.open();
            }
        });
        
        // Insert after search bar
        searchBar.parentNode.insertBefore(filterBtn, searchBar.nextSibling);
    }
    
    /**
     * Initialize filter sheet with current filter values
     */
    initFilterSheet() {
        if (this.filterSheet) {
            this.filterSheet.destroy();
        }
        
        // Get current filter values
        const filters = this.getCurrentFilters();
        
        this.filterSheet = new FilterSheet({
            title: 'Filters',
            filters: filters,
            onApply: () => {
                this.applyFilters();
                this.filterSheet.close();
            },
            onCancel: () => {
                this.filterSheet.close();
            },
            onFilterChange: () => {
                this.updateFilterBadge();
            }
        });
        
        // Update badge
        this.updateFilterBadge();
    }
    
    /**
     * Get current filter values from DOM
     */
    getCurrentFilters() {
        const filters = {};
        
        // Status filter
        const statusCheckboxes = document.querySelectorAll('.status-checkbox:checked');
        const statuses = Array.from(statusCheckboxes).map(cb => cb.value);
        filters.status = {
            type: 'multiselect',
            label: 'Status',
            value: statuses.length > 0 ? statuses : ['Open', 'Assigned', 'In Progress', 'Blocked', 'Resolved', 'Closed'],
            defaultValue: ['Open', 'Assigned', 'In Progress', 'Blocked', 'Resolved', 'Closed'],
            options: [
                { value: 'Open', label: 'Open' },
                { value: 'Assigned', label: 'Assigned' },
                { value: 'In Progress', label: 'In Progress' },
                { value: 'Blocked', label: 'Blocked' },
                { value: 'Resolved', label: 'Resolved' },
                { value: 'Closed', label: 'Closed' }
            ]
        };
        
        // Property filter
        const listingCheckboxes = document.querySelectorAll('.listing-checkbox:checked');
        const listings = Array.from(listingCheckboxes).map(cb => cb.value);
        filters.property = {
            type: 'multiselect',
            label: 'Property',
            value: listings,
            defaultValue: [],
            options: [] // Will be populated from allListings
        };
        
        // Assigned filter
        const assignedSelect = document.getElementById('filterAssigned');
        filters.assigned = {
            type: 'select',
            label: 'Assigned To',
            value: assignedSelect ? assignedSelect.value : '',
            defaultValue: '',
            options: [] // Will be populated from allUsers
        };
        
        // Priority filter
        const prioritySelect = document.getElementById('filterPriority');
        filters.priority = {
            type: 'select',
            label: 'Priority',
            value: prioritySelect ? prioritySelect.value : '',
            defaultValue: '',
            options: [
                { value: '', label: 'All Priorities' },
                { value: 'Low', label: 'Low' },
                { value: 'Medium', label: 'Medium' },
                { value: 'High', label: 'High' },
                { value: 'Critical', label: 'Critical' }
            ]
        };
        
        // Category filter
        const categorySelect = document.getElementById('filterCategory');
        filters.category = {
            type: 'select',
            label: 'Category',
            value: categorySelect ? categorySelect.value : '',
            defaultValue: '',
            options: [
                { value: '', label: 'All Categories' },
                { value: 'cleaning', label: 'Cleaning' },
                { value: 'maintenance', label: 'Maintenance' },
                { value: 'online', label: 'Online' },
                { value: 'technology', label: 'Technology' },
                { value: 'review management', label: 'Review Management' },
                { value: 'other', label: 'Other' }
            ]
        };
        
        // Past due filter
        const pastDueCheckbox = document.getElementById('filterPastDue');
        filters.pastDue = {
            type: 'checkbox',
            label: 'Past Due',
            value: pastDueCheckbox ? pastDueCheckbox.checked : false,
            defaultValue: false
        };
        
        return filters;
    }
    
    /**
     * Apply filters from filter sheet
     */
    applyFilters() {
        if (!this.filterSheet) return;
        
        const filterValues = this.filterSheet.getFilterValues();
        
        // Update DOM elements to match filter sheet values
        // Status
        if (filterValues.status) {
            document.querySelectorAll('.status-checkbox').forEach(cb => {
                cb.checked = filterValues.status.includes(cb.value);
            });
        }
        
        // Property
        if (filterValues.property) {
            document.querySelectorAll('.listing-checkbox').forEach(cb => {
                cb.checked = filterValues.property.includes(cb.value);
            });
        }
        
        // Assigned
        if (filterValues.assigned !== undefined) {
            const assignedSelect = document.getElementById('filterAssigned');
            if (assignedSelect) {
                assignedSelect.value = filterValues.assigned;
            }
        }
        
        // Priority
        if (filterValues.priority !== undefined) {
            const prioritySelect = document.getElementById('filterPriority');
            if (prioritySelect) {
                prioritySelect.value = filterValues.priority;
            }
        }
        
        // Category
        if (filterValues.category !== undefined) {
            const categorySelect = document.getElementById('filterCategory');
            if (categorySelect) {
                categorySelect.value = filterValues.category;
            }
        }
        
        // Past due
        if (filterValues.pastDue !== undefined) {
            const pastDueCheckbox = document.getElementById('filterPastDue');
            if (pastDueCheckbox) {
                pastDueCheckbox.checked = filterValues.pastDue;
            }
        }
        
        // Trigger filter update
        if (typeof updateActiveFilters === 'function') {
            updateActiveFilters();
        }
        if (typeof loadTickets === 'function') {
            loadTickets();
        }
    }
    
    /**
     * Update filter badge count
     */
    updateFilterBadge() {
        if (!this.filterSheet) return;
        
        const badge = document.getElementById('mobileFilterBadge');
        if (badge) {
            const count = this.filterSheet.filterCount;
            badge.textContent = count > 0 ? count : '';
            badge.style.display = count > 0 ? 'block' : 'none';
        }
    }
    
    /**
     * Setup mobile card rendering
     */
    setupMobileCardRendering() {
        // The ticket cards should already be rendered as cards
        // We just need to ensure they're styled correctly for mobile
        // This is handled by CSS
    }
    
    /**
     * Teardown mobile filters
     */
    teardownMobileFilters() {
        // Show desktop filter panel
        const filterPanel = document.querySelector('.tickets-filters-modern');
        if (filterPanel) {
            filterPanel.style.display = '';
        }
        
        // Remove mobile filter button
        const filterBtn = document.getElementById('mobileFilterBtn');
        if (filterBtn) {
            filterBtn.remove();
        }
        
        // Close filter sheet if open
        if (this.filterSheet && this.filterSheet.isOpen) {
            this.filterSheet.close();
        }
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.mobileTicketsList = new MobileTicketsList();
    });
} else {
    window.mobileTicketsList = new MobileTicketsList();
}

