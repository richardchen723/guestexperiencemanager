(function ticketCardsModule() {
    function escapeHtml(value) {
        const element = document.createElement('div');
        element.textContent = value == null ? '' : String(value);
        return element.innerHTML;
    }

    function classToken(value) {
        return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-');
    }

    function formatDate(value) {
        if (!value) return 'Not set';
        const rawValue = String(value);
        const date = /^\d{4}-\d{2}-\d{2}$/.test(rawValue)
            ? new Date(`${rawValue}T12:00:00`)
            : new Date(value);
        if (Number.isNaN(date.getTime())) return 'Not set';
        return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function listingName(listing, fallbackId = '') {
        return listing?.internal_listing_name
            || listing?.name
            || (fallbackId ? `Listing ${fallbackId}` : '');
    }

    function propertyName(ticket, listingCatalog = []) {
        const attachedListings = Array.isArray(ticket?.listings) ? ticket.listings : [];
        const names = attachedListings.map((listing) => listingName(listing, listing.listing_id)).filter(Boolean);
        if (names.length) return names.join(', ');

        if (ticket?.listing) return listingName(ticket.listing, ticket.listing_id);

        if (ticket?.listing_id) {
            const catalogListing = listingCatalog.find(
                (listing) => String(listing.listing_id) === String(ticket.listing_id),
            );
            return listingName(catalogListing, ticket.listing_id);
        }
        return 'General';
    }

    function safeTagColor(value) {
        const color = String(value || '').trim();
        return /^#[0-9a-f]{3,8}$/i.test(color) ? color : '';
    }

    function tagMarkup(tags) {
        const allTags = Array.isArray(tags) ? tags.filter((tag) => tag?.name) : [];
        const visibleTags = allTags.slice(0, 2);
        const chips = visibleTags.map((tag) => {
            const color = safeTagColor(tag.color);
            const style = color ? ` style="--ticket-tag-color: ${color}"` : '';
            const title = tag.is_inherited ? ' title="Inherited from property"' : '';
            return `<span class="ticket-summary-card__tag"${style}${title}>${escapeHtml(tag.name)}</span>`;
        }).join('');
        const remaining = allTags.length - visibleTags.length;
        const more = remaining > 0
            ? `<span class="ticket-summary-card__tag-more" title="${escapeHtml(allTags.slice(2).map((tag) => tag.name).join(', '))}">+${remaining}</span>`
            : '';
        const label = allTags.length ? `Tags: ${allTags.map((tag) => tag.name).join(', ')}` : 'No tags';
        return `
            <div class="ticket-summary-card__tags" aria-label="${escapeHtml(label)}">
                ${chips || '<span class="ticket-summary-card__tag-empty">No tags</span>'}
                ${more}
            </div>
        `;
    }

    function initials(name) {
        return String(name || 'Unassigned').trim().split(/\s+/).slice(0, 2)
            .map((part) => part[0]).join('').toUpperCase() || '—';
    }

    function isOverdue(ticket) {
        if (!ticket?.due_date || ['Resolved', 'Closed'].includes(ticket.status)) return false;
        const dueDate = new Date(`${String(ticket.due_date).slice(0, 10)}T23:59:59`);
        return !Number.isNaN(dueDate.getTime()) && dueDate < new Date();
    }

    function actionMarkup(ticket, action = {}) {
        const ticketId = escapeHtml(ticket.ticket_id);
        const title = escapeHtml(ticket.title || 'Untitled ticket');
        const ariaLabel = `Open details for ticket ${ticketId}: ${title}`;
        if (action.href) {
            return `<a class="ticket-summary-card__open" href="${escapeHtml(action.href)}" aria-label="${ariaLabel}"></a>`;
        }
        const dataAction = escapeHtml(action.dataAction || 'open-ticket-detail');
        return `<button class="ticket-summary-card__open" type="button" data-action="${dataAction}" data-ticket-id="${ticketId}" aria-label="${ariaLabel}"></button>`;
    }

    function create(ticket, options = {}) {
        const card = document.createElement('article');
        const extraClass = options.extraClass ? ` ${options.extraClass}` : '';
        card.className = `ticket-summary-card${extraClass}`;
        card.dataset.priority = classToken(ticket.priority || 'Low');

        const status = ticket.status || 'Open';
        const priority = ticket.priority || 'Low';
        const assignee = ticket.assigned_user_name || 'Unassigned';
        const overdue = isOverdue(ticket);
        const property = propertyName(ticket, options.listingCatalog || []);

        card.innerHTML = `
            <div class="ticket-summary-card__top">
                <span class="ticket-summary-card__number">#${escapeHtml(ticket.ticket_id)}</span>
                <div class="ticket-summary-card__badges">
                    <span class="ticket-summary-pill ticket-summary-pill--status status-${classToken(status)}">${escapeHtml(status)}</span>
                    <span class="ticket-summary-pill ticket-summary-pill--priority priority-${classToken(priority)}">${escapeHtml(priority)}</span>
                </div>
            </div>
            <h3 class="ticket-summary-card__title">${escapeHtml(ticket.title || 'Untitled ticket')}</h3>
            <div class="ticket-summary-card__property" title="${escapeHtml(property)}">
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m2.5 7 5.5-4 5.5 4v6.5H9.8V9.7H6.2v3.8H2.5V7Z"/></svg>
                <span>${escapeHtml(property)}</span>
            </div>
            ${tagMarkup(ticket.tags)}
            <div class="ticket-summary-card__assignee">
                <span class="ticket-summary-avatar" aria-hidden="true">${escapeHtml(initials(assignee))}</span>
                <span>
                    <small>Assignee</small>
                    <strong>${escapeHtml(assignee)}</strong>
                </span>
            </div>
            <div class="ticket-summary-card__dates">
                <span>
                    <small>Created</small>
                    <strong>${formatDate(ticket.created_at)}</strong>
                </span>
                <span class="${overdue ? 'is-overdue' : ''}">
                    <small>${overdue ? 'Overdue' : 'Due'}</small>
                    <strong>${ticket.due_date ? formatDate(ticket.due_date) : 'No due date'}</strong>
                </span>
            </div>
            ${actionMarkup(ticket, options.action)}
        `;
        return card;
    }

    window.TicketCards = { create, propertyName, formatDate };
})();
