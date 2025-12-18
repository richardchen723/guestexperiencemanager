/**
 * LoadingSkeleton Component
 * Skeleton loaders for better perceived performance
 */

class LoadingSkeleton {
    constructor(type = 'card', options = {}) {
        this.type = type;
        this.count = options.count || 1;
        this.options = options;
    }
    
    /**
     * Render skeleton element
     */
    render() {
        const container = document.createElement('div');
        container.className = 'loading-skeleton-container';
        
        for (let i = 0; i < this.count; i++) {
            const skeleton = this.createSkeleton();
            container.appendChild(skeleton);
        }
        
        return container;
    }
    
    /**
     * Create skeleton based on type
     */
    createSkeleton() {
        switch (this.type) {
            case 'card':
                return this.createCardSkeleton();
            case 'list':
                return this.createListSkeleton();
            case 'text':
                return this.createTextSkeleton();
            case 'avatar':
                return this.createAvatarSkeleton();
            default:
                return this.createCardSkeleton();
        }
    }
    
    /**
     * Create card skeleton
     */
    createCardSkeleton() {
        const card = document.createElement('div');
        card.className = 'mobile-skeleton mobile-skeleton-card';
        return card;
    }
    
    /**
     * Create list item skeleton
     */
    createListSkeleton() {
        const item = document.createElement('div');
        item.className = 'mobile-skeleton-list-item';
        item.innerHTML = `
            <div class="mobile-skeleton mobile-skeleton-avatar" style="width: 40px; height: 40px; border-radius: 50%; margin-right: 12px;"></div>
            <div style="flex: 1;">
                <div class="mobile-skeleton mobile-skeleton-title" style="width: 60%; margin-bottom: 8px;"></div>
                <div class="mobile-skeleton mobile-skeleton-text" style="width: 80%;"></div>
            </div>
        `;
        item.style.display = 'flex';
        item.style.alignItems = 'center';
        item.style.padding = 'var(--mobile-space-4)';
        item.style.marginBottom = 'var(--mobile-space-2)';
        return item;
    }
    
    /**
     * Create text skeleton
     */
    createTextSkeleton() {
        const text = document.createElement('div');
        text.className = 'mobile-skeleton mobile-skeleton-text';
        if (this.options.width) {
            text.style.width = this.options.width;
        }
        return text;
    }
    
    /**
     * Create avatar skeleton
     */
    createAvatarSkeleton() {
        const avatar = document.createElement('div');
        avatar.className = 'mobile-skeleton mobile-skeleton-avatar';
        return avatar;
    }
    
    /**
     * Static method to show skeleton in container
     */
    static show(container, type = 'card', options = {}) {
        const skeleton = new LoadingSkeleton(type, options);
        const element = skeleton.render();
        
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        
        if (container) {
            container.innerHTML = '';
            container.appendChild(element);
        }
        
        return element;
    }
    
    /**
     * Static method to hide skeleton
     */
    static hide(container) {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        
        if (container) {
            const skeleton = container.querySelector('.loading-skeleton-container');
            if (skeleton) {
                skeleton.remove();
            }
        }
    }
}

// Make it available globally
window.LoadingSkeleton = LoadingSkeleton;

