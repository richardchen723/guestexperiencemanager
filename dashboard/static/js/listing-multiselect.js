/**
 * Listing Multi-Select Component
 * Allows selecting multiple listings with a professional UI
 */

class ListingMultiSelect {
    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        if (!this.container) {
            console.error('ListingMultiSelect: Container element not found');
            return;
        }
        
        this.options = {
            listings: options.listings || [],
            selectedListingIds: options.selectedListingIds || [],
            onSelectionChange: options.onSelectionChange || (() => {}),
            placeholder: options.placeholder || 'Select properties...',
            allowGeneral: options.allowGeneral !== false  // Allow "General" option
        };
        
        this.selectedListingIds = new Set(this.options.selectedListingIds);
        this.render();
    }
    
    render() {
        this.container.innerHTML = '';
        this.container.className = 'listing-multiselect-container';
        
        // Selected listings display
        const selectedContainer = document.createElement('div');
        selectedContainer.className = 'listing-multiselect-selected';
        this.selectedContainer = selectedContainer;
        this.container.appendChild(selectedContainer);
        
        // Input wrapper
        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'listing-multiselect-input-wrapper';
        
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'listing-multiselect-input';
        input.placeholder = this.selectedListingIds.size === 0 ? this.options.placeholder : '';
        input.addEventListener('input', (e) => this.handleInput(e));
        input.addEventListener('keydown', (e) => this.handleKeyDown(e));
        input.addEventListener('focus', () => this.showDropdown());
        input.addEventListener('blur', () => {
            setTimeout(() => this.hideDropdown(), 200);
        });
        this.input = input;
        inputWrapper.appendChild(input);
        
        // Dropdown icon
        const dropdownIcon = document.createElement('div');
        dropdownIcon.className = 'listing-multiselect-icon';
        dropdownIcon.innerHTML = '▼';
        dropdownIcon.addEventListener('click', () => {
            if (this.dropdown.style.display === 'none') {
                this.showDropdown();
                this.input.focus();
            } else {
                this.hideDropdown();
            }
        });
        inputWrapper.appendChild(dropdownIcon);
        this.container.appendChild(inputWrapper);
        
        // Autocomplete dropdown
        const dropdown = document.createElement('div');
        dropdown.className = 'listing-multiselect-dropdown';
        dropdown.style.display = 'none';
        this.dropdown = dropdown;
        this.container.appendChild(dropdown);
        
        this.updateSelectedDisplay();
        this.updateFilteredListings();
    }
    
    handleInput(e) {
        const query = e.target.value.trim().toLowerCase();
        this.updateFilteredListings(query);
        if (query.length > 0 || this.filteredListings.length > 0) {
            this.showDropdown();
        } else {
            this.hideDropdown();
        }
    }
    
    handleKeyDown(e) {
        if (e.key === 'Escape') {
            this.hideDropdown();
            this.input.blur();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.selectDropdownItem(1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.selectDropdownItem(-1);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            const selected = this.dropdown.querySelector('.listing-multiselect-item.selected');
            if (selected) {
                selected.click();
            }
        } else if (e.key === 'Backspace' && this.input.value === '' && this.selectedListingIds.size > 0) {
            // Remove last selected listing if input is empty
            const lastId = Array.from(this.selectedListingIds).pop();
            this.toggleListing(lastId);
        }
    }
    
    updateFilteredListings(query = '') {
        const queryLower = query.toLowerCase();
        this.filteredListings = this.options.listings.filter(listing => {
            // Filter out already selected listings
            if (this.selectedListingIds.has(listing.listing_id)) {
                return false;
            }
            // Filter by query
            if (queryLower) {
                const name = (listing.internal_listing_name || listing.name || '').toLowerCase();
                return name.includes(queryLower);
            }
            return true;
        });
        this.renderDropdown();
    }
    
    renderDropdown() {
        this.dropdown.innerHTML = '';
        
        // Add "General" option if allowed and not already selected
        if (this.options.allowGeneral && !this.selectedListingIds.has(0)) {
            const generalItem = this.createDropdownItem({
                listing_id: 0,
                name: 'General (No specific property)',
                internal_listing_name: 'General'
            });
            this.dropdown.appendChild(generalItem);
        }
        
        // Add filtered listings
        this.filteredListings.slice(0, 10).forEach((listing, index) => {
            const item = this.createDropdownItem(listing, index === 0);
            this.dropdown.appendChild(item);
        });
        
        if (this.filteredListings.length === 0 && this.input.value.trim() === '') {
            const emptyItem = document.createElement('div');
            emptyItem.className = 'listing-multiselect-item-empty';
            emptyItem.textContent = 'No more listings available';
            this.dropdown.appendChild(emptyItem);
        }
    }
    
    createDropdownItem(listing, isSelected = false) {
        const item = document.createElement('div');
        item.className = 'listing-multiselect-item';
        if (isSelected) {
            item.classList.add('selected');
        }
        item.dataset.listingId = listing.listing_id;
        
        const displayName = listing.internal_listing_name || listing.name || `Listing ${listing.listing_id}`;
        item.textContent = displayName;
        
        item.addEventListener('click', () => {
            this.toggleListing(listing.listing_id);
            this.input.value = '';
            this.updateFilteredListings();
            this.hideDropdown();
        });
        
        item.addEventListener('mouseenter', () => {
            this.dropdown.querySelectorAll('.listing-multiselect-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
        });
        
        return item;
    }
    
    selectDropdownItem(direction) {
        const items = this.dropdown.querySelectorAll('.listing-multiselect-item');
        if (items.length === 0) return;
        
        const currentSelected = this.dropdown.querySelector('.listing-multiselect-item.selected');
        let currentIndex = currentSelected ? Array.from(items).indexOf(currentSelected) : -1;
        
        currentIndex += direction;
        if (currentIndex < 0) currentIndex = items.length - 1;
        if (currentIndex >= items.length) currentIndex = 0;
        
        items.forEach(item => item.classList.remove('selected'));
        items[currentIndex].classList.add('selected');
        items[currentIndex].scrollIntoView({ block: 'nearest' });
    }
    
    toggleListing(listingId) {
        if (this.selectedListingIds.has(listingId)) {
            this.selectedListingIds.delete(listingId);
        } else {
            this.selectedListingIds.add(listingId);
        }
        this.updateSelectedDisplay();
        this.options.onSelectionChange(Array.from(this.selectedListingIds));
    }
    
    updateSelectedDisplay() {
        this.selectedContainer.innerHTML = '';
        
        if (this.selectedListingIds.size === 0) {
            this.input.placeholder = this.options.placeholder;
            return;
        }
        
        this.input.placeholder = '';
        
        Array.from(this.selectedListingIds).forEach(listingId => {
            const listing = this.options.listings.find(l => l.listing_id === listingId);
            const chip = document.createElement('span');
            chip.className = 'listing-multiselect-chip';
            
            if (listingId === 0) {
                chip.textContent = 'General';
            } else if (listing) {
                const displayName = listing.internal_listing_name || listing.name || `Listing ${listingId}`;
                chip.textContent = displayName;
            } else {
                chip.textContent = `Listing ${listingId}`;
            }
            
            const removeBtn = document.createElement('button');
            removeBtn.className = 'listing-multiselect-chip-remove';
            removeBtn.innerHTML = '×';
            removeBtn.onclick = (e) => {
                e.stopPropagation();
                this.toggleListing(listingId);
            };
            
            chip.appendChild(removeBtn);
            this.selectedContainer.appendChild(chip);
        });
    }
    
    showDropdown() {
        this.updateFilteredListings(this.input.value.trim());
        this.dropdown.style.display = 'block';
    }
    
    hideDropdown() {
        this.dropdown.style.display = 'none';
    }
    
    getSelectedListingIds() {
        return Array.from(this.selectedListingIds);
    }
    
    setSelectedListingIds(listingIds) {
        this.selectedListingIds = new Set(listingIds);
        this.updateSelectedDisplay();
        this.updateFilteredListings();
    }
    
    setListings(listings) {
        this.options.listings = listings;
        this.updateFilteredListings();
    }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ListingMultiSelect };
}

