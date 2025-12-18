/**
 * FilterSheet Component
 * Specialized bottom sheet for filters with state management
 */

class FilterSheet extends BottomSheet {
    constructor(options = {}) {
        super({
            title: options.title || 'Filters',
            showBackdrop: true,
            closeOnBackdrop: true,
            maxHeight: '85vh',
            onApply: options.onApply || null,
            onCancel: options.onCancel || null
        });
        
        this.filters = options.filters || {};
        this.onFilterChange = options.onFilterChange || null;
        this.filterCount = 0;
        
        this.renderFilters();
    }
    
    /**
     * Render filter content
     */
    renderFilters() {
        const body = this.sheet.querySelector('.bottom-sheet-body');
        if (!body) return;
        
        body.innerHTML = '';
        
        // Create filter sections
        Object.keys(this.filters).forEach(filterKey => {
            const filterConfig = this.filters[filterKey];
            const section = this.createFilterSection(filterKey, filterConfig);
            body.appendChild(section);
        });
    }
    
    /**
     * Create a filter section
     */
    createFilterSection(key, config) {
        const section = document.createElement('div');
        section.className = 'filter-sheet-section';
        
        const label = document.createElement('label');
        label.className = 'filter-sheet-label';
        label.textContent = config.label || key;
        section.appendChild(label);
        
        const control = this.createFilterControl(key, config);
        section.appendChild(control);
        
        return section;
    }
    
    /**
     * Create filter control based on type
     */
    createFilterControl(key, config) {
        const container = document.createElement('div');
        container.className = 'filter-sheet-control';
        
        switch (config.type) {
            case 'checkbox':
                container.appendChild(this.createCheckboxFilter(key, config));
                break;
            case 'select':
                container.appendChild(this.createSelectFilter(key, config));
                break;
            case 'multiselect':
                container.appendChild(this.createMultiSelectFilter(key, config));
                break;
            case 'date':
                container.appendChild(this.createDateFilter(key, config));
                break;
            default:
                container.appendChild(this.createTextFilter(key, config));
        }
        
        return container;
    }
    
    /**
     * Create checkbox filter (for boolean filters like "Past Due")
     */
    createCheckboxFilter(key, config) {
        const wrapper = document.createElement('label');
        wrapper.className = 'filter-sheet-checkbox';
        wrapper.style.display = 'flex';
        wrapper.style.alignItems = 'center';
        wrapper.style.gap = 'var(--mobile-space-3)';
        wrapper.style.padding = 'var(--mobile-space-3)';
        wrapper.style.cursor = 'pointer';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = key;
        checkbox.checked = config.value || false;
        checkbox.style.width = 'var(--touch-target-min)';
        checkbox.style.height = 'var(--touch-target-min)';
        checkbox.addEventListener('change', (e) => {
            this.updateFilter(key, e.target.checked);
        });
        
        const label = document.createElement('span');
        label.textContent = config.label || key;
        label.style.fontSize = 'var(--mobile-text-base)';
        
        wrapper.appendChild(checkbox);
        wrapper.appendChild(label);
        
        return wrapper;
    }
    
    /**
     * Create select filter
     */
    createSelectFilter(key, config) {
        const select = document.createElement('select');
        select.name = key;
        select.className = 'mobile-form-input';
        select.style.width = '100%';
        
        // Add options
        if (config.options) {
            config.options.forEach(option => {
                const opt = document.createElement('option');
                opt.value = option.value;
                opt.textContent = option.label;
                if (option.value === config.value) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });
        }
        
        select.addEventListener('change', (e) => {
            this.updateFilter(key, e.target.value);
        });
        
        return select;
    }
    
    /**
     * Create multi-select filter (checkboxes)
     */
    createMultiSelectFilter(key, config) {
        const container = document.createElement('div');
        container.className = 'filter-sheet-multiselect';
        
        if (config.options) {
            config.options.forEach(option => {
                const wrapper = document.createElement('label');
                wrapper.className = 'filter-sheet-checkbox';
                wrapper.style.display = 'flex';
                wrapper.style.alignItems = 'center';
                wrapper.style.gap = 'var(--mobile-space-3)';
                wrapper.style.padding = 'var(--mobile-space-3)';
                wrapper.style.cursor = 'pointer';
                
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.name = `${key}[]`;
                checkbox.value = option.value;
                checkbox.checked = (config.value || []).includes(option.value);
                checkbox.style.width = 'var(--touch-target-min)';
                checkbox.style.height = 'var(--touch-target-min)';
                checkbox.addEventListener('change', () => {
                    this.updateMultiSelectFilter(key, config);
                });
                
                const label = document.createElement('span');
                label.textContent = option.label;
                label.style.fontSize = 'var(--mobile-text-base)';
                
                wrapper.appendChild(checkbox);
                wrapper.appendChild(label);
                container.appendChild(wrapper);
            });
        }
        
        return container;
    }
    
    /**
     * Update multi-select filter value
     */
    updateMultiSelectFilter(key, config) {
        const checkboxes = this.sheet.querySelectorAll(`input[name="${key}[]"]:checked`);
        const values = Array.from(checkboxes).map(cb => cb.value);
        this.updateFilter(key, values);
    }
    
    /**
     * Create date filter
     */
    createDateFilter(key, config) {
        const input = document.createElement('input');
        input.type = 'date';
        input.name = key;
        input.className = 'mobile-form-input';
        input.value = config.value || '';
        input.addEventListener('change', (e) => {
            this.updateFilter(key, e.target.value);
        });
        
        return input;
    }
    
    /**
     * Create text filter
     */
    createTextFilter(key, config) {
        const input = document.createElement('input');
        input.type = 'text';
        input.name = key;
        input.className = 'mobile-form-input';
        input.placeholder = config.placeholder || '';
        input.value = config.value || '';
        input.addEventListener('input', (e) => {
            this.updateFilter(key, e.target.value);
        });
        
        return input;
    }
    
    /**
     * Update filter value
     */
    updateFilter(key, value) {
        if (this.filters[key]) {
            this.filters[key].value = value;
            this.updateFilterCount();
            
            if (this.onFilterChange) {
                this.onFilterChange(key, value, this.filters);
            }
        }
    }
    
    /**
     * Update active filter count
     */
    updateFilterCount() {
        let count = 0;
        Object.keys(this.filters).forEach(key => {
            const filter = this.filters[key];
            if (filter.value) {
                if (Array.isArray(filter.value) && filter.value.length > 0) {
                    count++;
                } else if (!Array.isArray(filter.value) && filter.value !== '' && filter.value !== false) {
                    count++;
                }
            }
        });
        
        this.filterCount = count;
        
        // Update badge if exists
        const badge = document.querySelector(`[data-filter-badge="${this.sheet.id}"]`);
        if (badge) {
            badge.textContent = count > 0 ? count : '';
            badge.style.display = count > 0 ? 'block' : 'none';
        }
    }
    
    /**
     * Get all filter values
     */
    getFilterValues() {
        const values = {};
        Object.keys(this.filters).forEach(key => {
            const filter = this.filters[key];
            if (filter.value) {
                if (Array.isArray(filter.value) && filter.value.length > 0) {
                    values[key] = filter.value;
                } else if (!Array.isArray(filter.value) && filter.value !== '' && filter.value !== false) {
                    values[key] = filter.value;
                }
            }
        });
        return values;
    }
    
    /**
     * Reset all filters
     */
    resetFilters() {
        Object.keys(this.filters).forEach(key => {
            const filter = this.filters[key];
            if (Array.isArray(filter.defaultValue)) {
                filter.value = [...filter.defaultValue];
            } else {
                filter.value = filter.defaultValue || (filter.type === 'checkbox' ? false : '');
            }
        });
        
        this.renderFilters();
        this.updateFilterCount();
    }
    
    /**
     * Override open to update filter count
     */
    open() {
        super.open();
        this.updateFilterCount();
    }
}

// Make it available globally
window.FilterSheet = FilterSheet;

