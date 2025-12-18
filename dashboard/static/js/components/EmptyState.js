/**
 * EmptyState Component
 * Consistent empty states across the app
 */

class EmptyState {
    constructor(options = {}) {
        this.icon = options.icon || '📭';
        this.title = options.title || 'No items found';
        this.message = options.message || '';
        this.actionLabel = options.actionLabel || null;
        this.onAction = options.onAction || null;
    }
    
    /**
     * Render empty state element
     */
    render() {
        const container = document.createElement('div');
        container.className = 'mobile-empty-state';
        
        // Icon
        if (this.icon) {
            const iconEl = document.createElement('div');
            iconEl.className = 'mobile-empty-state-icon';
            if (this.icon.startsWith('<svg') || this.icon.startsWith('<')) {
                iconEl.innerHTML = this.icon;
            } else {
                iconEl.textContent = this.icon;
                iconEl.style.fontSize = '48px';
            }
            container.appendChild(iconEl);
        }
        
        // Title
        const titleEl = document.createElement('h3');
        titleEl.className = 'mobile-empty-state-title';
        titleEl.textContent = this.title;
        container.appendChild(titleEl);
        
        // Message
        if (this.message) {
            const messageEl = document.createElement('p');
            messageEl.className = 'mobile-empty-state-message';
            messageEl.textContent = this.message;
            container.appendChild(messageEl);
        }
        
        // Action button
        if (this.actionLabel && this.onAction) {
            const actionBtn = document.createElement('button');
            actionBtn.className = 'btn btn-primary mobile-empty-state-action';
            actionBtn.textContent = this.actionLabel;
            actionBtn.addEventListener('click', this.onAction);
            container.appendChild(actionBtn);
        }
        
        return container;
    }
    
    /**
     * Static method to create and append empty state
     */
    static show(container, options = {}) {
        const emptyState = new EmptyState(options);
        const element = emptyState.render();
        
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        
        if (container) {
            container.innerHTML = '';
            container.appendChild(element);
        }
        
        return element;
    }
}

// Make it available globally
window.EmptyState = EmptyState;

