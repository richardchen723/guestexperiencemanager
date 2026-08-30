/**
 * Mention autocomplete handler for textareas
 * Provides @mention functionality similar to GitHub/Slack
 */

class MentionHandler {
    constructor(textarea, options = {}) {
        this.textarea = textarea;
        this.options = {
            usersEndpoint: options.usersEndpoint || '/tickets/api/users',
            maxResults: options.maxResults || 10,
            dropdownClassName: options.dropdownClassName || '',
            ...options
        };
        
        this.users = [];
        this.currentMention = null;
        this.dropdown = null;
        this.selectedIndex = -1;
        
        this.init();
    }
    
    async init() {
        // Load users
        await this.loadUsers();
        
        // Create dropdown element
        this.createDropdown();
        
        // Attach event listeners
        this.attachListeners();
    }
    
    async loadUsers() {
        try {
            const response = await fetch(this.options.usersEndpoint);
            const users = await response.json();
            this.users = users.map(user => ({
                id: user.user_id,
                name: user.name || user.email,
                email: user.email,
                displayName: user.name ? `${user.name} (${user.email})` : user.email
            }));
        } catch (error) {
            console.error('Error loading users for mentions:', error);
            this.users = [];
        }
    }
    
    createDropdown() {
        this.dropdown = document.createElement('div');
        this.dropdown.className = ['mention-dropdown', this.options.dropdownClassName].filter(Boolean).join(' ');
        this.dropdown.id = `mentionDropdown-${MentionHandler.nextId++}`;
        this.dropdown.setAttribute('role', 'listbox');
        this.dropdown.setAttribute('aria-label', 'Team members');
        this.dropdown.style.display = 'none';
        document.body.appendChild(this.dropdown);

        this.textarea.setAttribute('aria-autocomplete', 'list');
        this.textarea.setAttribute('aria-haspopup', 'listbox');
        this.textarea.setAttribute('aria-controls', this.dropdown.id);
        this.textarea.setAttribute('aria-expanded', 'false');
    }
    
    attachListeners() {
        // Listen for @ character
        this.textarea.addEventListener('input', (e) => this.handleInput(e));
        this.textarea.addEventListener('keydown', (e) => this.handleKeyDown(e));
        
        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!this.textarea.contains(e.target) && !this.dropdown.contains(e.target)) {
                this.hideDropdown();
            }
        });
    }
    
    handleInput(e) {
        const value = this.textarea.value;
        const cursorPos = this.textarea.selectionStart;
        
        // Find @ mention at cursor position
        const textBeforeCursor = value.substring(0, cursorPos);
        // Match a mention only at the start of a line or after whitespace. The
        // query may contain spaces so full names (and "@ ") keep the picker open.
        const match = textBeforeCursor.match(/(^|[\s(])@([^\n@]*)$/);
        
        if (match) {
            const rawQuery = match[2];
            const query = rawQuery.trimStart().toLowerCase();
            const mentionStart = cursorPos - rawQuery.length - 1;
            
            this.currentMention = {
                start: mentionStart,
                end: cursorPos,
                query: query
            };
            
            this.showDropdown(query, mentionStart);
        } else {
            this.hideDropdown();
        }
    }
    
    handleKeyDown(e) {
        if (!this.dropdown || this.dropdown.style.display === 'none') {
            return;
        }
        
        const items = this.dropdown.querySelectorAll('.mention-item');
        if (items.length === 0) return;
        
        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                e.stopPropagation();
                this.selectedIndex = Math.min(this.selectedIndex + 1, items.length - 1);
                this.updateSelection(items);
                break;
                
            case 'ArrowUp':
                e.preventDefault();
                e.stopPropagation();
                this.selectedIndex = Math.max(this.selectedIndex - 1, -1);
                this.updateSelection(items);
                break;
                
            case 'Enter':
            case 'Tab':
                e.preventDefault();
                e.stopPropagation();
                if (this.selectedIndex >= 0 && items[this.selectedIndex]) {
                    this.selectUser(items[this.selectedIndex].dataset.userId);
                }
                break;
                
            case 'Escape':
                e.preventDefault();
                e.stopPropagation();
                this.hideDropdown();
                break;
        }
    }
    
    showDropdown(query, position) {
        // Filter users based on query
        const filtered = this.users.filter(user => {
            const nameMatch = user.name.toLowerCase().includes(query);
            const emailMatch = user.email.toLowerCase().includes(query);
            return nameMatch || emailMatch;
        }).slice(0, this.options.maxResults);
        
        if (filtered.length === 0) {
            this.hideDropdown();
            return;
        }
        
        // Build dropdown HTML
        this.dropdown.innerHTML = '';
        filtered.forEach((user, index) => {
            const item = document.createElement('div');
            item.className = 'mention-item';
            item.id = `${this.dropdown.id}-option-${index}`;
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', index === 0 ? 'true' : 'false');
            item.dataset.userId = user.id;
            item.dataset.userName = user.name;
            item.dataset.userEmail = user.email;
            
            if (index === 0) {
                item.classList.add('selected');
                this.selectedIndex = 0;
            }
            
            item.innerHTML = `
                <div class="mention-item-name">${this.escapeHtml(user.name)}</div>
                <div class="mention-item-email">${this.escapeHtml(user.email)}</div>
            `;
            
            item.addEventListener('click', () => this.selectUser(user.id));
            item.addEventListener('mousedown', event => event.preventDefault());
            item.addEventListener('mouseenter', () => {
                this.selectedIndex = index;
                this.updateSelection(this.dropdown.querySelectorAll('.mention-item'));
            });
            
            this.dropdown.appendChild(item);
        });
        
        this.dropdown.style.display = 'block';
        // Position after display so the measured height reflects the results.
        this.positionDropdown(position);
        this.textarea.setAttribute('aria-expanded', 'true');
        this.updateSelection(this.dropdown.querySelectorAll('.mention-item'));
    }
    
    positionDropdown(mentionStart) {
        // Get textarea position
        const rect = this.textarea.getBoundingClientRect();
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
        
        // Calculate position based on cursor
        // For simplicity, position below textarea
        this.dropdown.style.position = 'absolute';
        const dropdownHeight = Math.min(260, Math.max(92, this.dropdown.scrollHeight));
        const roomBelow = window.innerHeight - rect.bottom;
        const top = roomBelow >= dropdownHeight + 16
            ? rect.bottom + scrollTop + 6
            : Math.max(scrollTop + 8, rect.top + scrollTop - dropdownHeight - 6);
        this.dropdown.style.top = `${top}px`;
        this.dropdown.style.left = `${Math.max(scrollLeft + 8, rect.left + scrollLeft)}px`;
        this.dropdown.style.width = `${Math.min(Math.max(rect.width, 280), window.innerWidth - 32)}px`;
        this.dropdown.style.zIndex = '10000';
    }
    
    updateSelection(items) {
        items.forEach((item, index) => {
            if (index === this.selectedIndex) {
                item.classList.add('selected');
                item.setAttribute('aria-selected', 'true');
                this.textarea.setAttribute('aria-activedescendant', item.id);
                // Scroll into view
                item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            } else {
                item.classList.remove('selected');
                item.setAttribute('aria-selected', 'false');
            }
        });
    }
    
    selectUser(userId) {
        if (!this.currentMention) return;
        
        const user = this.users.find(u => u.id === parseInt(userId));
        if (!user) return;
        
        // Replace @mention with @username
        const value = this.textarea.value;
        const before = value.substring(0, this.currentMention.start);
        const after = value.substring(this.currentMention.end);
        const mentionText = `@${user.name}`;
        
        this.textarea.value = before + mentionText + ' ' + after;
        
        // Set cursor position after mention
        const newCursorPos = before.length + mentionText.length + 1;
        this.textarea.setSelectionRange(newCursorPos, newCursorPos);
        
        // Hide dropdown
        this.hideDropdown();
        
        // Trigger input event to update any listeners
        this.textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }
    
    hideDropdown() {
        if (this.dropdown) {
            this.dropdown.style.display = 'none';
        }
        this.textarea.setAttribute('aria-expanded', 'false');
        this.textarea.removeAttribute('aria-activedescendant');
        this.currentMention = null;
        this.selectedIndex = -1;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    destroy() {
        if (this.dropdown && this.dropdown.parentNode) {
            this.dropdown.parentNode.removeChild(this.dropdown);
        }
        // Note: Event listeners will be cleaned up when textarea is removed
    }
}

MentionHandler.nextId = 1;

// Initialize mention handlers for all textareas with data-mention attribute
document.addEventListener('DOMContentLoaded', function() {
    // Initialize on comment textarea
    const commentTextarea = document.getElementById('commentText');
    if (commentTextarea) {
        window.commentMentionHandler = new MentionHandler(commentTextarea);
    }
    
    // Initialize on description textarea
    const descriptionTextarea = document.getElementById('descriptionInput');
    if (descriptionTextarea) {
        window.descriptionMentionHandler = new MentionHandler(descriptionTextarea);
    }
});


