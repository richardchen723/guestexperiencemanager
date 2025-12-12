// Activities Page JavaScript

let currentPage = 1;
let trendsChart = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadUsers();
    setPreset('30days');
    loadReports();
    loadTrends();
});

// Load users for filter dropdown
async function loadUsers() {
    try {
        const response = await fetch('/admin/api/users');
        const users = await response.json();
        const select = document.getElementById('userId');
        
        users.forEach(user => {
            const option = document.createElement('option');
            option.value = user.user_id;
            option.textContent = user.name || user.email;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading users:', error);
    }
}

// Set date preset
function setPreset(preset) {
    const today = new Date();
    const startDate = document.getElementById('startDate');
    const endDate = document.getElementById('endDate');
    
    endDate.value = today.toISOString().split('T')[0];
    
    switch(preset) {
        case '7days':
            const date7 = new Date(today);
            date7.setDate(date7.getDate() - 7);
            startDate.value = date7.toISOString().split('T')[0];
            break;
        case '30days':
            const date30 = new Date(today);
            date30.setDate(date30.getDate() - 30);
            startDate.value = date30.toISOString().split('T')[0];
            break;
        case '90days':
            const date90 = new Date(today);
            date90.setDate(date90.getDate() - 90);
            startDate.value = date90.toISOString().split('T')[0];
            break;
        case 'month':
            startDate.value = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().split('T')[0];
            break;
        case 'year':
            startDate.value = new Date(today.getFullYear(), 0, 1).toISOString().split('T')[0];
            break;
    }
    
    runQuery();
}

// Reset filters
function resetFilters() {
    document.getElementById('startDate').value = '';
    document.getElementById('endDate').value = '';
    document.getElementById('activityType').value = '';
    document.getElementById('userId').value = '';
    document.getElementById('action').value = '';
    currentPage = 1;
    runQuery();
}

// Run query
async function runQuery() {
    const container = document.getElementById('activityLogContainer');
    container.innerHTML = '<div class="loading">Loading activities...</div>';
    
    const params = new URLSearchParams();
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const activityType = document.getElementById('activityType').value;
    const userId = document.getElementById('userId').value;
    const action = document.getElementById('action').value;
    
    if (startDate) params.append('start_date', startDate + 'T00:00:00');
    if (endDate) params.append('end_date', endDate + 'T23:59:59');
    if (activityType) params.append('activity_type', activityType);
    if (userId) params.append('user_id', userId);
    if (action) params.append('action', action);
    params.append('page', currentPage);
    params.append('per_page', 50);
    
    try {
        const response = await fetch(`/admin/api/activities?${params}`);
        const data = await response.json();
        
        if (data.activities && data.activities.length > 0) {
            renderActivityLog(data.activities);
            renderPagination(data.page, data.pages, data.total);
        } else {
            container.innerHTML = '<p>No activities found.</p>';
            document.getElementById('activityLogPagination').innerHTML = '';
        }
    } catch (error) {
        container.innerHTML = `<div class="error">Error loading activities: ${error.message}</div>`;
    }
}

// Render activity log table
function renderActivityLog(activities) {
    const container = document.getElementById('activityLogContainer');
    let html = '<table class="activity-table"><thead><tr>';
    html += '<th>Timestamp</th><th>User</th><th>Activity Type</th><th>Action</th><th>Entity</th><th>Details</th>';
    html += '</tr></thead><tbody>';
    
    activities.forEach(activity => {
        const date = new Date(activity.created_at);
        const metadata = activity.metadata || {};
        let details = '';
        
        if (activity.action === 'status_change') {
            details = `${metadata.old_status || ''} → ${metadata.new_status || ''}`;
        } else if (activity.action === 'assign') {
            details = `Assigned to user ${metadata.new_assigned_user_id || ''}`;
        } else if (activity.entity_id) {
            details = `ID: ${activity.entity_id}`;
        }
        
        html += `<tr>
            <td>${date.toLocaleString()}</td>
            <td>${escapeHtml(activity.user_name || activity.user_email || 'Unknown')}</td>
            <td>${escapeHtml(activity.activity_type)}</td>
            <td>${escapeHtml(activity.action)}</td>
            <td>${escapeHtml(activity.entity_type)}</td>
            <td>${escapeHtml(details)}</td>
        </tr>`;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

// Render pagination
function renderPagination(page, pages, total) {
    const container = document.getElementById('activityLogPagination');
    if (pages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    let html = `<div class="pagination-info">Page ${page} of ${pages} (${total} total)</div>`;
    html += '<div class="pagination-buttons">';
    
    if (page > 1) {
        html += `<button class="btn btn-secondary" onclick="goToPage(${page - 1})">Previous</button>`;
    }
    
    for (let i = Math.max(1, page - 2); i <= Math.min(pages, page + 2); i++) {
        html += `<button class="btn ${i === page ? 'btn-primary' : 'btn-secondary'}" onclick="goToPage(${i})">${i}</button>`;
    }
    
    if (page < pages) {
        html += `<button class="btn btn-secondary" onclick="goToPage(${page + 1})">Next</button>`;
    }
    
    html += '</div>';
    container.innerHTML = html;
}

// Go to page
function goToPage(page) {
    currentPage = page;
    runQuery();
}

// Switch tab
function switchTab(tab, buttonElement) {
    // Update tab buttons
    document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
    if (buttonElement) {
        buttonElement.classList.add('active');
    }
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById(tab + 'Tab').classList.add('active');
    
    // Load data for active tab
    if (tab === 'reports') {
        loadReports();
    } else if (tab === 'trends') {
        loadTrends();
    }
}

// Load reports
async function loadReports() {
    const startDate = document.getElementById('startDate').value || 
        new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    const endDate = document.getElementById('endDate').value || 
        new Date().toISOString().split('T')[0];
    
    // Load ticket metrics
    loadTicketMetrics(startDate, endDate);
    
    // Load user performance
    loadUserPerformance(startDate, endDate);
    
    // Load unresolved assignments
    loadUnresolvedAssignments();
}

// Load ticket metrics
async function loadTicketMetrics(startDate, endDate) {
    const container = document.getElementById('ticketMetricsContent');
    
    try {
        const response = await fetch(
            `/admin/api/activities/reports/ticket-metrics?start_date=${startDate}T00:00:00&end_date=${endDate}T23:59:59`
        );
        const data = await response.json();
        
        let html = `<div class="metrics-summary">
            <div class="metric-item">
                <strong>Created:</strong> ${data.created_count || 0}
            </div>
            <div class="metric-item">
                <strong>Resolved:</strong> ${data.resolved_count || 0}
            </div>
            <div class="metric-item">
                <strong>Resolution Rate:</strong> ${(data.resolution_rate || 0).toFixed(1)}%
            </div>
        </div>`;
        
        if (data.resolvers && data.resolvers.length > 0) {
            html += '<h4>Top Resolvers</h4><table class="report-table"><thead><tr>';
            html += '<th>User</th><th>Resolved</th></tr></thead><tbody>';
            
            data.resolvers.forEach(resolver => {
                html += `<tr>
                    <td>${escapeHtml(resolver.user_name || resolver.user_email || 'Unknown')}</td>
                    <td>${resolver.count}</td>
                </tr>`;
            });
            
            html += '</tbody></table>';
        }
        
        container.innerHTML = html;
    } catch (error) {
        container.innerHTML = `<div class="error">Error loading metrics: ${error.message}</div>`;
    }
}

// Load user performance
async function loadUserPerformance(startDate, endDate) {
    const container = document.getElementById('userPerformanceContent');
    
    try {
        const response = await fetch(
            `/admin/api/activities/reports/user-performance?start_date=${startDate}T00:00:00&end_date=${endDate}T23:59:59`
        );
        const data = await response.json();
        
        if (data.length > 0) {
            let html = '<table class="report-table"><thead><tr>';
            html += '<th>User</th><th>Created</th><th>Resolved</th><th>Assigned</th></tr></thead><tbody>';
            
            data.forEach(user => {
                html += `<tr onclick="filterByUser(${user.user_id})" style="cursor: pointer;">
                    <td>${escapeHtml(user.user_name || user.user_email || 'Unknown')}</td>
                    <td>${user.tickets_created || 0}</td>
                    <td>${user.tickets_resolved || 0}</td>
                    <td>${user.tickets_assigned || 0}</td>
                </tr>`;
            });
            
            html += '</tbody></table>';
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p>No performance data available.</p>';
        }
    } catch (error) {
        container.innerHTML = `<div class="error">Error loading performance data: ${error.message}</div>`;
    }
}

// Load unresolved assignments
async function loadUnresolvedAssignments() {
    const container = document.getElementById('unresolvedAssignmentsContent');
    
    try {
        const response = await fetch('/admin/api/activities/reports/unresolved-assignments');
        const data = await response.json();
        
        if (data.length > 0) {
            let html = '<table class="report-table"><thead><tr>';
            html += '<th>User</th><th>Count</th><th>Tickets</th></tr></thead><tbody>';
            
            data.forEach(assignment => {
                html += `<tr>
                    <td>${escapeHtml(assignment.user_name || assignment.user_email || 'Unknown')}</td>
                    <td>${assignment.ticket_count}</td>
                    <td>`;
                
                assignment.tickets.slice(0, 3).forEach(ticket => {
                    html += `<a href="/tickets/${ticket.ticket_id}/page" target="_blank">#${ticket.ticket_id}</a> `;
                });
                
                if (assignment.tickets.length > 3) {
                    html += `+${assignment.tickets.length - 3} more`;
                }
                
                html += '</td></tr>';
            });
            
            html += '</tbody></table>';
            container.innerHTML = html;
        } else {
            container.innerHTML = '<p>No unresolved assignments.</p>';
        }
    } catch (error) {
        container.innerHTML = `<div class="error">Error loading assignments: ${error.message}</div>`;
    }
}

// Load trends
async function loadTrends() {
    const startDate = document.getElementById('startDate').value || 
        new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    const endDate = document.getElementById('endDate').value || 
        new Date().toISOString().split('T')[0];
    const metric = document.getElementById('trendMetric').value;
    
    try {
        const response = await fetch(
            `/admin/api/activities/reports/trends?start_date=${startDate}T00:00:00&end_date=${endDate}T23:59:59&metric=${metric}`
        );
        const data = await response.json();
        
        if (data.data && data.data.length > 0) {
            renderTrendsChart(data.data, metric);
        } else {
            const canvas = document.getElementById('trendsChart');
            const ctx = canvas.getContext('2d');
            if (trendsChart) {
                trendsChart.destroy();
            }
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            canvas.parentElement.innerHTML = '<p>No trend data available.</p>';
        }
    } catch (error) {
        console.error('Error loading trends:', error);
    }
}

// Render trends chart
function renderTrendsChart(data, metric) {
    const canvas = document.getElementById('trendsChart');
    const ctx = canvas.getContext('2d');
    
    if (trendsChart) {
        trendsChart.destroy();
    }
    
    const labels = data.map(d => d.date);
    const values = data.map(d => d.count);
    
    trendsChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: metric.charAt(0).toUpperCase() + metric.slice(1),
                data: values,
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

// Filter by user
function filterByUser(userId) {
    document.getElementById('userId').value = userId;
    switchTab('log');
    runQuery();
}

// Export to CSV
function exportToCSV() {
    // This would require fetching all activities (not paginated) and converting to CSV
    alert('CSV export feature - to be implemented');
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

