// Dashboard Page JavaScript
// Configuration
const CONFIG = {
    ticketLimit: 50,
    refreshInterval: null, // Can be set for auto-refresh
    ticketStatuses: ['Open', 'Assigned', 'In Progress', 'Blocked', 'Resolved', 'Closed'],
    ticketPriorities: ['Low', 'Medium', 'High', 'Critical']
};

// State
let dashboardData = null;
let occupancyChart = null;
let dashboardTicketReturnFocus = null;
let dashboardOpenTicketId = null;
let dashboardUsers = [];
let dashboardUsersPromise = null;
let dashboardToastTimer = null;

// Helper functions
function formatDate(date) {
    if (!date) return 'Not set';
    const rawValue = String(date);
    const parsedDate = /^\d{4}-\d{2}-\d{2}$/.test(rawValue)
        ? new Date(`${rawValue}T12:00:00`)
        : new Date(date);
    if (Number.isNaN(parsedDate.getTime())) return 'Not set';
    return parsedDate.toLocaleDateString('en-US', {
        year: 'numeric', 
        month: 'short', 
        day: 'numeric' 
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// UI State Management
function showLoading() {
    document.getElementById('dashboardLoading').style.display = 'block';
}

function hideLoading() {
    document.getElementById('dashboardLoading').style.display = 'none';
}

function showError(error) {
    const errorDiv = document.getElementById('dashboardError');
    errorDiv.style.display = 'block';
    const errorMsg = error ? error.message || String(error) : 'Unknown error';
    errorDiv.querySelector('p').textContent = `Failed to load dashboard data: ${errorMsg}`;
}

function hideError() {
    document.getElementById('dashboardError').style.display = 'none';
}

function showContent() {
    document.getElementById('dashboardContent').style.display = 'block';
}

function hideContent() {
    document.getElementById('dashboardContent').style.display = 'none';
}

// Main load function
async function loadDashboard() {
    showLoading();
    hideError();
    hideContent();
    
    try {
        const response = await fetch(`/dashboard/api/data?ticket_limit=${CONFIG.ticketLimit}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        dashboardData = await response.json();
        
        renderStatistics(dashboardData.statistics);
        renderTickets(dashboardData.tickets, dashboardData.statistics);
        renderOccupancyChart(dashboardData.occupancy, dashboardData.occupancy_metadata);
        
        hideLoading();
        showContent();
    } catch (error) {
        console.error('Error loading dashboard:', error);
        hideLoading();
        showError(error);
    }
}

// Render functions with error handling
function renderStatistics(stats) {
    if (!stats) {
        stats = {};
    }
    // Update stat cards with null checks
    document.getElementById('statTotalAssigned').textContent = stats.total_assigned || 0;
    document.getElementById('statDueThisWeek').textContent = stats.due_this_week || 0;
    document.getElementById('statOverdue').textContent = stats.overdue_count || 0;
    document.getElementById('statHighPriority').textContent = stats.high_priority_count || 0;
}

function renderTickets(tickets, statistics = {}) {
    const container = document.getElementById('myTicketsList');
    const noTickets = document.getElementById('noTickets');
    closeDashboardTicketDetail(false);
    
    if (!tickets || tickets.length === 0) {
        container.innerHTML = '';
        noTickets.style.display = 'block';
        setDashboardText('dashboardTicketsCaption', 'No active tickets are currently assigned to you.');
        return;
    }
    
    noTickets.style.display = 'none';
    container.innerHTML = '';
    const totalAssigned = Number(statistics.total_assigned || tickets.length);
    const countLabel = tickets.length === totalAssigned
        ? `${tickets.length} active ${tickets.length === 1 ? 'ticket' : 'tickets'}`
        : `Showing ${tickets.length} of ${totalAssigned} active tickets`;
    setDashboardText('dashboardTicketsCaption', `${countLabel} · sorted by priority and due date`);
    
    // Reuse ticket card creation
    tickets.forEach(ticket => {
        const card = createTicketCard(ticket);
        container.appendChild(card);
    });
}

function occupancyMonthLabel(value) {
    const [year, month] = value.split('-').map(Number);
    return new Date(year, month - 1, 1).toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}

function renderOccupancyChart(occupancyData, metadata = {}) {
    if (occupancyChart) {
        occupancyChart.destroy();
        occupancyChart = null;
    }
    const rows = Array.isArray(occupancyData) ? occupancyData : [];
    const hasValues = rows.some(row => Number.isFinite(row.occupancy_rate));
    const empty = document.getElementById('noOccupancyData');
    empty.style.display = hasValues ? 'none' : 'block';
    const count = metadata.listing_count;
    setDashboardText('occupancyScope', rows.length
        ? `${occupancyMonthLabel(rows[0].month)} – ${occupancyMonthLabel(rows[rows.length - 1].month)} · ${count ?? 'Unknown number of'} current listings`
        : 'Occupancy data unavailable');
    setDashboardText('occupancySync', ['reservations', 'listings'].map(source => {
        const stamp = metadata.syncs?.[source]?.completed_at;
        const parsed = stamp ? new Date(stamp) : null;
        if (!parsed || Number.isNaN(parsed.getTime())) return `${source}: sync time unavailable`;
        const stale = Date.now() - parsed.getTime() > 48 * 60 * 60 * 1000;
        const label = parsed.toLocaleString('en-US', {
            timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit'
        });
        return `${source}: ${label} UTC${stale ? ' (over 48 hours old)' : ''}`;
    }).join(' · '));
    const labels = rows.map(row => occupancyMonthLabel(row.month));
    document.getElementById('occupancyValues').innerHTML = rows.map((row, index) => `
        <tr><th scope="row">${escapeHtml(labels[index])}${row.is_current_month ? ' (current)' : ''}</th>
        <td>${row.period === 'historical' ? 'Recorded bookings' : 'On the books'}</td>
        <td>${row.booked_nights ?? '—'}</td><td>${row.capacity_nights ?? '—'}</td>
        <td>${Number.isFinite(row.occupancy_rate) ? row.occupancy_rate.toFixed(2) + '%' : 'Unavailable'}</td></tr>
    `).join('');
    const canvas = document.getElementById('occupancyChart');
    if (!canvas || !rows.length) return;
    if (typeof Chart === 'undefined') {
        document.getElementById('occupancyDetails').open = true;
        return;
    }
    occupancyChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels.map((label, index) => rows[index].is_current_month ? [label, 'Current'] : label),
            datasets: [{
                label: 'Booked nights / calendar capacity',
                data: rows.map(row => Number.isFinite(row.occupancy_rate) ? row.occupancy_rate : null),
                backgroundColor: rows.map(row => row.period === 'historical' ? '#5875c4' : '#9dcfc9'),
                borderColor: rows.map(row => row.is_current_month ? '#207a70' : 'transparent'),
                borderWidth: rows.map(row => row.is_current_month ? 2 : 0),
                borderRadius: 4,
                maxBarThickness: 48
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { ticks: { autoSkip: false, maxRotation: 0 }, grid: { display: false } },
                y: { beginAtZero: true, max: 100, ticks: { callback: value => value + '%' } }
            },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: {
                    label: context => {
                        const row = rows[context.dataIndex];
                        const period = row.period === 'historical' ? 'Recorded bookings' : 'On the books';
                        return Number.isFinite(row.occupancy_rate) ? `${period}: ${row.occupancy_rate.toFixed(2)}%` : `${period}: unavailable`;
                    },
                    afterLabel: context => {
                        const row = rows[context.dataIndex];
                        return `${row.booked_nights} booked / ${row.capacity_nights} calendar nights`;
                    }
                } }
            }
        }
    });
}

// Reuse ticket card creation (adapted from tickets/list.html)
function createTicketCard(ticket) {
    return window.TicketCards.create(ticket, {
        action: { dataAction: 'open-ticket-detail' }
    });
}

function handleDashboardTicketClick(event) {
    const trigger = event.target.closest('[data-action="open-ticket-detail"]');
    if (!trigger) return;
    const ticket = (dashboardData?.tickets || []).find(
        (item) => String(item.ticket_id) === String(trigger.dataset.ticketId),
    );
    if (ticket) openDashboardTicketDetail(ticket, trigger);
}

function openDashboardTicketDetail(ticket, trigger) {
    const detail = document.getElementById('dashboardTicketDetail');
    const body = document.getElementById('dashboardTicketDetailBody');
    const fullTicketLink = document.getElementById('dashboardTicketDetailLink');
    if (!detail || !body || !fullTicketLink) return;

    const propertyName = dashboardTicketProperty(ticket);
    const category = dashboardTitleCase(ticket.category || 'Other');
    const tags = (ticket.tags || []).map((tag) => (
        `<span class="dashboard-ticket-detail__tag">${escapeHtml(tag.name)}</span>`
    )).join('');

    setDashboardText('dashboardTicketDetailNumber', `Ticket #${ticket.ticket_id}`);
    setDashboardText('dashboardTicketDetailTitle', ticket.title || 'Untitled ticket');
    setDashboardText('dashboardTicketDetailStatus', '');
    fullTicketLink.href = `/tickets/${ticket.ticket_id}/page`;
    body.innerHTML = `
        <div class="dashboard-ticket-detail__badges">
            <span class="dashboard-ticket-pill dashboard-ticket-pill--status status-${dashboardClassToken(ticket.status || 'Open')}">${escapeHtml(ticket.status || 'Open')}</span>
            <span class="dashboard-ticket-pill dashboard-ticket-pill--priority priority-${dashboardClassToken(ticket.priority || 'Low')}">${escapeHtml(ticket.priority || 'Low')} priority</span>
            ${ticket.is_recurring ? '<span class="dashboard-ticket-pill dashboard-ticket-pill--neutral">Recurring</span>' : ''}
        </div>
        <section class="dashboard-ticket-detail__description">
            <span>Details</span>
            <p>${escapeHtml(ticket.description || ticket.issue_title || 'No description was provided for this ticket.')}</p>
        </section>
        <section class="dashboard-ticket-detail__editor" aria-labelledby="dashboardTicketQuickEditHeading">
            <div class="dashboard-ticket-detail__editor-head">
                <div>
                    <span>Quick update</span>
                    <h3 id="dashboardTicketQuickEditHeading">Keep the essentials current</h3>
                </div>
                <small>Status, priority, owner, and deadline</small>
            </div>
            <form id="dashboardTicketQuickEditForm" class="dashboard-ticket-detail__form" data-ticket-id="${escapeHtml(ticket.ticket_id)}">
                ${dashboardTicketSelectField('Status', 'status', CONFIG.ticketStatuses, ticket.status || 'Open')}
                ${dashboardTicketSelectField('Priority', 'priority', CONFIG.ticketPriorities, ticket.priority || 'Low')}
                <label class="dashboard-ticket-detail__field">
                    <span>Assignee</span>
                    <select name="assigned_user_id" aria-label="Assignee">
                        ${dashboardAssigneeOptions(ticket)}
                    </select>
                </label>
                <label class="dashboard-ticket-detail__field">
                    <span>Due date</span>
                    <input type="date" name="due_date" value="${escapeHtml(dashboardDateInputValue(ticket.due_date))}">
                </label>
            </form>
        </section>
        <dl class="dashboard-ticket-detail__facts">
            ${dashboardTicketFact('Created', formatDate(ticket.created_at))}
            ${dashboardTicketFact('Property', propertyName)}
            ${dashboardTicketFact('Category', category)}
            ${dashboardTicketFact('Created by', ticket.created_by_name || 'Unknown')}
        </dl>
        ${tags ? `<section class="dashboard-ticket-detail__tags"><span>Tags</span><div>${tags}</div></section>` : ''}
    `;

    dashboardTicketReturnFocus = trigger || null;
    dashboardOpenTicketId = ticket.ticket_id;
    detail.hidden = false;
    document.body.classList.add('dashboard-modal-open');
    setDashboardTicketSaveState(false);
    loadDashboardUsers().then(() => refreshDashboardAssigneeOptions(ticket)).catch(() => {
        setDashboardText('dashboardTicketDetailStatus', 'Assignee choices could not be refreshed.');
    });
    window.requestAnimationFrame(() => detail.querySelector('.dashboard-ticket-detail__close')?.focus());
}

function dashboardTicketSelectField(label, name, choices, selectedValue) {
    const options = choices.map((choice) => (
        `<option value="${escapeHtml(choice)}" ${choice === selectedValue ? 'selected' : ''}>${escapeHtml(choice)}</option>`
    )).join('');
    return `
        <label class="dashboard-ticket-detail__field">
            <span>${escapeHtml(label)}</span>
            <select name="${escapeHtml(name)}" aria-label="${escapeHtml(label)}">${options}</select>
        </label>
    `;
}

function dashboardAssigneeOptions(ticket, selectedValue = ticket.assigned_user_id) {
    const users = [...dashboardUsers];
    if (ticket.assigned_user_id && !users.some((user) => Number(user.user_id) === Number(ticket.assigned_user_id))) {
        users.unshift({
            user_id: ticket.assigned_user_id,
            name: ticket.assigned_user_name || ticket.assigned_user_email || 'Current assignee'
        });
    }
    const options = users.map((user) => {
        const label = user.name || user.email || `User ${user.user_id}`;
        return `<option value="${escapeHtml(user.user_id)}" ${Number(user.user_id) === Number(selectedValue) ? 'selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
    return `<option value="" ${selectedValue ? '' : 'selected'}>Unassigned</option>${options}`;
}

function dashboardDateInputValue(value) {
    return value ? String(value).slice(0, 10) : '';
}

async function loadDashboardUsers() {
    if (dashboardUsers.length) return dashboardUsers;
    if (dashboardUsersPromise) return dashboardUsersPromise;
    dashboardUsersPromise = fetch('/tickets/api/users')
        .then(async (response) => {
            const result = await response.json().catch(() => []);
            if (!response.ok) throw new Error(result.error || 'Could not load assignees');
            dashboardUsers = Array.isArray(result) ? result : [];
            return dashboardUsers;
        })
        .finally(() => { dashboardUsersPromise = null; });
    return dashboardUsersPromise;
}

function refreshDashboardAssigneeOptions(ticket) {
    if (String(dashboardOpenTicketId) !== String(ticket.ticket_id)) return;
    const select = document.querySelector('#dashboardTicketQuickEditForm [name="assigned_user_id"]');
    if (!select) return;
    const selectedValue = select.value;
    select.innerHTML = dashboardAssigneeOptions(ticket, selectedValue);
}

function dashboardTicketFact(label, value, isAlert = false) {
    return `
        <div class="${isAlert ? 'is-alert' : ''}">
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(value)}</dd>
        </div>
    `;
}

function handleDashboardTicketDetailClick(event) {
    if (event.target.closest('[data-action="close-ticket-detail"]')) closeDashboardTicketDetail();
}

function handleDashboardTicketDetailChange(event) {
    if (!event.target.closest('#dashboardTicketQuickEditForm')) return;
    setDashboardText('dashboardTicketDetailStatus', 'Unsaved changes');
    setDashboardTicketSaveState(true);
}

async function handleDashboardTicketDetailSubmit(event) {
    const form = event.target.closest('#dashboardTicketQuickEditForm');
    if (!form) return;
    event.preventDefault();

    const ticketId = form.dataset.ticketId;
    const ticket = (dashboardData?.tickets || []).find((item) => String(item.ticket_id) === String(ticketId));
    if (!ticket) {
        setDashboardText('dashboardTicketDetailStatus', 'This ticket is no longer available.');
        return;
    }

    const values = new FormData(form);
    const assignedUserId = values.get('assigned_user_id');
    const changes = {
        status: values.get('status'),
        priority: values.get('priority'),
        assigned_user_id: assignedUserId ? Number(assignedUserId) : null,
        due_date: values.get('due_date') || null
    };

    setDashboardTicketSaveState(false, true);
    setDashboardText('dashboardTicketDetailStatus', 'Saving…');

    try {
        const response = await fetch(`/tickets/api/tickets/${ticketId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.error) throw new Error(result.error || 'Could not update ticket');

        const index = dashboardData.tickets.findIndex((item) => String(item.ticket_id) === String(ticketId));
        if (index >= 0) dashboardData.tickets[index] = { ...ticket, ...result };
        showDashboardToast(`Ticket #${ticketId} updated.`);
        await refreshDashboardAfterTicketUpdate();
    } catch (error) {
        console.error('Error updating dashboard ticket:', error);
        setDashboardText('dashboardTicketDetailStatus', error.message || 'Could not update ticket.');
        setDashboardTicketSaveState(true);
    }
}

function setDashboardTicketSaveState(enabled, saving = false) {
    const button = document.getElementById('dashboardTicketDetailSave');
    if (!button) return;
    button.disabled = !enabled || saving;
    button.textContent = saving ? 'Saving…' : 'Save changes';
}

async function refreshDashboardAfterTicketUpdate() {
    const response = await fetch(`/dashboard/api/data?ticket_limit=${CONFIG.ticketLimit}`);
    if (!response.ok) throw new Error('Ticket saved, but the dashboard could not refresh.');
    dashboardData = await response.json();
    renderStatistics(dashboardData.statistics);
    renderTickets(dashboardData.tickets, dashboardData.statistics);
}

function showDashboardToast(message, isError = false) {
    const toast = document.getElementById('dashboardToast');
    if (!toast) return;
    window.clearTimeout(dashboardToastTimer);
    toast.textContent = message;
    toast.classList.toggle('is-error', isError);
    toast.classList.add('is-visible');
    dashboardToastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 3600);
}

function handleDashboardTicketDetailKeydown(event) {
    if (event.key !== 'Escape') return;
    const detail = document.getElementById('dashboardTicketDetail');
    if (detail && !detail.hidden) closeDashboardTicketDetail();
}

function closeDashboardTicketDetail(restoreFocus = true) {
    const detail = document.getElementById('dashboardTicketDetail');
    if (!detail || detail.hidden) return;
    detail.hidden = true;
    document.body.classList.remove('dashboard-modal-open');
    if (restoreFocus) dashboardTicketReturnFocus?.focus();
    dashboardTicketReturnFocus = null;
    dashboardOpenTicketId = null;
}

function dashboardTicketProperty(ticket) {
    return window.TicketCards.propertyName(ticket);
}

function isDashboardTicketOverdue(ticket) {
    if (!ticket.due_date || ['Resolved', 'Closed'].includes(ticket.status)) return false;
    const dueDate = new Date(`${String(ticket.due_date).slice(0, 10)}T23:59:59`);
    return !Number.isNaN(dueDate.getTime()) && dueDate < new Date();
}

function dashboardClassToken(value) {
    return String(value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function dashboardTitleCase(value) {
    return String(value || '').replace(/[-_]+/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function dashboardInitials(name) {
    return String(name || 'Unassigned').trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || '—';
}

function setDashboardText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
}

// Store current user ID (set in template)
const CURRENT_USER_ID = window.CURRENT_USER_ID || null;

// Build tickets page URL with filters
function buildTicketsUrl(filters) {
    const params = new URLSearchParams();
    if (CURRENT_USER_ID) {
        params.append('assigned_user_id', CURRENT_USER_ID);
    }
    
    if (filters.status) {
        params.append('status', filters.status.join(','));
    }
    if (filters.priority) {
        params.append('priority', filters.priority.join(','));
    }
    if (filters.past_due) {
        params.append('past_due', 'true');
    }
    if (filters.due_days) {
        params.append('due_days', filters.due_days);
    }
    
    return `/tickets?${params.toString()}`;
}

// Navigation functions for dashboard cards
function navigateToMyTickets() {
    const url = buildTicketsUrl({ 
        status: ['Open', 'Assigned', 'In Progress', 'Blocked'] 
    });
    window.location.href = url;
}

function navigateToDueThisWeek() {
    const url = buildTicketsUrl({ 
        status: ['Open', 'Assigned', 'In Progress', 'Blocked'], 
        due_days: 7 
    });
    window.location.href = url;
}

function navigateToOverdue() {
    const url = buildTicketsUrl({ 
        status: ['Open', 'Assigned', 'In Progress', 'Blocked'], 
        past_due: true 
    });
    window.location.href = url;
}

function navigateToHighPriority() {
    const url = buildTicketsUrl({ 
        status: ['Open', 'Assigned', 'In Progress', 'Blocked'], 
        priority: ['High', 'Critical'] 
    });
    window.location.href = url;
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('myTicketsList')?.addEventListener('click', handleDashboardTicketClick);
    document.getElementById('dashboardTicketDetail')?.addEventListener('click', handleDashboardTicketDetailClick);
    document.getElementById('dashboardTicketDetail')?.addEventListener('change', handleDashboardTicketDetailChange);
    document.getElementById('dashboardTicketDetail')?.addEventListener('submit', handleDashboardTicketDetailSubmit);
    document.addEventListener('keydown', handleDashboardTicketDetailKeydown);
    loadDashboardUsers().catch((error) => console.warn('Could not preload dashboard assignees:', error));
    loadDashboard();
});
