(() => {
    const search = document.getElementById('guestIssueSearch');
    const source = document.getElementById('guestIssueSource');
    const empty = document.getElementById('guestIssueEmpty');
    const expandGroups = document.querySelector('[data-expand-groups]');
    const collapseGroups = document.querySelector('[data-collapse-groups]');
    const customWindowToggle = document.querySelector('[data-custom-window-toggle]');
    const customWindowForm = document.querySelector('[data-custom-window-form]');

    customWindowToggle?.addEventListener('click', () => {
        if (!customWindowForm) return;
        customWindowForm.hidden = false;
        document.querySelectorAll('.analysis-window-presets .is-active').forEach((control) => {
            control.classList.remove('is-active');
        });
        customWindowToggle.classList.add('is-active');
        customWindowToggle.setAttribute('aria-expanded', 'true');
        customWindowForm.querySelector('input[type="date"]')?.focus();
    });

    const applyFilters = () => {
        if (!search || !source) return;
        const query = search.value.trim().toLowerCase();
        let visibleUnits = 0;

        document.querySelectorAll('[data-portfolio-section]').forEach((portfolio) => {
            let portfolioUnits = 0;
            let portfolioIssues = 0;
            portfolio.querySelectorAll('[data-unit]').forEach((unit) => {
                const matchesSearch = !query || unit.dataset.search.includes(query);
                let visibleIssues = 0;
                unit.querySelectorAll('[data-issue]').forEach((issue) => {
                    const matchesSource = !source.value || issue.dataset.source === source.value;
                    const visible = matchesSearch && matchesSource;
                    issue.hidden = !visible;
                    if (visible) visibleIssues += 1;
                });
                unit.hidden = visibleIssues === 0;
                if (visibleIssues > 0) {
                    if (query || source.value) unit.open = true;
                    portfolioUnits += 1;
                    portfolioIssues += visibleIssues;
                    visibleUnits += 1;
                }
                const count = unit.querySelector('[data-unit-issue-count]');
                if (count) count.textContent = visibleIssues;
            });
            portfolio.hidden = portfolioUnits === 0;
            if (portfolioUnits > 0 && (query || source.value)) portfolio.open = true;
            const count = portfolio.querySelector('[data-portfolio-issue-count]');
            if (count) count.textContent = portfolioIssues;
        });

        if (empty) empty.hidden = visibleUnits > 0;
    };

    search?.addEventListener('input', applyFilters);
    source?.addEventListener('change', applyFilters);
    expandGroups?.addEventListener('click', () => {
        document.querySelectorAll('[data-portfolio-section], [data-unit]').forEach((group) => {
            if (!group.hidden) group.open = true;
        });
    });
    collapseGroups?.addEventListener('click', () => {
        document.querySelectorAll('[data-portfolio-section], [data-unit], [data-issue]').forEach((group) => {
            group.open = false;
        });
    });

    const dialog = document.getElementById('resolveIssueDialog');
    const form = document.getElementById('resolveIssueForm');
    const comment = document.getElementById('resolutionComment');
    const issueName = document.getElementById('resolveIssueName');
    const error = document.getElementById('resolveDialogError');
    const confirm = dialog?.querySelector('.dialog-confirm');
    let selectedIssueId = null;

    const adjustCount = (selector, delta) => {
        document.querySelectorAll(selector).forEach((counter) => {
            const current = Number.parseInt(counter.textContent, 10);
            if (Number.isFinite(current)) counter.textContent = Math.max(0, current + delta);
        });
    };

    const refreshPropertiesWithIssues = () => {
        const propertyCount = Array.from(document.querySelectorAll('[data-unit]')).filter((unit) => {
            return unit.querySelector('[data-issue]');
        }).length;
        document.querySelectorAll('[data-properties-with-issues]').forEach((counter) => {
            counter.textContent = propertyCount;
        });
    };

    const closeDialog = () => {
        if (!dialog) return;
        if (typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
    };

    document.querySelectorAll('[data-resolve-issue]').forEach((button) => {
        button.addEventListener('click', () => {
            selectedIssueId = button.dataset.resolveIssue;
            issueName.textContent = button.dataset.issueTitle || '';
            comment.value = '';
            error.hidden = true;
            if (typeof dialog.showModal === 'function') dialog.showModal();
            else dialog.setAttribute('open', '');
            window.setTimeout(() => comment.focus(), 50);
        });
    });

    dialog?.querySelector('[data-close-resolve]')?.addEventListener('click', closeDialog);
    dialog?.addEventListener('click', (event) => {
        if (event.target === dialog) closeDialog();
    });

    form?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const note = comment.value.trim();
        if (!note || !selectedIssueId) {
            error.textContent = 'Add a short note describing the resolution.';
            error.hidden = false;
            comment.focus();
            return;
        }

        error.hidden = true;
        confirm.disabled = true;
        confirm.classList.add('is-loading');
        try {
            const response = await fetch(`/workspace/guest-issues/api/issues/${selectedIssueId}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ comment: note })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'The issue could not be resolved.');

            const resolvedIssue = document.getElementById(`issue-${selectedIssueId}`);
            const visibleIssues = Array.from(document.querySelectorAll('[data-issue]')).filter((issue) => {
                return !issue.hidden && !issue.closest('[data-unit]')?.hidden && !issue.closest('[data-portfolio-section]')?.hidden;
            });
            const resolvedIndex = visibleIssues.indexOf(resolvedIssue);
            const continuationIssue = resolvedIndex >= 0
                ? (visibleIssues[resolvedIndex + 1] || visibleIssues[resolvedIndex - 1])
                : null;
            const scrollPosition = window.scrollY;

            resolvedIssue?.remove();
            adjustCount('[data-active-issue-count]', -1);
            adjustCount('[data-resolved-issue-count]', 1);
            adjustCount('[data-open-issue-count]', -1);
            applyFilters();
            refreshPropertiesWithIssues();

            confirm.disabled = false;
            confirm.classList.remove('is-loading');
            selectedIssueId = null;
            closeDialog();
            window.requestAnimationFrame(() => {
                window.scrollTo(0, scrollPosition);
                if (continuationIssue?.isConnected && !continuationIssue.hidden) {
                    continuationIssue.querySelector('.issue-card-summary')?.focus({ preventScroll: true });
                }
            });
        } catch (requestError) {
            error.textContent = requestError.message;
            error.hidden = false;
            confirm.disabled = false;
            confirm.classList.remove('is-loading');
        }
    });

    const focusedIssueId = new URLSearchParams(window.location.search).get('issue');
    if (focusedIssueId) {
        const focusedIssue = document.getElementById(`issue-${focusedIssueId}`);
        if (focusedIssue) {
            focusedIssue.open = true;
            const focusedUnit = focusedIssue.closest('[data-unit]');
            const focusedPortfolio = focusedIssue.closest('[data-portfolio-section]');
            if (focusedUnit) focusedUnit.open = true;
            if (focusedPortfolio) focusedPortfolio.open = true;
            window.setTimeout(() => {
                focusedIssue.scrollIntoView({ behavior: 'smooth', block: 'center' });
                focusedIssue.classList.add('issue-focus');
            }, 120);
        }
    }
})();
