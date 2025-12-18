/**
 * BottomSheet Component
 * Reusable bottom sheet modal for mobile interactions
 */

class BottomSheet {
    constructor(options = {}) {
        this.title = options.title || '';
        this.content = options.content || '';
        this.onClose = options.onClose || null;
        this.onApply = options.onApply || null;
        this.onCancel = options.onCancel || null;
        this.showBackdrop = options.showBackdrop !== false;
        this.closeOnBackdrop = options.closeOnBackdrop !== false;
        this.maxHeight = options.maxHeight || '80vh';
        this.sheet = null;
        this.isOpen = false;
        
        this.createSheet();
    }
    
    /**
     * Create the bottom sheet DOM structure
     */
    createSheet() {
        this.sheet = document.createElement('div');
        this.sheet.className = 'bottom-sheet';
        this.sheet.setAttribute('role', 'dialog');
        this.sheet.setAttribute('aria-modal', 'true');
        this.sheet.setAttribute('aria-labelledby', 'bottom-sheet-title');
        this.sheet.style.display = 'none';
        
        // Overlay/backdrop
        if (this.showBackdrop) {
            const overlay = document.createElement('div');
            overlay.className = 'bottom-sheet-overlay';
            if (this.closeOnBackdrop) {
                overlay.addEventListener('click', () => this.close());
            }
            this.sheet.appendChild(overlay);
        }
        
        // Sheet content container
        const contentContainer = document.createElement('div');
        contentContainer.className = 'bottom-sheet-content';
        contentContainer.style.maxHeight = this.maxHeight;
        
        // Header
        if (this.title) {
            const header = document.createElement('div');
            header.className = 'bottom-sheet-header';
            header.innerHTML = `
                <h3 id="bottom-sheet-title" class="bottom-sheet-title">${this.escapeHtml(this.title)}</h3>
                <button class="bottom-sheet-close" aria-label="Close" type="button">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                        <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                    </svg>
                </button>
            `;
            header.querySelector('.bottom-sheet-close').addEventListener('click', () => this.close());
            contentContainer.appendChild(header);
        }
        
        // Body (scrollable content area)
        const body = document.createElement('div');
        body.className = 'bottom-sheet-body';
        if (typeof this.content === 'string') {
            body.innerHTML = this.content;
        } else if (this.content instanceof HTMLElement) {
            body.appendChild(this.content);
        }
        contentContainer.appendChild(body);
        
        // Footer (if actions provided)
        if (this.onApply || this.onCancel) {
            const footer = document.createElement('div');
            footer.className = 'bottom-sheet-footer';
            
            if (this.onCancel) {
                const cancelBtn = document.createElement('button');
                cancelBtn.type = 'button';
                cancelBtn.className = 'btn btn-secondary bottom-sheet-cancel';
                cancelBtn.textContent = 'Cancel';
                cancelBtn.addEventListener('click', () => {
                    if (this.onCancel) this.onCancel();
                    this.close();
                });
                footer.appendChild(cancelBtn);
            }
            
            if (this.onApply) {
                const applyBtn = document.createElement('button');
                applyBtn.type = 'button';
                applyBtn.className = 'btn btn-primary bottom-sheet-apply';
                applyBtn.textContent = 'Apply';
                applyBtn.addEventListener('click', () => {
                    if (this.onApply) this.onApply();
                });
                footer.appendChild(applyBtn);
            }
            
            contentContainer.appendChild(footer);
        }
        
        this.sheet.appendChild(contentContainer);
        document.body.appendChild(this.sheet);
        
        // Close on Escape key
        this.setupKeyboardListeners();
    }
    
    /**
     * Setup keyboard event listeners
     */
    setupKeyboardListeners() {
        this.keyboardHandler = (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this.close();
            }
        };
        document.addEventListener('keydown', this.keyboardHandler);
    }
    
    /**
     * Open the bottom sheet
     */
    open() {
        if (this.isOpen) return;
        
        this.isOpen = true;
        this.sheet.style.display = 'flex';
        
        // Prevent body scroll
        document.body.style.overflow = 'hidden';
        
        // Trigger animation
        requestAnimationFrame(() => {
            this.sheet.classList.add('open');
        });
        
        // Focus management
        const firstFocusable = this.sheet.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (firstFocusable) {
            firstFocusable.focus();
        }
    }
    
    /**
     * Close the bottom sheet
     */
    close() {
        if (!this.isOpen) return;
        
        this.isOpen = false;
        this.sheet.classList.remove('open');
        
        // Wait for animation to complete
        setTimeout(() => {
            this.sheet.style.display = 'none';
            document.body.style.overflow = '';
            
            if (this.onClose) {
                this.onClose();
            }
        }, 300); // Match CSS transition duration
    }
    
    /**
     * Update sheet content
     */
    setContent(content) {
        const body = this.sheet.querySelector('.bottom-sheet-body');
        if (!body) return;
        
        if (typeof content === 'string') {
            body.innerHTML = content;
        } else if (content instanceof HTMLElement) {
            body.innerHTML = '';
            body.appendChild(content);
        }
    }
    
    /**
     * Update sheet title
     */
    setTitle(title) {
        const titleEl = this.sheet.querySelector('.bottom-sheet-title');
        if (titleEl) {
            titleEl.textContent = title;
        }
    }
    
    /**
     * Destroy the bottom sheet
     */
    destroy() {
        if (this.keyboardHandler) {
            document.removeEventListener('keydown', this.keyboardHandler);
        }
        if (this.sheet && this.sheet.parentNode) {
            this.sheet.parentNode.removeChild(this.sheet);
        }
        document.body.style.overflow = '';
    }
    
    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Make it available globally
window.BottomSheet = BottomSheet;

