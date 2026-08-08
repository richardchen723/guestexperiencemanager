(function () {
    'use strict';

    const body = document.body;
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('appSidebar');
    const userMenu = document.getElementById('userMenu');
    const userMenuButton = document.getElementById('userMenuButton');
    const userMenuDropdown = document.getElementById('userMenuDropdown');
    const collapseStorageKey = 'hostaway-sidebar-collapsed';

    function setSidebarCollapsed(collapsed, persist) {
        body.classList.toggle('sidebar-collapsed', collapsed);

        if (sidebarToggle) {
            sidebarToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            sidebarToggle.setAttribute(
                'aria-label',
                collapsed ? 'Expand navigation' : 'Collapse navigation'
            );
        }

        if (sidebar) {
            sidebar.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
        }

        if (persist) {
            try {
                window.localStorage.setItem(collapseStorageKey, collapsed ? 'true' : 'false');
            } catch (error) {
                // The shell still works when storage is unavailable.
            }
        }
    }

    if (sidebarToggle && sidebar) {
        let savedCollapseState = false;
        try {
            savedCollapseState = window.localStorage.getItem(collapseStorageKey) === 'true';
        } catch (error) {
            savedCollapseState = false;
        }

        setSidebarCollapsed(savedCollapseState, false);
        sidebarToggle.addEventListener('click', function () {
            setSidebarCollapsed(!body.classList.contains('sidebar-collapsed'), true);
        });
    }

    function closeUserMenu(options) {
        if (!userMenuButton || !userMenuDropdown || userMenuDropdown.hidden) return;
        userMenuDropdown.hidden = true;
        userMenuButton.setAttribute('aria-expanded', 'false');
        userMenu.classList.remove('is-open');

        if (options && options.restoreFocus) {
            userMenuButton.focus();
        }
    }

    function openUserMenu() {
        if (!userMenuButton || !userMenuDropdown) return;
        userMenuDropdown.hidden = false;
        userMenuButton.setAttribute('aria-expanded', 'true');
        userMenu.classList.add('is-open');

        const firstMenuItem = userMenuDropdown.querySelector('[role="menuitem"]');
        if (firstMenuItem) firstMenuItem.focus();
    }

    if (userMenuButton && userMenuDropdown && userMenu) {
        userMenuButton.addEventListener('click', function () {
            if (userMenuDropdown.hidden) {
                openUserMenu();
            } else {
                closeUserMenu({ restoreFocus: false });
            }
        });

        userMenu.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeUserMenu({ restoreFocus: true });
                return;
            }

            if (!['ArrowDown', 'ArrowUp'].includes(event.key) || userMenuDropdown.hidden) return;

            event.preventDefault();
            const menuItems = Array.from(userMenuDropdown.querySelectorAll('[role="menuitem"]'));
            const currentIndex = menuItems.indexOf(document.activeElement);
            const step = event.key === 'ArrowDown' ? 1 : -1;
            const nextIndex = currentIndex < 0
                ? 0
                : (currentIndex + step + menuItems.length) % menuItems.length;
            menuItems[nextIndex].focus();
        });

        document.addEventListener('click', function (event) {
            if (!userMenu.contains(event.target)) {
                closeUserMenu({ restoreFocus: false });
            }
        });
    }
})();
