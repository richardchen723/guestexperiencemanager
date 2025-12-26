// Dashboard Page JavaScript
// Configuration
const CONFIG = {
    ticketLimit: 10,
    occupancyMonths: 6,
    refreshInterval: null // Can be set for auto-refresh
};

// State
let dashboardData = null;
let occupancyChart = null;

// Helper functions
function formatDate(date) {
    if (!date) return '';
    if (typeof date === 'string') {
        date = new Date(date);
    }
    return date.toLocaleDateString('en-US', { 
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
        const response = await fetch(`/dashboard/api/data?ticket_limit=${CONFIG.ticketLimit}&occupancy_months=${CONFIG.occupancyMonths}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        dashboardData = await response.json();
        
        renderStatistics(dashboardData.statistics);
        renderTickets(dashboardData.tickets);
        renderOccupancyChart(dashboardData.occupancy);
        
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

function renderTickets(tickets) {
    const container = document.getElementById('myTicketsList');
    const noTickets = document.getElementById('noTickets');
    
    if (!tickets || tickets.length === 0) {
        container.innerHTML = '';
        noTickets.style.display = 'block';
        return;
    }
    
    noTickets.style.display = 'none';
    container.innerHTML = '';
    
    // Reuse ticket card creation
    tickets.forEach(ticket => {
        const card = createTicketCard(ticket);
        container.appendChild(card);
    });
}

function renderOccupancyChart(occupancyData) {
    // Handle empty data
    if (!occupancyData || occupancyData.length === 0) {
        document.getElementById('noOccupancyData').style.display = 'block';
        return;
    }
    
    document.getElementById('noOccupancyData').style.display = 'none';
    
    // Destroy existing chart if present
    if (occupancyChart) {
        occupancyChart.destroy();
        occupancyChart = null;
    }
    
    // Prepare data
    const labels = occupancyData.map(d => {
        // Format month as "MMM YYYY" (e.g., "Jan 2024")
        const [year, month] = d.month.split('-');
        const date = new Date(parseInt(year), parseInt(month) - 1);
        return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    });
    const data = occupancyData.map(d => d.occupancy_rate);
    
    // Create chart
    const ctx = document.getElementById('occupancyChart');
    if (!ctx) {
        console.error('Chart canvas not found');
        return;
    }
    
    occupancyChart = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Occupancy Rate (%)',
                data: data,
                backgroundColor: 'rgba(37, 99, 235, 0.6)',
                borderColor: 'rgba(37, 99, 235, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        callback: function(value) {
                            return value + '%';
                        }
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return 'Occupancy: ' + context.parsed.y.toFixed(1) + '%';
                        }
                    }
                }
            }
        }
    });
}

// Reuse ticket card creation (adapted from tickets/list.html)
function createTicketCard(ticket) {
    const card = document.createElement('div');
    card.className = 'ticket-card';
    
    // Try to get listing name from ticket.listing (from API)
    let listingName = 'General';
    if (ticket.listing_id) {
        if (ticket.listing) {
            // Use listing info from API response (includes internal_listing_name)
            listingName = ticket.listing.internal_listing_name || ticket.listing.name || `Listing ${ticket.listing_id}`;
        } else {
            listingName = `Listing ${ticket.listing_id}`;
        }
    }
    
    const statusClass = ticket.status.toLowerCase().replace(' ', '-');
    const priorityClass = ticket.priority.toLowerCase();
    const categoryClass = ticket.category ? ticket.category.toLowerCase() : 'other';
    
    const dueDate = ticket.due_date ? new Date(ticket.due_date) : null;
    const isOverdue = dueDate && dueDate < new Date() && ticket.status !== 'Resolved' && ticket.status !== 'Closed';
    
    const categoryDisplay = ticket.category ? ticket.category.charAt(0).toUpperCase() + ticket.category.slice(1) : 'Other';
    
    // Render tags
    let tagsHtml = '';
    if (ticket.tags && ticket.tags.length > 0) {
        tagsHtml = '<div class="ticket-card-tags tags-display" style="margin-top: 0.5rem;">';
        ticket.tags.forEach(tag => {
            const tagStyle = tag.color ? `style="background-color: ${tag.color}; border-color: ${tag.color};" class="tag-chip has-color"` : 'class="tag-chip"';
            const inheritedClass = tag.is_inherited ? ' tag-chip-inherited' : '';
            const title = tag.is_inherited ? ' title="Inherited from property"' : '';
            tagsHtml += `<span ${tagStyle}${inheritedClass}${title}>${escapeHtml(tag.name)}</span>`;
        });
        tagsHtml += '</div>';
    }
    
    // Check if recurring
    const recurringBadge = ticket.is_recurring ? 
        `<span class="status-badge" style="background: #6366f1; color: white; font-size: 10px; padding: 2px 6px;" title="Recurring task">🔄 Recurring</span>` : '';
    
    card.innerHTML = `
        <div class="ticket-card-header">
            <h3><a href="/tickets/${ticket.ticket_id}/page">${escapeHtml(ticket.title)}</a></h3>
            <div class="ticket-badges">
                <span class="status-badge status-${statusClass}">${escapeHtml(ticket.status)}</span>
                <span class="priority-badge priority-${priorityClass}">${escapeHtml(ticket.priority)}</span>
                <span class="category-badge category-${categoryClass}">${escapeHtml(categoryDisplay)}</span>
                ${recurringBadge}
            </div>
        </div>
        <div class="ticket-card-body">
            ${ticket.issue_title ? `<p class="ticket-issue">Issue: ${escapeHtml(ticket.issue_title)}</p>` : ''}
            <p class="ticket-listing">Property: ${escapeHtml(listingName)}</p>
            ${ticket.description ? `<p class="ticket-description">${escapeHtml(ticket.description.substring(0, 150))}${ticket.description.length > 150 ? '...' : ''}</p>` : ''}
            ${tagsHtml}
            <div class="ticket-meta">
                ${ticket.assigned_user_name ? `<span class="ticket-assigned">Assigned to: ${escapeHtml(ticket.assigned_user_name)}</span>` : '<span class="ticket-assigned">Unassigned</span>'}
                ${dueDate ? `<span class="ticket-due ${isOverdue ? 'overdue' : ''}">Due: ${formatDate(dueDate)}</span>` : ''}
            </div>
        </div>
        <div class="ticket-card-footer">
            <span class="ticket-created">Created ${formatDate(new Date(ticket.created_at))}</span>
            <a href="/tickets/${ticket.ticket_id}/page" class="btn-secondary">View Details</a>
        </div>
    `;
    
    return card;
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
document.addEventListener('DOMContentLoaded', loadDashboard);





