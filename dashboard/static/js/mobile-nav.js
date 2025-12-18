/**
 * Mobile Navigation System
 * Handles responsive navigation: bottom tab bar (mobile) and sidebar (desktop)
 */

class MobileNavigation {
    constructor() {
        this.currentView = this.detectViewport();
        this.isMobile = this.currentView === 'mobile';
        this.sidebarCollapsed = false;
        this.bottomNav = null;
        this.sidebar = null;
        this.moreMenu = null;
        
        // Navigation items configuration
        this.navItems = this.getNavItems();
        
        this.init();
    }
    
    /**
     * Detect if we're on mobile or desktop
     */
    detectViewport() {
        return window.innerWidth < 768 ? 'mobile' : 'desktop';
    }
    
    /**
     * Get navigation items based on user role
     */
    getNavItems() {
        // Check if user is admin (we'll need to get this from the page)
        const isAdmin = document.body.dataset.isAdmin === 'true' || 
                       document.querySelector('[data-is-admin="true"]') !== null;
        
        const primaryItems = [
            { id: 'dashboard', label: 'Dashboard', icon: 'dashboard', url: '/dashboard/' },
            { id: 'tickets', label: 'Tickets', icon: 'ticket', url: '/tickets/' },
            { id: 'reviews', label: 'Reviews', icon: 'review', url: '/reviews/' },
            { id: 'knowledge', label: 'Knowledge', icon: 'knowledge', url: '/knowledge/' },
            { id: 'more', label: 'More', icon: 'more', url: '#', hasSubmenu: true }
        ];
        
        const moreItems = [
            { id: 'properties', label: 'Properties', icon: 'property', url: '/properties' }
        ];
        
        if (isAdmin) {
            moreItems.push(
                { id: 'activities', label: 'Activities', icon: 'activity', url: '/admin/activities' },
                { id: 'sync', label: 'Sync', icon: 'sync', url: '/sync/history' },
                { id: 'admin', label: 'Admin', icon: 'admin', url: '/admin/users' }
            );
        } else {
            moreItems.push(
                { id: 'sync', label: 'Sync', icon: 'sync', url: '/sync/history' }
            );
        }
        
        return {
            primary: primaryItems,
            more: moreItems
        };
    }
    
    /**
     * Initialize navigation based on viewport
     */
    init() {
        if (this.isMobile) {
            this.renderBottomNav();
        } else {
            // Desktop: Keep existing top navigation, don't render sidebar
            this.adjustContentPadding(false);
        }
        
        this.setupResponsiveListener();
        this.setupActiveState();
    }
    
    /**
     * Render bottom tab bar for mobile
     */
    renderBottomNav() {
        // Remove existing sidebar if present
        const existingSidebar = document.getElementById('desktop-sidebar');
        if (existingSidebar) {
            existingSidebar.remove();
        }
        
        // Create bottom nav container
        this.bottomNav = document.createElement('nav');
        this.bottomNav.id = 'mobile-bottom-nav';
        this.bottomNav.className = 'mobile-bottom-nav';
        this.bottomNav.setAttribute('role', 'navigation');
        this.bottomNav.setAttribute('aria-label', 'Main navigation');
        
        // Create nav items
        const navList = document.createElement('div');
        navList.className = 'mobile-bottom-nav-list';
        
        this.navItems.primary.forEach(item => {
            const navItem = this.createBottomNavItem(item);
            navList.appendChild(navItem);
        });
        
        this.bottomNav.appendChild(navList);
        
        // Create more menu overlay
        this.createMoreMenu();
        
        // Append to body
        document.body.appendChild(this.bottomNav);
        
        // Add padding to main content to prevent overlap
        this.adjustContentPadding(true);
    }
    
    /**
     * Create a bottom nav item
     */
    createBottomNavItem(item) {
        const navItem = document.createElement('a');
        navItem.href = item.url;
        navItem.className = 'mobile-bottom-nav-item';
        navItem.setAttribute('data-nav-id', item.id);
        navItem.setAttribute('aria-label', item.label);
        
        if (item.hasSubmenu) {
            navItem.addEventListener('click', (e) => {
                e.preventDefault();
                this.toggleMoreMenu();
            });
        }
        
        // Icon
        const icon = document.createElement('div');
        icon.className = 'mobile-bottom-nav-icon';
        icon.innerHTML = this.getIconSVG(item.icon);
        navItem.appendChild(icon);
        
        // Label
        const label = document.createElement('span');
        label.className = 'mobile-bottom-nav-label';
        label.textContent = item.label;
        navItem.appendChild(label);
        
        // Badge (for notifications, if needed)
        if (item.badge) {
            const badge = document.createElement('span');
            badge.className = 'mobile-bottom-nav-badge';
            badge.textContent = item.badge;
            navItem.appendChild(badge);
        }
        
        return navItem;
    }
    
    /**
     * Create more menu overlay
     */
    createMoreMenu() {
        this.moreMenu = document.createElement('div');
        this.moreMenu.id = 'mobile-more-menu';
        this.moreMenu.className = 'mobile-more-menu';
        this.moreMenu.style.display = 'none';
        
        const overlay = document.createElement('div');
        overlay.className = 'mobile-more-menu-overlay';
        overlay.addEventListener('click', () => this.toggleMoreMenu());
        this.moreMenu.appendChild(overlay);
        
        const menuContent = document.createElement('div');
        menuContent.className = 'mobile-more-menu-content';
        
        const menuHeader = document.createElement('div');
        menuHeader.className = 'mobile-more-menu-header';
        menuHeader.innerHTML = `
            <h3>More</h3>
            <button class="mobile-more-menu-close" aria-label="Close menu">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                    <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                </svg>
            </button>
        `;
        menuHeader.querySelector('.mobile-more-menu-close').addEventListener('click', () => this.toggleMoreMenu());
        menuContent.appendChild(menuHeader);
        
        const menuList = document.createElement('div');
        menuList.className = 'mobile-more-menu-list';
        
        this.navItems.more.forEach(item => {
            const menuItem = document.createElement('a');
            menuItem.href = item.url;
            menuItem.className = 'mobile-more-menu-item';
            menuItem.innerHTML = `
                <div class="mobile-more-menu-icon">${this.getIconSVG(item.icon)}</div>
                <span>${item.label}</span>
            `;
            menuList.appendChild(menuItem);
        });
        
        menuContent.appendChild(menuList);
        this.moreMenu.appendChild(menuContent);
        document.body.appendChild(this.moreMenu);
    }
    
    /**
     * Toggle more menu
     */
    toggleMoreMenu() {
        if (!this.moreMenu) return;
        
        const isOpen = this.moreMenu.style.display !== 'none';
        this.moreMenu.style.display = isOpen ? 'none' : 'flex';
        
        // Lock/unlock body scroll
        document.body.style.overflow = isOpen ? '' : 'hidden';
    }
    
    /**
     * Desktop: Keep existing top navigation (no sidebar)
     */
    
    /**
     * Get icon SVG based on icon name
     */
    getIconSVG(iconName) {
        const icons = {
            dashboard: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                <rect x="14" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                <rect x="3" y="14" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                <rect x="14" y="14" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
            </svg>`,
            ticket: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M9 5H7C5.89543 5 5 5.89543 5 7V19C5 20.1046 5.89543 21 7 21H17C18.1046 21 19 20.1046 19 19V7C19 5.89543 18.1046 5 17 5H15M9 5C9 6.10457 9.89543 7 11 7H13C14.1046 7 15 6.10457 15 5M9 5C9 3.89543 9.89543 3 11 3H13C14.1046 3 15 3.89543 15 5M9 12H15M9 16H15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>`,
            review: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`,
            knowledge: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M4 19.5C4 18.6716 4.67157 18 5.5 18H20.5C21.3284 18 22 18.6716 22 19.5C22 20.3284 21.3284 21 20.5 21H5.5C4.67157 21 4 20.3284 4 19.5Z" stroke="currentColor" stroke-width="1.5"/>
                <path d="M6 18V9C6 7.34315 7.34315 6 9 6H15C16.6569 6 18 7.34315 18 9V18" stroke="currentColor" stroke-width="1.5"/>
            </svg>`,
            more: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="1" fill="currentColor"/>
                <circle cx="12" cy="5" r="1" fill="currentColor"/>
                <circle cx="12" cy="19" r="1" fill="currentColor"/>
            </svg>`,
            property: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M3 9L12 2L21 9V20C21 20.5523 20.5523 21 20 21H4C3.44772 21 3 20.5523 3 20V9Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                <path d="M9 21V12H15V21" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`,
            activity: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2Z" stroke="currentColor" stroke-width="1.5"/>
                <path d="M12 6V12L16 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>`,
            sync: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M3 12C3 7.02944 7.02944 3 12 3C16.9706 3 21 7.02944 21 12C21 16.9706 16.9706 21 12 21" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                <path d="M12 3V8M12 16V21M3 12H8M16 12H21" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>`,
            admin: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="8" r="3" stroke="currentColor" stroke-width="1.5"/>
                <path d="M6 21C6 17.6863 8.68629 15 12 15C15.3137 15 18 17.6863 18 21" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>`
        };
        
        return icons[iconName] || icons.more;
    }
    
    /**
     * Setup responsive listener to switch between mobile/desktop nav
     */
    setupResponsiveListener() {
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                const newView = this.detectViewport();
                if (newView !== this.currentView) {
                    this.currentView = newView;
                    this.isMobile = newView === 'mobile';
                    this.init();
                }
            }, 250);
        });
    }
    
    /**
     * Setup active state based on current URL
     */
    setupActiveState() {
        const currentPath = window.location.pathname;
        
        // Find matching nav item
        const allItems = [...this.navItems.primary, ...this.navItems.more];
        const activeItem = allItems.find(item => {
            if (item.url === '#') return false;
            return currentPath.startsWith(item.url) || 
                   (item.url === '/dashboard/' && currentPath === '/') ||
                   (item.url === '/' && currentPath === '/');
        });
        
        if (activeItem) {
            const navItems = document.querySelectorAll(`[data-nav-id="${activeItem.id}"]`);
            navItems.forEach(item => item.classList.add('active'));
        }
    }
    
    /**
     * Adjust content padding based on navigation type
     */
    adjustContentPadding(isMobile) {
        const main = document.querySelector('main');
        if (!main) return;
        
        if (isMobile) {
            main.style.paddingBottom = '80px'; // Space for bottom nav
            main.style.paddingLeft = '';
            main.style.paddingRight = '';
        } else {
            // Desktop: No sidebar, keep default padding
            main.style.paddingLeft = '';
            main.style.paddingBottom = '';
        }
    }
}

// Initialize navigation when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.mobileNav = new MobileNavigation();
    });
} else {
    window.mobileNav = new MobileNavigation();
}

