/**
 * Tag Management JavaScript
 * Provides reusable tag input, display, and filter components
 */

// Tag Input Component
class TagInput {
    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        this.options = {
            endpoint: options.endpoint || '/api/tags/autocomplete',
            placeholder: options.placeholder || 'Type to add tags...',
            allowCreate: options.allowCreate !== false,
            onTagsChange: options.onTagsChange || (() => {}),
            existingTags: options.existingTags || [],
            readOnly: options.readOnly || false
        };
        this.tags = [...this.options.existingTags];
        this.autocompleteCache = [];
        this.render();
    }

    render() {
        this.container.innerHTML = '';
        this.container.className = 'tag-input-container';
        
        // Render existing tags
        this.tags.forEach((tag, index) => {
            const tagEl = this.createTagElement(tag, index);
            this.container.appendChild(tagEl);
        });
        
        if (!this.options.readOnly) {
            // Input field
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'tag-input';
            input.placeholder = this.tags.length === 0 ? this.options.placeholder : '';
            input.addEventListener('keydown', (e) => this.handleKeyDown(e));
            input.addEventListener('input', (e) => this.handleInput(e));
            input.addEventListener('blur', () => {
                setTimeout(() => this.hideAutocomplete(), 200);
            });
            this.input = input;
            this.container.appendChild(input);
            
            // Autocomplete dropdown
            const autocomplete = document.createElement('div');
            autocomplete.className = 'tag-autocomplete';
            this.autocomplete = autocomplete;
            this.container.appendChild(autocomplete);
        }
    }

    createTagElement(tag, index) {
        const tagEl = document.createElement('span');
        tagEl.className = 'tag-chip';
        if (tag.color) {
            tagEl.style.backgroundColor = tag.color;
            tagEl.style.borderColor = tag.color;
            tagEl.classList.add('has-color');
        }
        if (tag.is_inherited) {
            tagEl.classList.add('tag-chip-inherited');
            tagEl.title = 'Inherited from property';
        }
        
        tagEl.textContent = tag.name;
        
        if (!this.options.readOnly && !tag.is_inherited) {
            const removeBtn = document.createElement('button');
            removeBtn.className = 'tag-chip-remove';
            removeBtn.innerHTML = '×';
            removeBtn.onclick = (e) => {
                e.stopPropagation();
                this.removeTag(index);
            };
            tagEl.appendChild(removeBtn);
        }
        
        return tagEl;
    }

    handleKeyDown(e) {
        if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            this.addTagFromInput();
        } else if (e.key === 'Backspace' && this.input.value === '' && this.tags.length > 0) {
            // Remove last tag if input is empty
            const lastTag = this.tags[this.tags.length - 1];
            if (!lastTag.is_inherited) {
                this.removeTag(this.tags.length - 1);
            }
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.selectAutocompleteItem(1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.selectAutocompleteItem(-1);
        }
    }

    handleInput(e) {
        const query = e.target.value.trim();
        if (query.length > 0) {
            this.showAutocomplete(query);
        } else {
            this.hideAutocomplete();
        }
    }

    async showAutocomplete(query) {
        try {
            const response = await fetch(`${this.options.endpoint}?q=${encodeURIComponent(query)}`);
            const suggestions = await response.json();
            
            this.autocompleteCache = suggestions;
            this.renderAutocomplete();
            this.autocomplete.classList.add('active');
        } catch (error) {
            console.error('Error fetching tag suggestions:', error);
            this.hideAutocomplete();
        }
    }

    renderAutocomplete() {
        this.autocomplete.innerHTML = '';
        const query = this.input.value.trim().toLowerCase();
        
        this.autocompleteCache.forEach((tag, index) => {
            const item = document.createElement('div');
            item.className = 'tag-autocomplete-item';
            if (index === 0) {
                item.classList.add('selected');
            }
            item.dataset.index = index;
            
            const nameSpan = document.createElement('span');
            nameSpan.textContent = tag.name;
            item.appendChild(nameSpan);
            
            item.onclick = () => this.selectTag(tag);
            this.autocomplete.appendChild(item);
        });
        
        // Add option to create new tag if not in suggestions
        if (this.options.allowCreate && query.length > 0) {
            const existing = this.autocompleteCache.some(t => t.name.toLowerCase() === query);
            if (!existing && !this.tags.some(t => t.name.toLowerCase() === query)) {
                const createItem = document.createElement('div');
                createItem.className = 'tag-autocomplete-item';
                
                const createText = document.createElement('span');
                createText.innerHTML = `<strong>Create "${query}"</strong>`;
                createItem.appendChild(createText);
                
                createItem.onclick = () => this.createTag(query);
                this.autocomplete.appendChild(createItem);
            }
        }
    }

    selectAutocompleteItem(direction) {
        const items = this.autocomplete.querySelectorAll('.tag-autocomplete-item');
        const current = this.autocomplete.querySelector('.tag-autocomplete-item.selected');
        let index = current ? Array.from(items).indexOf(current) : -1;
        
        index += direction;
        if (index < 0) index = items.length - 1;
        if (index >= items.length) index = 0;
        
        items.forEach(item => item.classList.remove('selected'));
        if (items[index]) {
            items[index].classList.add('selected');
            items[index].scrollIntoView({ block: 'nearest' });
        }
    }

    hideAutocomplete() {
        this.autocomplete.classList.remove('active');
    }

    addTagFromInput() {
        const value = this.input.value.trim();
        if (value) {
            // Check if it's a suggestion
            const selected = this.autocomplete.querySelector('.tag-autocomplete-item.selected');
            if (selected) {
                const index = selected.dataset.index;
                if (index !== undefined) {
                    this.selectTag(this.autocompleteCache[index]);
                } else {
                    this.createTag(value);
                }
            } else {
                this.createTag(value);
            }
        }
    }

    async selectTag(tag) {
        if (!this.tags.some(t => t.tag_id === tag.tag_id)) {
            this.tags.push(tag);
            this.render();
            this.options.onTagsChange(this.tags);
        }
        this.input.value = '';
        this.hideAutocomplete();
    }

    async createTag(name) {
        try {
            const response = await fetch('/api/tags', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            
            if (response.ok) {
                const tag = await response.json();
                this.tags.push(tag);
                this.render();
                this.options.onTagsChange(this.tags);
                this.input.value = '';
                this.hideAutocomplete();
            } else {
                const error = await response.json();
                alert(error.error || 'Failed to create tag');
            }
        } catch (error) {
            console.error('Error creating tag:', error);
            alert('Failed to create tag');
        }
    }

    removeTag(index) {
        this.tags.splice(index, 1);
        this.render();
        this.options.onTagsChange(this.tags);
    }

    getTags() {
        return this.tags;
    }

    setTags(tags) {
        this.tags = [...tags];
        this.render();
    }
}

// Tag Display Component
function renderTags(container, tags, options = {}) {
    const onRemove = options.onRemove || (() => {});
    const showInherited = options.showInherited !== false;
    
    container.innerHTML = '';
    container.className = 'tags-display';
    
    tags.forEach((tag, index) => {
        if (!showInherited && tag.is_inherited) return;
        
        const tagEl = document.createElement('span');
        tagEl.className = 'tag-chip';
        if (tag.color) {
            tagEl.style.backgroundColor = tag.color;
            tagEl.style.borderColor = tag.color;
            tagEl.classList.add('has-color');
        }
        if (tag.is_inherited) {
            tagEl.classList.add('tag-chip-inherited');
            tagEl.title = 'Inherited from property';
        }
        
        tagEl.textContent = tag.name;
        
        if (onRemove && !tag.is_inherited) {
            const removeBtn = document.createElement('button');
            removeBtn.className = 'tag-chip-remove';
            removeBtn.innerHTML = '×';
            removeBtn.onclick = () => onRemove(tag, index);
            tagEl.appendChild(removeBtn);
        }
        
        container.appendChild(tagEl);
    });
}

// Tag Filter Component - Redesigned
class TagFilter {
    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        if (!this.container) {
            console.error('TagFilter: Container element not found');
            return;
        }
        this.options = {
            endpoint: options.endpoint || '/api/tags',
            onFilterChange: options.onFilterChange || (() => {}),
            logic: options.logic || 'AND'
        };
        this.selectedTags = [];
        this.allTags = [];
        this.logic = this.options.logic;
        this.filteredTags = [];
        this.render();
        this.loadTags();
    }

    render() {
        if (!this.container) return;
        this.container.innerHTML = '';
        this.container.className = 'tag-filter';
        
        // Header with label
        const header = document.createElement('div');
        header.className = 'tag-filter-header';
        
        const label = document.createElement('label');
        label.textContent = 'Filter by Tags:';
        header.appendChild(label);
        
        // Selected tags display
        const selectedContainer = document.createElement('div');
        selectedContainer.className = 'tag-filter-selected';
        selectedContainer.id = 'tagFilterSelected';
        header.appendChild(selectedContainer);
        
        this.container.appendChild(header);
        
        // Input wrapper
        const inputWrapper = document.createElement('div');
        inputWrapper.className = 'tag-filter-input-wrapper';
        
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'tag-filter-input';
        input.placeholder = 'Search tags...';
        input.addEventListener('input', (e) => this.handleInput(e));
        input.addEventListener('keydown', (e) => this.handleKeyDown(e));
        input.addEventListener('focus', () => {
            this.updateFilteredTags();
            if (this.filteredTags.length > 0) {
                this.showAutocomplete();
            }
        });
        input.addEventListener('blur', () => {
            setTimeout(() => this.hideAutocomplete(), 200);
        });
        this.input = input;
        
        const inputIcon = document.createElement('div');
        inputIcon.className = 'tag-filter-input-icon';
        inputIcon.innerHTML = '🔍';
        inputWrapper.appendChild(input);
        inputWrapper.appendChild(inputIcon);
        
        // Autocomplete dropdown
        const autocomplete = document.createElement('div');
        autocomplete.className = 'tag-autocomplete';
        autocomplete.id = 'tagFilterAutocomplete';
        inputWrapper.appendChild(autocomplete);
        this.autocomplete = autocomplete;
        
        this.container.appendChild(inputWrapper);
        
        // Logic toggle and clear button
        const controls = document.createElement('div');
        controls.style.display = 'flex';
        controls.style.alignItems = 'center';
        controls.style.gap = '0.75rem';
        controls.style.marginTop = '0.75rem';
        controls.style.width = '100%';
        
        const logicContainer = document.createElement('div');
        logicContainer.className = 'tag-filter-logic';
        
        const logicLabel = document.createElement('span');
        logicLabel.className = 'tag-filter-logic-label';
        logicLabel.textContent = 'Match:';
        logicContainer.appendChild(logicLabel);
        
        const logicToggle = document.createElement('div');
        logicToggle.className = 'tag-filter-logic-toggle';
        
        const andBtn = document.createElement('button');
        andBtn.textContent = 'AND';
        andBtn.type = 'button';
        if (this.logic === 'AND') {
            andBtn.classList.add('active');
        }
        andBtn.onclick = () => this.setLogic('AND');
        
        const orBtn = document.createElement('button');
        orBtn.textContent = 'OR';
        orBtn.type = 'button';
        if (this.logic === 'OR') {
            orBtn.classList.add('active');
        }
        orBtn.onclick = () => this.setLogic('OR');
        
        logicToggle.appendChild(andBtn);
        logicToggle.appendChild(orBtn);
        logicContainer.appendChild(logicToggle);
        
        const clearBtn = document.createElement('button');
        clearBtn.className = 'tag-filter-clear';
        clearBtn.textContent = 'Clear All';
        clearBtn.type = 'button';
        clearBtn.onclick = () => this.clear();
        
        controls.appendChild(logicContainer);
        controls.appendChild(clearBtn);
        controls.style.marginLeft = 'auto';
        
        this.container.appendChild(controls);
        
        this.selectedContainer = selectedContainer;
        this.updateSelectedDisplay();
    }

    async loadTags() {
        if (!this.container) return;
        try {
            const response = await fetch(this.options.endpoint);
            if (!response.ok) {
                console.error('Error loading tags: HTTP', response.status);
                return;
            }
            const tags = await response.json();
            this.allTags = tags || [];
            // Initialize filtered tags (exclude already selected)
            this.updateFilteredTags();
        } catch (error) {
            console.error('Error loading tags:', error);
            // Don't block page load if tags fail to load
            this.allTags = [];
            this.filteredTags = [];
        }
    }

    updateFilteredTags() {
        const query = this.input ? this.input.value.trim().toLowerCase() : '';
        this.filteredTags = this.allTags.filter(tag => 
            !this.selectedTags.includes(tag.tag_id) &&
            (query.length === 0 || tag.name.toLowerCase().includes(query))
        );
    }

    handleInput(e) {
        this.updateFilteredTags();
        if (this.input.value.trim().length > 0 && this.filteredTags.length > 0) {
            this.showAutocomplete();
        } else {
            this.hideAutocomplete();
        }
    }

    handleKeyDown(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            const query = this.input.value.trim();
            if (query && this.filteredTags.length > 0) {
                const selected = this.autocomplete.querySelector('.tag-autocomplete-item.selected');
                if (selected) {
                    const tagId = parseInt(selected.dataset.tagId);
                    this.toggleTag(tagId);
                } else {
                    // Select first item
                    const firstItem = this.filteredTags[0];
                    if (firstItem) {
                        this.toggleTag(firstItem.tag_id);
                    }
                }
            }
            this.input.value = '';
            this.hideAutocomplete();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.selectAutocompleteItem(1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.selectAutocompleteItem(-1);
        } else if (e.key === 'Escape') {
            this.hideAutocomplete();
            this.input.blur();
        }
    }

    showAutocomplete() {
        if (!this.autocomplete || this.filteredTags.length === 0) {
            this.hideAutocomplete();
            return;
        }
        
        this.autocomplete.innerHTML = '';
        this.filteredTags.slice(0, 10).forEach((tag, index) => {
            const item = document.createElement('div');
            item.className = 'tag-autocomplete-item';
            item.dataset.tagId = tag.tag_id;
            if (index === 0) {
                item.classList.add('selected');
            }
            
            const nameSpan = document.createElement('span');
            nameSpan.textContent = tag.name;
            item.appendChild(nameSpan);
            
            if (tag.usage_count) {
                const countSpan = document.createElement('span');
                countSpan.style.marginLeft = 'auto';
                countSpan.style.color = '#9ca3af';
                countSpan.style.fontSize = '0.8125rem';
                countSpan.textContent = `(${tag.usage_count})`;
                item.appendChild(countSpan);
            }
            
            item.onclick = () => {
                this.toggleTag(tag.tag_id);
                this.input.value = '';
                this.hideAutocomplete();
            };
            
            this.autocomplete.appendChild(item);
        });
        
        this.autocomplete.classList.add('active');
    }

    hideAutocomplete() {
        if (this.autocomplete) {
            this.autocomplete.classList.remove('active');
        }
    }

    selectAutocompleteItem(direction) {
        const items = this.autocomplete.querySelectorAll('.tag-autocomplete-item');
        const current = this.autocomplete.querySelector('.tag-autocomplete-item.selected');
        let index = current ? Array.from(items).indexOf(current) : -1;
        
        index += direction;
        if (index < 0) index = items.length - 1;
        if (index >= items.length) index = 0;
        
        items.forEach(item => item.classList.remove('selected'));
        if (items[index]) {
            items[index].classList.add('selected');
            items[index].scrollIntoView({ block: 'nearest' });
        }
    }

    toggleTag(tagId) {
        const index = this.selectedTags.indexOf(tagId);
        if (index > -1) {
            this.selectedTags.splice(index, 1);
        } else {
            this.selectedTags.push(tagId);
        }
        this.updateFilteredTags();
        this.updateSelectedDisplay();
        this.options.onFilterChange(this.selectedTags, this.logic);
    }

    updateSelectedDisplay() {
        if (!this.selectedContainer) return;
        
        this.selectedContainer.innerHTML = '';
        
        if (this.selectedTags.length === 0) {
            const empty = document.createElement('span');
            empty.className = 'tags-empty';
            empty.textContent = 'No tags selected';
            this.selectedContainer.appendChild(empty);
            return;
        }
        
        this.selectedTags.forEach(tagId => {
            const tag = this.allTags.find(t => t.tag_id === tagId);
            if (!tag) return;
            
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            if (tag.color) {
                chip.style.backgroundColor = tag.color;
                chip.style.borderColor = tag.color;
                chip.classList.add('has-color');
            }
            
            chip.textContent = tag.name;
            
            const removeBtn = document.createElement('button');
            removeBtn.className = 'tag-chip-remove';
            removeBtn.innerHTML = '×';
            removeBtn.onclick = (e) => {
                e.stopPropagation();
                this.toggleTag(tagId);
            };
            chip.appendChild(removeBtn);
            
            this.selectedContainer.appendChild(chip);
        });
    }

    setLogic(logic) {
        this.logic = logic;
        // Update button states
        const logicToggle = this.container.querySelector('.tag-filter-logic-toggle');
        if (logicToggle) {
            const buttons = logicToggle.querySelectorAll('button');
            buttons.forEach(btn => {
                if (btn.textContent === logic) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
        }
        this.options.onFilterChange(this.selectedTags, this.logic);
    }

    getSelectedTags() {
        return this.selectedTags;
    }

    getLogic() {
        return this.logic;
    }

    clear() {
        this.selectedTags = [];
        this.updateFilteredTags();
        this.updateSelectedDisplay();
        if (this.input) {
            this.input.value = '';
        }
        this.hideAutocomplete();
        this.options.onFilterChange([], this.logic);
    }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TagInput, renderTags, TagFilter };
}

