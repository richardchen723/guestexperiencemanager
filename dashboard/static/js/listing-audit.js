(function () {
    'use strict';

    const page = document.getElementById('listingAuditPage');
    if (!page) return;

    const cards = Array.from(page.querySelectorAll('.audit-property-card'));
    const searchInput = document.getElementById('auditSearch');
    const portfolioScope = document.getElementById('auditPortfolioScope');
    const severityFilter = document.getElementById('auditSeverityFilter');
    const visibleCount = document.getElementById('auditVisibleCount');
    const emptyState = document.getElementById('auditFilterEmpty');
    const channelPanels = Array.from(page.querySelectorAll('.channel-missing-panel'));
    const channelTriggers = Array.from(page.querySelectorAll('[data-channel-panel]'));

    function applyFilters() {
        const search = (searchInput?.value || '').trim().toLowerCase();
        const severity = severityFilter?.value || '';
        let visible = 0;

        cards.forEach(card => {
            const matchesSearch = !search || (card.dataset.search || '').includes(search);
            const matchesSeverity = !severity || card.dataset.severity === severity;
            const show = matchesSearch && matchesSeverity;
            card.hidden = !show;
            if (show) visible += 1;
        });

        if (visibleCount) visibleCount.textContent = `${visible} ${visible === 1 ? 'property' : 'properties'}`;
        if (emptyState) emptyState.hidden = visible !== 0;
    }

    [searchInput, severityFilter].forEach(control => {
        control?.addEventListener(control === searchInput ? 'input' : 'change', applyFilters);
    });

    portfolioScope?.addEventListener('change', () => {
        const url = new URL(window.location.href);
        if (portfolioScope.value) url.searchParams.set('portfolio', portfolioScope.value);
        else url.searchParams.delete('portfolio');
        window.location.assign(url.toString());
    });

    function closeChannelPanels(exceptId = '') {
        channelPanels.forEach(panel => {
            if (panel.id !== exceptId) panel.hidden = true;
        });
        channelTriggers.forEach(trigger => {
            trigger.setAttribute('aria-expanded', String(trigger.dataset.channelPanel === exceptId));
        });
    }

    channelTriggers.forEach(trigger => {
        trigger.addEventListener('click', () => {
            const panelId = trigger.dataset.channelPanel || '';
            const panel = document.getElementById(panelId);
            if (!panel) return;
            const shouldOpen = panel.hidden;
            closeChannelPanels(shouldOpen ? panelId : '');
            panel.hidden = !shouldOpen;
            if (shouldOpen && window.matchMedia('(max-width: 820px)').matches) {
                panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
    });

    page.querySelectorAll('[data-close-channel-panel]').forEach(button => {
        button.addEventListener('click', () => closeChannelPanels());
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeChannelPanels();
    });

    page.querySelectorAll('[data-audit-target]').forEach(button => {
        button.addEventListener('click', () => {
            if (searchInput) searchInput.value = '';
            if (severityFilter) severityFilter.value = '';
            applyFilters();
            closeChannelPanels();
            const card = document.getElementById(button.dataset.auditTarget || '');
            if (!card) return;
            card.open = true;
            card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            window.setTimeout(() => card.classList.add('audit-focus-pulse'), 350);
            window.setTimeout(() => card.classList.remove('audit-focus-pulse'), 1600);
        });
    });
})();
