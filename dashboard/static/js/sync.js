// Sync Dashboard JavaScript

let currentPollInterval = null;
let currentFilter = 'all';
let allSyncRuns = [];

// Format number with commas
function formatNumber(num) {
    if (num === null || num === undefined) return '0';
    return num.toLocaleString('en-US');
}

// Format duration
function formatDuration(startedAt, completedAt) {
    if (!startedAt) return '-';
    const start = new Date(startedAt);
    const end = completedAt ? new Date(completedAt) : new Date();
    const diff = Math.floor((end - start) / 1000); // seconds
    
    if (diff < 60) return `${diff}s`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ${diff % 60}s`;
    const hours = Math.floor(diff / 3600);
    const minutes = Math.floor((diff % 3600) / 60);
    return `${hours}h ${minutes}m`;
}

// Format date
function formatDate(date) {
    return date.toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Format relative time
function formatRelativeTime(date) {
    if (!date) return '-';
    const now = new Date();
    const then = new Date(date);
    const diff = Math.floor((now - then) / 1000); // seconds
    
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return formatDate(then);
}

// Calculate and display quick stats
function updateQuickStats(syncRuns) {
    if (!syncRuns || syncRuns.length === 0) {
        document.getElementById('quickStats').style.display = 'none';
        return;
    }
    
    const statsEl = document.getElementById('quickStats');
    if (!statsEl) return;
    
    statsEl.style.display = 'grid';
    
    const total = syncRuns.length;
    const completed = syncRuns.filter(r => r.status === 'completed').length;
    const successRate = total > 0 ? Math.round((completed / total) * 100) : 0;
    
    // Find most recent sync
    const sorted = [...syncRuns].sort((a, b) => {
        const aTime = a.started_at ? new Date(a.started_at).getTime() : 0;
        const bTime = b.started_at ? new Date(b.started_at).getTime() : 0;
        return bTime - aTime;
    });
    const lastSync = sorted[0];
    
    document.getElementById('totalSyncs').textContent = formatNumber(total);
    document.getElementById('successRate').textContent = successRate + '%';
    document.getElementById('lastSyncTime').textContent = lastSync ? formatRelativeTime(lastSync.started_at) : '-';
}

// Load sync history
function loadSyncHistory() {
    const container = document.getElementById('syncHistoryContainer');
    const loading = document.getElementById('loading');
    const noResults = document.getElementById('noResults');
    
    loading.style.display = 'flex';
    container.innerHTML = '';
    noResults.style.display = 'none';
    
    fetch('/sync/api/history')
        .then(response => response.json())
        .then(data => {
            loading.style.display = 'none';
            
            if (!data || data.length === 0) {
                noResults.style.display = 'block';
                document.getElementById('quickStats').style.display = 'none';
                return;
            }
            
            allSyncRuns = data;
            updateQuickStats(data);
            applyFilter();
        })
        .catch(error => {
            loading.style.display = 'none';
            console.error('Error loading sync history:', error);
            container.innerHTML = '<div class="alert alert-error">Error loading sync history</div>';
        });
}

// Apply filter
function applyFilter() {
    const container = document.getElementById('syncHistoryContainer');
    container.innerHTML = '';
    
    let filtered = allSyncRuns;
    if (currentFilter !== 'all') {
        filtered = allSyncRuns.filter(run => run.status === currentFilter);
    }
    
    if (filtered.length === 0) {
        document.getElementById('noResults').style.display = 'block';
        return;
    }
    
    document.getElementById('noResults').style.display = 'none';
    
    filtered.forEach(syncRun => {
        const card = createSyncRunCard(syncRun);
        container.appendChild(card);
    });
}

// Setup filter chips
function setupFilters() {
    const chips = document.querySelectorAll('.filter-chip');
    chips.forEach(chip => {
        chip.addEventListener('click', () => {
            // Update active state
            chips.forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            
            // Update filter
            currentFilter = chip.getAttribute('data-filter');
            applyFilter();
        });
    });
}

// Create sync run card
function createSyncRunCard(syncRun) {
    const card = document.createElement('div');
    card.className = 'sync-run-card';
    
    // Add running class for animation
    if (syncRun.status === 'running') {
        card.classList.add('running');
    }
    
    // Handle click - if sync_run_id is null, use job_id
    if (syncRun.sync_run_id) {
        card.onclick = () => {
            window.location.href = `/sync/${syncRun.sync_run_id}/detail`;
        };
    } else if (syncRun.job_id) {
        card.onclick = () => {
            window.location.href = `/sync/job/${syncRun.job_id}/detail`;
        };
    }
    
    const date = syncRun.started_at ? new Date(syncRun.started_at) : null;
    const dateStr = date ? formatDate(date) : 'Unknown';
    const duration = formatDuration(syncRun.started_at, syncRun.completed_at);
    
    // Format status display
    let statusDisplay = syncRun.status || 'unknown';
    if (statusDisplay === 'running') {
        statusDisplay = 'In Progress';
    } else if (statusDisplay === 'completed') {
        statusDisplay = 'Completed';
    } else if (statusDisplay === 'error') {
        statusDisplay = 'Error';
    }
    
    // Get metrics
    const recordsProcessed = syncRun.records_processed || 0;
    const recordsCreated = syncRun.records_created || 0;
    const recordsUpdated = syncRun.records_updated || 0;
    const errors = syncRun.errors || 0;
    const syncTypes = syncRun.sync_types ? syncRun.sync_types.length : 0;
    
    card.innerHTML = `
        <div class="sync-run-header">
            <div class="sync-run-info">
                <div class="sync-run-id">${syncRun.sync_run_id ? `Sync Run #${syncRun.sync_run_id}` : 'Starting...'}</div>
                <div class="sync-run-date">${dateStr}</div>
                <div class="sync-run-badges">
                    <span class="sync-run-mode ${syncRun.sync_mode || 'full'}">${syncRun.sync_mode || 'Full'}</span>
                    <span class="sync-run-status ${syncRun.status}">${statusDisplay}</span>
                </div>
            </div>
        </div>
        <div class="sync-run-summary">
            <div class="sync-summary-item">
                <span class="sync-summary-label">Processed</span>
                <span class="sync-summary-value">${formatNumber(recordsProcessed)}</span>
            </div>
            <div class="sync-summary-item">
                <span class="sync-summary-label">Created</span>
                <span class="sync-summary-value">${formatNumber(recordsCreated)}</span>
            </div>
            <div class="sync-summary-item">
                <span class="sync-summary-label">Updated</span>
                <span class="sync-summary-value">${formatNumber(recordsUpdated)}</span>
            </div>
            <div class="sync-summary-item">
                <span class="sync-summary-label">Errors</span>
                <span class="sync-summary-value">${formatNumber(errors)}</span>
            </div>
            <div class="sync-summary-item">
                <span class="sync-summary-label">Duration</span>
                <span class="sync-summary-value">${duration}</span>
            </div>
        </div>
    `;
    
    return card;
}

// Trigger full sync
function triggerFullSync() {
    const btn = document.getElementById('triggerFullSyncBtn');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<svg class="btn-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 2V8L12 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 8L4 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 8C2 11.3137 4.68629 14 8 14C11.3137 14 14 11.3137 14 8C14 4.68629 11.3137 2 8 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>Starting...';
    
    fetch('/sync/api/full', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    if (response.status === 403) {
                        throw new Error('Admin access required to trigger syncs');
                    }
                    throw new Error(err.error || `HTTP ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.job_id) {
                showProgressModal(data.job_id);
                pollSyncProgress(data.job_id);
            } else {
                alert('Error starting sync: ' + (data.error || 'Unknown error'));
            }
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        })
        .catch(error => {
            console.error('Error triggering full sync:', error);
            alert('Error starting sync: ' + error.message);
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        });
}

// Trigger incremental sync
function triggerIncrementalSync() {
    const btn = document.getElementById('triggerIncrementalSyncBtn');
    if (!btn) {
        console.error('Incremental sync button not found');
        alert('Error: Incremental sync button not found');
        return;
    }
    
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<svg class="btn-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 2V8L12 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M8 8L4 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M2 8C2 11.3137 4.68629 14 8 14C11.3137 14 14 11.3137 14 8C14 4.68629 11.3137 2 8 2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>Starting...';
    
    fetch('/sync/api/incremental', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
        .then(response => {
            if (!response.ok) {
                return response.json().then(err => {
                    if (response.status === 403) {
                        throw new Error('Admin access required to trigger syncs');
                    }
                    throw new Error(err.error || `HTTP ${response.status}`);
                });
            }
            return response.json();
        })
        .then(data => {
            if (data.job_id) {
                showProgressModal(data.job_id);
                pollSyncProgress(data.job_id);
            } else {
                alert('Error starting sync: ' + (data.error || 'Unknown error'));
            }
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        })
        .catch(error => {
            console.error('Error triggering incremental sync:', error);
            alert('Error starting sync: ' + error.message);
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        });
}

// Show progress modal
function showProgressModal(jobId) {
    const modal = document.getElementById('progressModal');
    if (!modal) return;
    
    modal.style.display = 'flex';
    
    // Reset progress
    updateProgressDisplay({
        phase: 'Starting...',
        processed: 0,
        total: 0,
        created: 0,
        updated: 0,
        errors: 0,
        percentage: 0
    });
    
    document.getElementById('progressError').style.display = 'none';
    document.getElementById('progressComplete').style.display = 'none';
    document.getElementById('closeProgressBtn').style.display = 'none';
}

// Close progress modal
function closeProgressModal() {
    const modal = document.getElementById('progressModal');
    modal.style.display = 'none';
    
    if (currentPollInterval) {
        clearInterval(currentPollInterval);
        currentPollInterval = null;
    }
}

// Poll sync progress
function pollSyncProgress(jobId) {
    if (currentPollInterval) {
        clearInterval(currentPollInterval);
    }
    
    currentPollInterval = setInterval(() => {
        fetch(`/sync/api/status/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('progressError').textContent = data.error;
                    document.getElementById('progressError').style.display = 'block';
                    stopPolling();
                    return;
                }
                
                if (data.progress) {
                    updateProgressDisplay(data.progress);
                }
                
                if (data.status === 'completed') {
                    handleSyncComplete(data);
                    stopPolling();
                } else if (data.status === 'error') {
                    document.getElementById('progressError').textContent = data.error || 'Sync failed';
                    document.getElementById('progressError').style.display = 'block';
                    stopPolling();
                }
            })
            .catch(error => {
                console.error('Error polling sync progress:', error);
                stopPolling();
            });
    }, 1500);
}

// Stop polling
function stopPolling() {
    if (currentPollInterval) {
        clearInterval(currentPollInterval);
        currentPollInterval = null;
    }
    document.getElementById('closeProgressBtn').style.display = 'block';
}

// Update progress display
function updateProgressDisplay(progress) {
    const percentage = progress.percentage || 0;
    const processed = progress.processed || 0;
    const total = progress.total || 0;
    
    const bar = document.getElementById('progressBar');
    if (bar) bar.style.width = percentage + '%';
    
    const pct = document.getElementById('progressPercentage');
    if (pct) pct.textContent = percentage.toFixed(1) + '%';
    
    const count = document.getElementById('progressCount');
    if (count) count.textContent = `${formatNumber(processed)} / ${formatNumber(total)}`;
    
    const phase = document.getElementById('progressPhase');
    if (phase) phase.textContent = progress.phase || '-';
    
    const created = document.getElementById('statCreated');
    if (created) created.textContent = formatNumber(progress.created || 0);
    
    const updated = document.getElementById('statUpdated');
    if (updated) updated.textContent = formatNumber(progress.updated || 0);
    
    const errors = document.getElementById('statErrors');
    if (errors) errors.textContent = formatNumber(progress.errors || 0);
}

// Handle sync complete
function handleSyncComplete(data) {
    document.getElementById('progressComplete').style.display = 'block';
    document.getElementById('closeProgressBtn').style.display = 'block';
    
    // Reload history after a short delay
    setTimeout(() => {
        if (window.location.pathname.includes('/sync/history')) {
            loadSyncHistory();
        }
    }, 1000);
}

// Load sync detail
function loadSyncDetail(syncRunId) {
    const loading = document.getElementById('loading');
    const content = document.getElementById('syncDetailContent');
    const error = document.getElementById('errorMessage');
    
    loading.style.display = 'flex';
    content.style.display = 'none';
    error.style.display = 'none';
    
    fetch(`/sync/api/${syncRunId}/detail`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load sync detail');
            }
            return response.json();
        })
        .then(data => {
            loading.style.display = 'none';
            content.style.display = 'block';
            
            // Update metadata
            document.getElementById('syncMode').textContent = data.sync_mode || 'Unknown';
            
            // Format status display
            let statusDisplay = data.status || 'Unknown';
            if (statusDisplay === 'running') {
                statusDisplay = 'In Progress';
            } else if (statusDisplay === 'completed') {
                statusDisplay = 'Completed';
            } else if (statusDisplay === 'error') {
                statusDisplay = 'Error';
            }
            
            document.getElementById('syncStatus').textContent = statusDisplay;
            document.getElementById('syncStartedAt').textContent = data.started_at ? formatDate(new Date(data.started_at)) : '-';
            document.getElementById('syncCompletedAt').textContent = data.completed_at ? formatDate(new Date(data.completed_at)) : '-';
            
            // Calculate and display duration
            const duration = formatDuration(data.started_at, data.completed_at);
            const durationEl = document.getElementById('syncDuration');
            if (durationEl) durationEl.textContent = duration;
            
            // Update status badge
            const statusEl = document.getElementById('syncStatus');
            statusEl.className = 'metadata-value sync-run-status ' + (data.status || 'unknown');
            
            // Show progress dashboard if running
            if (data.is_running) {
                document.getElementById('progressDashboard').style.display = 'block';
                document.getElementById('syncSummary').style.display = 'none';
                
                if (data.progress) {
                    updateDetailProgressDisplay(data.progress);
                }
                
                if (data.job_id) {
                    pollSyncProgressForDetail(data.job_id);
                } else {
                    pollSyncDetailForUpdates(syncRunId);
                }
                
                document.getElementById('listingsTableContainer').style.display = 'none';
                document.getElementById('noListingsMessage').style.display = 'block';
            } else {
                document.getElementById('progressDashboard').style.display = 'none';
                document.getElementById('listingsTableContainer').style.display = 'block';
                document.getElementById('noListingsMessage').style.display = 'none';
                
                // Show summary if available
                if (data.summary && Object.keys(data.summary).length > 0) {
                    renderSyncSummary(data.summary);
                    document.getElementById('syncSummary').style.display = 'block';
                } else {
                    document.getElementById('syncSummary').style.display = 'none';
                }
                
                if (data.listings && data.listings.length > 0) {
                    renderListingsTable(data.listings);
                } else {
                    renderListingsTable([]);
                }
            }
        })
        .catch(err => {
            loading.style.display = 'none';
            error.textContent = 'Error loading sync detail: ' + err.message;
            error.style.display = 'block';
        });
}

// Poll sync progress for detail page
let detailPollInterval = null;

function pollSyncProgressForDetail(jobId) {
    if (detailPollInterval) {
        clearInterval(detailPollInterval);
    }
    
    detailPollInterval = setInterval(() => {
        fetch(`/sync/api/status/${jobId}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    stopDetailPolling();
                    return;
                }
                
                if (data.progress) {
                    updateDetailProgressDisplay(data.progress);
                }
                
                if (data.status === 'completed' || data.status === 'partial' || data.status === 'error') {
                    stopDetailPolling();
                    setTimeout(() => {
                        const pathParts = window.location.pathname.split('/');
                        const lastPart = pathParts[pathParts.length - 1];
                        if (lastPart === 'detail') {
                            const syncRunId = parseInt(pathParts[pathParts.length - 2]);
                            if (syncRunId) {
                                loadSyncDetail(syncRunId);
                            } else if (data.results && data.results.sync_run_id) {
                                window.location.href = `/sync/${data.results.sync_run_id}/detail`;
                            } else {
                                location.reload();
                            }
                        } else if (data.results && data.results.sync_run_id) {
                            window.location.href = `/sync/${data.results.sync_run_id}/detail`;
                        } else {
                            location.reload();
                        }
                    }, 1500);
                }
            })
            .catch(error => {
                console.error('Error polling sync progress:', error);
                stopDetailPolling();
            });
    }, 1500);
}

function stopDetailPolling() {
    if (detailPollInterval) {
        clearInterval(detailPollInterval);
        detailPollInterval = null;
    }
}

// Poll sync detail endpoint for updates
function pollSyncDetailForUpdates(syncRunId) {
    if (detailPollInterval) {
        clearInterval(detailPollInterval);
    }
    
    detailPollInterval = setInterval(() => {
        fetch(`/sync/api/${syncRunId}/detail`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    stopDetailPolling();
                    return;
                }
                
                if (data.progress) {
                    updateDetailProgressDisplay(data.progress);
                }
                
                let statusDisplay = data.status || 'Unknown';
                if (statusDisplay === 'running') {
                    statusDisplay = 'In Progress';
                }
                document.getElementById('syncStatus').textContent = statusDisplay;
                
                if (!data.is_running) {
                    stopDetailPolling();
                    setTimeout(() => {
                        loadSyncDetail(syncRunId);
                    }, 1000);
                }
            })
            .catch(error => {
                console.error('Error polling sync detail:', error);
                stopDetailPolling();
            });
    }, 2000);
}

// Update progress display on detail page
function updateDetailProgressDisplay(progress) {
    if (!progress) return;
    
    const percentage = progress.percentage || 0;
    const processed = progress.processed || 0;
    const total = progress.total || 0;
    
    const progressBar = document.getElementById('detailProgressBar');
    if (progressBar) progressBar.style.width = percentage + '%';
    
    const progressPercentage = document.getElementById('detailProgressPercentage');
    if (progressPercentage) progressPercentage.textContent = percentage.toFixed(1) + '%';
    
    const progressCount = document.getElementById('detailProgressCount');
    if (progressCount) progressCount.textContent = `${formatNumber(processed)} / ${formatNumber(total)}`;
    
    const progressPhase = document.getElementById('detailProgressPhase');
    if (progressPhase) progressPhase.textContent = progress.phase || 'Initializing...';
    
    const currentItem = progress.current_item || progress.item_name || null;
    const currentItemContainer = document.getElementById('currentItemContainer');
    const currentItemSpan = document.getElementById('detailCurrentItem');
    if (currentItemContainer && currentItemSpan) {
        if (currentItem) {
            currentItemSpan.textContent = currentItem;
            currentItemContainer.style.display = 'block';
        } else {
            currentItemContainer.style.display = 'none';
        }
    }
    
    const statCreated = document.getElementById('detailStatCreated');
    if (statCreated) statCreated.textContent = formatNumber(progress.created || 0);
    
    const statUpdated = document.getElementById('detailStatUpdated');
    if (statUpdated) statUpdated.textContent = formatNumber(progress.updated || 0);
    
    const statErrors = document.getElementById('detailStatErrors');
    if (statErrors) statErrors.textContent = formatNumber(progress.errors || 0);
}

// Load sync detail by job_id
function loadSyncDetailByJob(jobId) {
    const loading = document.getElementById('loading');
    const content = document.getElementById('syncDetailContent');
    const error = document.getElementById('errorMessage');
    
    loading.style.display = 'flex';
    content.style.display = 'none';
    error.style.display = 'none';
    
    fetch(`/sync/api/job/${jobId}/detail`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Failed to load sync detail');
            }
            return response.json();
        })
        .then(data => {
            loading.style.display = 'none';
            content.style.display = 'block';
            
            document.getElementById('syncMode').textContent = data.sync_mode || 'Unknown';
            document.getElementById('syncStatus').textContent = data.status || 'Unknown';
            document.getElementById('syncStartedAt').textContent = data.started_at ? formatDate(new Date(data.started_at)) : '-';
            document.getElementById('syncCompletedAt').textContent = data.completed_at ? formatDate(new Date(data.completed_at)) : '-';
            
            const duration = formatDuration(data.started_at, data.completed_at);
            const durationEl = document.getElementById('syncDuration');
            if (durationEl) durationEl.textContent = duration;
            
            const statusEl = document.getElementById('syncStatus');
            statusEl.className = 'metadata-value sync-run-status ' + (data.status || 'unknown');
            
            if (data.is_running && data.job_id) {
                document.getElementById('progressDashboard').style.display = 'block';
                document.getElementById('syncSummary').style.display = 'none';
                if (data.progress) {
                    updateDetailProgressDisplay(data.progress);
                }
                pollSyncProgressForDetail(data.job_id);
                document.getElementById('listingsTableContainer').style.display = 'none';
                document.getElementById('noListingsMessage').style.display = 'block';
            } else {
                document.getElementById('progressDashboard').style.display = 'none';
                document.getElementById('listingsTableContainer').style.display = 'block';
                document.getElementById('noListingsMessage').style.display = 'none';
                
                // Show summary if available
                if (data.summary && Object.keys(data.summary).length > 0) {
                    renderSyncSummary(data.summary);
                    document.getElementById('syncSummary').style.display = 'block';
                } else {
                    document.getElementById('syncSummary').style.display = 'none';
                }
                
                if (data.listings && data.listings.length > 0) {
                    renderListingsTable(data.listings);
                } else {
                    renderListingsTable([]);
                }
            }
        })
        .catch(err => {
            loading.style.display = 'none';
            error.textContent = 'Error loading sync detail: ' + err.message;
            error.style.display = 'block';
        });
}

// Render sync summary by data type
function renderSyncSummary(summary) {
    const summaryGrid = document.getElementById('summaryGrid');
    if (!summaryGrid) return;
    
    // Define sync type labels and order
    const syncTypeOrder = ['listings', 'reservations', 'messages', 'reviews', 'guests'];
    const syncTypeLabels = {
        'listings': 'Listings',
        'reservations': 'Reservations',
        'messages': 'Messages',
        'reviews': 'Reviews',
        'guests': 'Guests'
    };
    
    summaryGrid.innerHTML = '';
    
    // Render each sync type
    syncTypeOrder.forEach(syncType => {
        const data = summary[syncType];
        if (!data) return; // Skip if no data for this type
        
        const summaryCard = document.createElement('div');
        summaryCard.className = 'summary-card';
        
        const created = data.created || 0;
        const updated = data.updated || 0;
        const errors = data.errors || 0;
        const processed = data.processed || 0;
        
        summaryCard.innerHTML = `
            <div class="summary-card-header">
                <h3 class="summary-card-title">${syncTypeLabels[syncType] || syncType}</h3>
            </div>
            <div class="summary-card-body">
                <div class="summary-stat">
                    <div class="summary-stat-icon summary-stat-icon-success">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M13 4L6 11L3 8" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <div class="summary-stat-content">
                        <span class="summary-stat-label">Created</span>
                        <span class="summary-stat-value">${formatNumber(created)}</span>
                    </div>
                </div>
                <div class="summary-stat">
                    <div class="summary-stat-icon summary-stat-icon-info">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M8 2V8L12 4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M8 8L4 4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                            <path d="M2 8C2 11.3137 4.68629 14 8 14C11.3137 14 14 11.3137 14 8C14 4.68629 11.3137 2 8 2" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <div class="summary-stat-content">
                        <span class="summary-stat-label">Updated</span>
                        <span class="summary-stat-value">${formatNumber(updated)}</span>
                    </div>
                </div>
                <div class="summary-stat">
                    <div class="summary-stat-icon summary-stat-icon-error">
                        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M12 4L4 12M4 4L12 12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <div class="summary-stat-content">
                        <span class="summary-stat-label">Errors</span>
                        <span class="summary-stat-value ${errors > 0 ? 'summary-stat-value-error' : ''}">${formatNumber(errors)}</span>
                    </div>
                </div>
            </div>
        `;
        
        summaryGrid.appendChild(summaryCard);
    });
}

// Render listings table
function renderListingsTable(listings) {
    const tbody = document.getElementById('listingsTableBody');
    tbody.innerHTML = '';
    
    if (listings.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: var(--space-6); color: var(--text-secondary);">No listing data available</td></tr>';
        return;
    }
    
    listings.forEach(listing => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(listing.name || `Listing ${listing.listing_id}`)}</td>
            <td>${escapeHtml(listing.address || '-')}</td>
            <td class="text-right">${formatNumber(listing.messages || 0)}</td>
            <td class="text-right">${formatNumber(listing.reviews || 0)}</td>
            <td class="text-right">${formatNumber(listing.reservations || 0)}</td>
            <td class="text-right">${formatNumber(listing.guests || 0)}</td>
        `;
        tbody.appendChild(row);
    });
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Load history if on history page
    if (window.location.pathname === '/sync/history') {
        loadSyncHistory();
        setupFilters();
        
        // Set up button handlers
        const fullSyncBtn = document.getElementById('triggerFullSyncBtn');
        const incrementalSyncBtn = document.getElementById('triggerIncrementalSyncBtn');
        
        if (fullSyncBtn) {
            fullSyncBtn.onclick = triggerFullSync;
        }
        
        if (incrementalSyncBtn) {
            incrementalSyncBtn.onclick = triggerIncrementalSync;
        }
        
        // Auto-refresh history every 5 seconds
        setInterval(loadSyncHistory, 5000);
    }
});
