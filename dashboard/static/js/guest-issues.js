(() => {
    const search = document.getElementById('guestIssueSearch');
    const source = document.getElementById('guestIssueSource');
    const status = document.getElementById('guestIssueStatus');
    const priority = document.getElementById('guestIssuePriority');
    const sort = document.getElementById('guestIssueSort');
    const reportedFrom = document.getElementById('guestIssueReportedFrom');
    const reportedTo = document.getElementById('guestIssueReportedTo');
    const reportedError = document.getElementById('guestIssueReportedError');
    const reportedCustom = document.querySelector('[data-reported-custom]');
    const reportedCustomToggle = document.querySelector('[data-reported-custom-toggle]');
    const resetFilters = document.querySelector('[data-reset-issue-filters]');
    const empty = document.getElementById('guestIssueEmpty');
    const expandGroups = document.querySelector('[data-expand-groups]');
    const collapseGroups = document.querySelector('[data-collapse-groups]');
    const customWindowToggle = document.querySelector('[data-custom-window-toggle]');
    const customWindowForm = document.querySelector('[data-custom-window-form]');
    const priorityRank = { critical: 0, high: 1, medium: 2, low: 3 };
    let reportedPreset = '';

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

    const renderStatusCounts = (counts) => {
        if (!status) return;
        Array.from(status.options).forEach((option) => {
            const label = option.dataset.statusLabel;
            if (!label) return;
            const count = option.value ? (counts[option.value] || 0) : (counts.all || 0);
            option.dataset.statusCount = count;
            option.textContent = `${label} (${count})`;
        });
    };

    const renderPriorityCounts = (counts) => {
        if (!priority) return;
        Array.from(priority.options).forEach((option) => {
            const label = option.dataset.priorityLabel;
            if (!label) return;
            const count = option.value ? (counts[option.value] || 0) : (counts.all || 0);
            option.dataset.priorityCount = count;
            option.textContent = `${label} (${count})`;
        });
    };

    const parseReportedAt = (value) => {
        const reportedAt = value ? new Date(value) : null;
        return reportedAt && !Number.isNaN(reportedAt.getTime()) ? reportedAt : null;
    };

    const localDateBoundary = (value, endOfDay = false) => {
        const parts = String(value || '').split('-').map(Number);
        if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return null;
        return new Date(
            parts[0], parts[1] - 1, parts[2],
            endOfDay ? 23 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 999 : 0
        );
    };

    const reportedRange = () => {
        const now = new Date();
        let start = null;
        let end = null;
        let invalid = false;

        if (reportedPreset === 'today') {
            start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            end = now;
        } else if (reportedPreset === '24h') {
            start = new Date(now.getTime() - (24 * 60 * 60 * 1000));
            end = now;
        } else if (reportedPreset === '7d' || reportedPreset === '30d') {
            const days = reportedPreset === '7d' ? 7 : 30;
            start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            start.setDate(start.getDate() - (days - 1));
            end = now;
        } else if (reportedPreset === 'custom') {
            start = localDateBoundary(reportedFrom?.value);
            end = localDateBoundary(reportedTo?.value, true);
            invalid = Boolean(start && end && end < start);
        }

        if (reportedError) {
            reportedError.textContent = invalid
                ? 'To date must be on or after From date.'
                : '';
            reportedError.hidden = !invalid;
        }
        return { start, end, invalid };
    };

    const setReportedPreset = (preset) => {
        reportedPreset = preset;
        document.querySelectorAll('[data-reported-preset]').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.reportedPreset === preset);
        });
        if (reportedCustomToggle) {
            const customActive = preset === 'custom';
            reportedCustomToggle.classList.toggle('is-active', customActive);
            reportedCustomToggle.setAttribute('aria-expanded', String(customActive));
            if (reportedCustom) reportedCustom.hidden = !customActive;
        }
    };

    const localizeReportedTimes = () => {
        document.querySelectorAll('[data-reported-time]').forEach((timeElement) => {
            const reportedAt = parseReportedAt(timeElement.getAttribute('datetime'));
            if (!reportedAt) return;
            timeElement.textContent = reportedAt.toLocaleString('en-US', {
                month: 'short', day: 'numeric', year: 'numeric'
            });
        });
    };

    const sortIssueCards = () => {
        if (!sort) return;
        document.querySelectorAll('.issue-card-list').forEach((list) => {
            const cards = Array.from(list.querySelectorAll(':scope > [data-issue]'));
            cards.sort((left, right) => {
                const leftPriority = priorityRank[left.dataset.priority] ?? 99;
                const rightPriority = priorityRank[right.dataset.priority] ?? 99;
                const leftReported = parseReportedAt(left.dataset.reportedAt)?.getTime() || 0;
                const rightReported = parseReportedAt(right.dataset.reportedAt)?.getTime() || 0;
                const originalOrder = Number(left.dataset.originalOrder || 0)
                    - Number(right.dataset.originalOrder || 0);
                if (sort.value === 'priority_asc') {
                    return rightPriority - leftPriority || rightReported - leftReported || originalOrder;
                }
                if (sort.value === 'reported_desc') {
                    return rightReported - leftReported || originalOrder;
                }
                if (sort.value === 'reported_asc') {
                    return leftReported - rightReported || originalOrder;
                }
                return leftPriority - rightPriority || rightReported - leftReported || originalOrder;
            });
            cards.forEach((card) => list.append(card));
        });
    };

    const applyFilters = () => {
        if (!search || !source || !status || !priority || !sort) return;
        sortIssueCards();
        const query = search.value.trim().toLowerCase();
        const statusCounts = { all: 0 };
        const priorityCounts = { all: 0 };
        const range = reportedRange();
        const hasReportedFilter = Boolean(
            reportedPreset && (
                reportedPreset !== 'custom' || reportedFrom?.value || reportedTo?.value
            )
        );
        const filtersActive = Boolean(
            query || source.value || status.value || priority.value || hasReportedFilter
        );
        let visibleUnits = 0;

        document.querySelectorAll('[data-portfolio-section]').forEach((portfolio) => {
            let portfolioUnits = 0;
            let portfolioIssues = 0;
            portfolio.querySelectorAll('[data-unit]').forEach((unit) => {
                const matchesSearch = !query || unit.dataset.search.includes(query);
                let visibleIssues = 0;
                unit.querySelectorAll('[data-issue]').forEach((issue) => {
                    const matchesSource = !source.value || issue.dataset.source === source.value;
                    const reportedAt = parseReportedAt(issue.dataset.reportedAt);
                    const matchesReported = range.invalid || !hasReportedFilter || Boolean(
                        reportedAt
                        && (!range.start || reportedAt >= range.start)
                        && (!range.end || reportedAt <= range.end)
                    );
                    const matchesScope = matchesSearch && matchesSource && matchesReported;
                    const matchesStatus = !status.value || issue.dataset.status === status.value;
                    const matchesPriority = !priority.value || issue.dataset.priority === priority.value;
                    const visible = matchesScope && matchesStatus && matchesPriority;
                    if (matchesScope && matchesPriority) {
                        statusCounts.all += 1;
                        statusCounts[issue.dataset.status] = (statusCounts[issue.dataset.status] || 0) + 1;
                    }
                    if (matchesScope && matchesStatus) {
                        priorityCounts.all += 1;
                        priorityCounts[issue.dataset.priority] = (priorityCounts[issue.dataset.priority] || 0) + 1;
                    }
                    issue.hidden = !visible;
                    if (visible) visibleIssues += 1;
                });
                unit.hidden = visibleIssues === 0;
                if (visibleIssues > 0) {
                    if (filtersActive) unit.open = true;
                    portfolioUnits += 1;
                    portfolioIssues += visibleIssues;
                    visibleUnits += 1;
                }
                const count = unit.querySelector('[data-unit-issue-count]');
                if (count) count.textContent = visibleIssues;
            });
            portfolio.hidden = portfolioUnits === 0;
            if (portfolioUnits > 0 && filtersActive) portfolio.open = true;
            const count = portfolio.querySelector('[data-portfolio-issue-count]');
            if (count) count.textContent = portfolioIssues;
        });

        renderStatusCounts(statusCounts);
        renderPriorityCounts(priorityCounts);
        if (resetFilters) {
            resetFilters.disabled = !Boolean(
                filtersActive || sort.value !== 'priority_desc'
            );
        }
        if (empty) empty.hidden = visibleUnits > 0;
    };

    search?.addEventListener('input', applyFilters);
    source?.addEventListener('change', applyFilters);
    status?.addEventListener('change', applyFilters);
    priority?.addEventListener('change', applyFilters);
    sort?.addEventListener('change', applyFilters);
    document.querySelectorAll('[data-reported-preset]').forEach((button) => {
        button.addEventListener('click', () => {
            setReportedPreset(button.dataset.reportedPreset || '');
            applyFilters();
        });
    });
    reportedCustomToggle?.addEventListener('click', () => {
        setReportedPreset(reportedPreset === 'custom' ? '' : 'custom');
        applyFilters();
        if (reportedPreset === 'custom') reportedFrom?.focus();
    });
    [reportedFrom, reportedTo].forEach((input) => {
        input?.addEventListener('change', () => {
            setReportedPreset('custom');
            applyFilters();
        });
    });
    document.querySelector('[data-clear-reported]')?.addEventListener('click', () => {
        if (reportedFrom) reportedFrom.value = '';
        if (reportedTo) reportedTo.value = '';
        setReportedPreset('');
        applyFilters();
    });
    resetFilters?.addEventListener('click', () => {
        search.value = '';
        source.value = '';
        status.value = '';
        priority.value = '';
        sort.value = 'priority_desc';
        if (reportedFrom) reportedFrom.value = '';
        if (reportedTo) reportedTo.value = '';
        setReportedPreset('');
        applyFilters();
    });
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

    localizeReportedTimes();
    applyFilters();

    const dialog = document.getElementById('resolveIssueDialog');
    const form = document.getElementById('resolveIssueForm');
    const comment = document.getElementById('resolutionComment');
    const issueName = document.getElementById('resolveIssueName');
    const error = document.getElementById('resolveDialogError');
    const confirm = dialog?.querySelector('.dialog-confirm');
    let selectedIssueId = null;

    const requestJson = async (url, options) => {
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
        return result;
    };

    const appendIssueNote = (issue, note) => {
        if (!issue || !note) return;
        const list = issue.querySelector('[data-note-list]');
        if (!list) return;
        list.querySelector('[data-note-empty]')?.remove();

        const article = document.createElement('article');
        article.className = 'issue-note';
        article.dataset.note = '';
        const heading = document.createElement('div');
        const type = document.createElement('strong');
        type.textContent = note.note_type_label || 'Note';
        const metadata = document.createElement('span');
        const timestamp = note.created_at ? new Date(note.created_at) : null;
        const formattedTime = timestamp && !Number.isNaN(timestamp.getTime())
            ? timestamp.toLocaleString('en-US', {
                month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
            })
            : 'Just now';
        metadata.textContent = `${note.author_name || 'Team member'} · ${formattedTime}`;
        heading.append(type, metadata);
        const body = document.createElement('p');
        body.textContent = note.body || '';
        article.append(heading, body);
        list.append(article);

        const counter = issue.querySelector('[data-note-count]');
        if (counter) {
            const current = Number.parseInt(counter.textContent, 10) || 0;
            counter.textContent = current + 1;
        }
        const preview = issue.querySelector('[data-note-preview]');
        if (preview) preview.textContent = note.body || 'New activity';
        list.scrollTop = list.scrollHeight;
    };

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

    const openResolveDialog = (issueId, title) => {
        selectedIssueId = issueId;
        issueName.textContent = title || '';
        comment.value = '';
        error.hidden = true;
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        window.setTimeout(() => comment.focus(), 50);
    };

    document.querySelectorAll('[data-resolve-issue]').forEach((button) => {
        button.addEventListener('click', () => {
            openResolveDialog(button.dataset.resolveIssue, button.dataset.issueTitle);
        });
    });

    document.querySelectorAll('[data-issue-priority]').forEach((select) => {
        select.addEventListener('change', async () => {
            const previous = select.dataset.currentPriority || 'Medium';
            const next = select.value;
            const issue = select.closest('[data-issue]');
            const priorityError = issue?.querySelector('[data-priority-error]');

            if (priorityError) priorityError.hidden = true;
            select.disabled = true;
            try {
                const result = await requestJson(
                    `/workspace/guest-issues/api/issues/${select.dataset.issuePriority}/priority`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ priority: next })
                    }
                );
                select.dataset.currentPriority = result.priority;
                select.value = result.priority;
                if (issue) issue.dataset.priority = result.priority_key;
                const pill = issue?.querySelector('[data-priority-pill]');
                if (pill) {
                    pill.className = `issue-priority-pill priority-${result.priority_key}`;
                    pill.innerHTML = '<i aria-hidden="true"></i>';
                    pill.append(document.createTextNode(result.priority));
                }
                const audit = issue?.querySelector('[data-priority-audit]');
                if (audit) {
                    const timestamp = result.priority_updated_at
                        ? new Date(result.priority_updated_at)
                        : null;
                    const formattedTime = timestamp && !Number.isNaN(timestamp.getTime())
                        ? timestamp.toLocaleString('en-US', {
                            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
                        })
                        : 'Just now';
                    audit.textContent = `Priority set by ${result.priority_updated_by_name || 'Team member'} · ${formattedTime}`;
                }
                appendIssueNote(issue, result.note);
                applyFilters();
            } catch (requestError) {
                select.value = previous;
                if (priorityError) {
                    priorityError.textContent = requestError.message;
                    priorityError.hidden = false;
                }
            } finally {
                select.disabled = false;
            }
        });
    });

    document.querySelectorAll('[data-issue-status]').forEach((select) => {
        select.addEventListener('change', async () => {
            const previous = select.dataset.currentStatus || 'need_attention';
            const next = select.value;
            const issue = select.closest('[data-issue]');
            const statusError = issue?.querySelector('[data-status-error]');
            if (next === 'resolved') {
                select.value = previous;
                openResolveDialog(select.dataset.issueStatus, select.dataset.issueTitle);
                return;
            }

            if (statusError) statusError.hidden = true;
            select.disabled = true;
            try {
                const result = await requestJson(
                    `/workspace/guest-issues/api/issues/${select.dataset.issueStatus}/status`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status: next })
                    }
                );
                select.dataset.currentStatus = result.operational_status;
                select.value = result.operational_status;
                if (issue) issue.dataset.status = result.operational_status;
                const pill = issue?.querySelector('[data-status-pill]');
                if (pill) {
                    pill.className = `workflow-pill workflow-${result.operational_status}`;
                    pill.textContent = result.status_label;
                }
                appendIssueNote(issue, result.note);
                applyFilters();
            } catch (requestError) {
                select.value = previous;
                if (statusError) {
                    statusError.textContent = requestError.message;
                    statusError.hidden = false;
                }
            } finally {
                select.disabled = false;
            }
        });
    });

    document.querySelectorAll('[data-issue-note-form]').forEach((noteForm) => {
        noteForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const issue = noteForm.closest('[data-issue]');
            const textarea = noteForm.querySelector('textarea');
            const submit = noteForm.querySelector('button[type="submit"]');
            const noteError = noteForm.querySelector('[data-note-error]');
            const note = textarea.value.trim();
            if (!note) {
                noteError.textContent = 'Add a note before posting.';
                noteError.hidden = false;
                textarea.focus();
                return;
            }

            noteError.hidden = true;
            submit.disabled = true;
            try {
                const result = await requestJson(
                    `/workspace/guest-issues/api/issues/${noteForm.dataset.issueNoteForm}/notes`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ note })
                    }
                );
                appendIssueNote(issue, result.note);
                textarea.value = '';
            } catch (requestError) {
                noteError.textContent = requestError.message;
                noteError.hidden = false;
            } finally {
                submit.disabled = false;
            }
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
            await requestJson(`/workspace/guest-issues/api/issues/${selectedIssueId}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ comment: note })
            });

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
