#!/usr/bin/env python3
"""
Sync dashboard API routes.
"""

import sys
import os
import json
import threading
import logging
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

# Configure logger
logger = logging.getLogger(__name__)

from database.models import SyncLog, Listing, get_session
from database.schema import get_database_path
from sync.sync_manager import full_sync, incremental_sync
from dashboard.sync.job_manager import get_job_manager
from dashboard.sync.web_progress import WebProgressTracker
from dashboard.auth.decorators import approved_required, admin_required
import dashboard.config as config

sync_bp = Blueprint('sync', __name__, url_prefix='/sync')


def get_expected_sync_types(sync_mode: str, logs: list) -> set:
    """
    Determine which sync types are expected for a sync run.
    
    For full sync: always ['listings', 'reservations', 'messages', 'reviews']
    For incremental: use the sync types that actually have logs (they were needed)
    
    Args:
        sync_mode: 'full' or 'incremental'
        logs: List of SyncLog objects for this sync run
        
    Returns:
        Set of expected sync type strings
    """
    if sync_mode == 'full':
        return {'listings', 'reservations', 'messages', 'reviews'}
    else:
        # Incremental: only sync types that have logs are expected
        return {log.sync_type for log in logs}


def determine_sync_status(sync_run_id: int, logs: list, sync_mode: str) -> str:
    """
    Determine sync status based on database logs only (source of truth).
    
    A sync is only completed when ALL expected sync types have completed logs
    with status 'success' or 'partial' and completed_at is not None.
    
    Args:
        sync_run_id: The sync run ID
        logs: List of SyncLog objects for this sync run
        sync_mode: 'full' or 'incremental'
        
    Returns:
        'running', 'completed', or 'error'
    """
    if not logs:
        # No logs yet - might be just starting
        return 'running'
    
    # Determine expected sync types
    expected_types = get_expected_sync_types(sync_mode, logs)
    
    # Get actual sync types that have logs
    actual_types = {log.sync_type for log in logs}
    
    # Check if all expected types have completed logs
    all_completed = True
    has_error = False
    
    for sync_type in expected_types:
        # Find the log(s) for this sync type
        type_logs = [log for log in logs if log.sync_type == sync_type]
        
        if not type_logs:
            # Expected type has no logs yet - still running
            all_completed = False
            break
        
        # Check the latest log for this type (in case there are duplicates)
        latest_log = max(type_logs, key=lambda x: x.started_at if x.started_at else datetime.min)
        
        if latest_log.status == 'error':
            has_error = True
        elif latest_log.completed_at is None or latest_log.status not in ('success', 'partial'):
            # Log exists but not completed
            all_completed = False
    
    if has_error:
        return 'error'
    elif all_completed and len(actual_types) >= len(expected_types):
        return 'completed'
    else:
        return 'running'


def run_sync_async(job_id: str, sync_mode: str):
    """Run sync in background thread"""
    logger.info(f"Starting async sync: job_id={job_id}, sync_mode={sync_mode}")
    job_manager = get_job_manager()
    
    try:
        logger.debug(f"Updating job {job_id} status to 'running'")
        job_manager.update_job_status(job_id, 'running')
        
        # Generate sync_run_id BEFORE starting sync
        # This way we can set it on the job immediately so it can be found while sync is running
        db_path = get_database_path()
        session = get_session(db_path)
        try:
            # Get the highest sync_run_id and increment
            max_run = session.query(SyncLog.sync_run_id).filter(
                SyncLog.sync_run_id.isnot(None)
            ).order_by(SyncLog.sync_run_id.desc()).first()
            sync_run_id = (max_run[0] + 1) if max_run and max_run[0] else 1
            logger.info(f"Generated sync_run_id: {sync_run_id} for job {job_id}")
        except Exception as e:
            logger.warning(f"Error generating sync_run_id: {e}, defaulting to 1")
            sync_run_id = 1
        finally:
            session.close()
        
        # Set sync_run_id on job IMMEDIATELY so it can be found by sync_run_id while running
        job_manager.set_sync_run_id(job_id, sync_run_id)
        logger.debug(f"Set sync_run_id {sync_run_id} on job {job_id}")
        
        # Create web progress tracker
        progress = WebProgressTracker(job_id)
        logger.debug(f"Created WebProgressTracker for job {job_id}")
        
        # Run sync with the pre-generated sync_run_id
        if sync_mode == 'full':
            logger.info(f"Starting full_sync for job {job_id}, sync_run_id={sync_run_id}")
            results = full_sync(progress_tracker=progress, sync_run_id=sync_run_id)
        else:
            logger.info(f"Starting incremental_sync for job {job_id}, sync_run_id={sync_run_id} (force=True for manual trigger)")
            # Force=True when manually triggered from UI to ensure sync actually runs
            results = incremental_sync(progress_tracker=progress, sync_run_id=sync_run_id, force=True)
        
        logger.info(f"Sync completed for job {job_id}, sync_run_id={sync_run_id}, results: {results}")
        
        # Store results (sync_run_id is already set above)
        job_manager.set_results(job_id, results)
        job_manager.update_job_status(job_id, 'completed')
        logger.info(f"Job {job_id} marked as completed")
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(
            f"Error in run_sync_async for job {job_id}: {str(e)}",
            exc_info=True,
            extra={'job_id': job_id, 'sync_mode': sync_mode}
        )
        logger.debug(f"Full error traceback for job {job_id}:\n{error_details}")
        job_manager.update_job_status(job_id, 'error', error=str(e))


@sync_bp.route('/history')
@approved_required
def sync_history_page():
    """Render sync history page"""
    return render_template('sync/history.html')


@sync_bp.route('/api/history')
@approved_required
def api_sync_history():
    """Get sync history - list of all sync runs"""
    db_path = get_database_path()
    job_manager = get_job_manager()
    
    # Note: Database tables should already be initialized at app startup
    # No need to call init_models() on every request - it's expensive
    session = get_session(db_path)
    
    try:
        # Get recent sync logs with pagination (limit to 500 most recent to improve performance)
        # Include both with and without sync_run_id
        # Logs without sync_run_id are from older syncs or command-line runs
        all_logs = session.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(500).all()
        
        # Get active jobs from job manager (get fresh copy to catch any updates)
        active_jobs = job_manager.get_all_active_jobs()
        # Re-check active jobs after processing to catch any that got sync_run_id assigned
        active_sync_run_ids = {job.get('sync_run_id') for job in active_jobs.values() if job.get('sync_run_id')}
        
        # Create a map of job_id to job for easy lookup
        jobs_by_sync_run_id = {job.get('sync_run_id'): job for job in active_jobs.values() if job.get('sync_run_id')}
        jobs_without_sync_run_id = {job_id: job for job_id, job in active_jobs.items() if job.get('sync_run_id') is None}
        
        # Also create a reverse map: sync_run_id -> list of job_ids (in case of duplicates, though there shouldn't be)
        sync_run_id_to_jobs = {}
        for job_id, job in active_jobs.items():
            sync_run_id = job.get('sync_run_id')
            if sync_run_id:
                if sync_run_id not in sync_run_id_to_jobs:
                    sync_run_id_to_jobs[sync_run_id] = []
                sync_run_id_to_jobs[sync_run_id].append(job_id)
        
        # Group by sync_run_id (or by time for logs without sync_run_id)
        sync_runs = {}
        # First pass: collect all logs by sync_run_id to determine sync_mode
        logs_by_run_id = {}
        logs_without_run_id = []  # Logs without sync_run_id - group by time
        
        for log in all_logs:
            run_id = log.sync_run_id
            if run_id is not None:
                if run_id not in logs_by_run_id:
                    logs_by_run_id[run_id] = []
                logs_by_run_id[run_id].append(log)
            else:
                logs_without_run_id.append(log)
        
        # Group logs without sync_run_id by approximate time (within 5 minutes)
        # This creates "virtual" sync runs for older syncs
        if logs_without_run_id:
            from datetime import timedelta
            logs_without_run_id.sort(key=lambda x: x.started_at if x.started_at else datetime.min, reverse=True)
            
            current_group = []
            current_group_start = None
            
            for log in logs_without_run_id:
                if not log.started_at:
                    continue
                
                if current_group_start is None:
                    # Start a new group
                    current_group = [log]
                    current_group_start = log.started_at
                else:
                    # Check if this log is within 5 minutes of the group start
                    time_diff = abs((log.started_at - current_group_start).total_seconds())
                    if time_diff <= 300:  # 5 minutes
                        current_group.append(log)
                    else:
                        # Save current group and start a new one
                        if current_group:
                            # Use a negative "virtual" sync_run_id to distinguish from real ones
                            virtual_run_id = -len(logs_by_run_id) - 1
                            logs_by_run_id[virtual_run_id] = current_group
                        current_group = [log]
                        current_group_start = log.started_at
            
            # Don't forget the last group
            if current_group:
                virtual_run_id = -len(logs_by_run_id) - 1
                logs_by_run_id[virtual_run_id] = current_group
        
        # Second pass: create sync_runs with correct sync_mode
        for run_id, run_logs in logs_by_run_id.items():
            # Determine sync_mode from all logs for this run
            # Count sync_modes - prefer 'incremental' if there's a mix (more specific)
            sync_mode_counts = {}
            for log in run_logs:
                if log.sync_mode:
                    sync_mode_counts[log.sync_mode] = sync_mode_counts.get(log.sync_mode, 0) + 1
            
            # Determine the sync_mode
            if sync_mode_counts:
                # If there's a mix, prefer 'incremental' (more specific)
                if 'incremental' in sync_mode_counts:
                    determined_sync_mode = 'incremental'
                else:
                    # Use the most common mode
                    determined_sync_mode = max(sync_mode_counts.items(), key=lambda x: x[1])[0]
            else:
                # Fallback: check job manager if sync is still running
                determined_sync_mode = None
                if run_id in jobs_by_sync_run_id:
                    determined_sync_mode = jobs_by_sync_run_id[run_id].get('sync_mode', 'full')
                else:
                    determined_sync_mode = 'full'  # Default fallback
            
            # Use the earliest started_at from all logs
            earliest_started = min((log.started_at for log in run_logs if log.started_at), default=None)
            
            # Determine status using database logs only (will be set below)
            sync_runs[run_id] = {
                'sync_run_id': run_id,
                'sync_mode': determined_sync_mode,
                'started_at': earliest_started.isoformat() if earliest_started else None,
                'completed_at': None,  # Will be set below
                'status': 'running',  # Temporary, will be determined from logs below
                'sync_types': [],
                '_latest_completed_at': None  # Temporary: keep as datetime for comparison
            }
            
            # Process all logs for this run
            for log in run_logs:
                # Update completed_at if this is the latest (keep as datetime for now)
                if log.completed_at:
                    if not sync_runs[run_id]['_latest_completed_at'] or log.completed_at > sync_runs[run_id]['_latest_completed_at']:
                        sync_runs[run_id]['_latest_completed_at'] = log.completed_at
                
                # Add sync type info
                sync_runs[run_id]['sync_types'].append({
                    'type': log.sync_type,
                    'records_processed': log.records_processed,
                    'records_created': log.records_created,
                    'records_updated': log.records_updated,
                    'status': log.status
                })
            
            # Convert completed_at to ISO string now (once, not in loop)
            if sync_runs[run_id]['_latest_completed_at']:
                sync_runs[run_id]['completed_at'] = sync_runs[run_id]['_latest_completed_at'].isoformat()
            del sync_runs[run_id]['_latest_completed_at']  # Remove temporary field
        
        # Determine overall status for each sync_run - use database logs as source of truth
        # Do this after collecting all logs to be more efficient
        for run_id, sync_run in sync_runs.items():
            # Skip if this is a job without sync_run_id
            if not isinstance(run_id, int):
                continue
            
            # Get logs for this run
            run_logs = logs_by_run_id.get(run_id, [])
            sync_mode = sync_run.get('sync_mode', 'full')
            
            # Determine status using database logs only (source of truth)
            status = determine_sync_status(run_id, run_logs, sync_mode)
            sync_run['status'] = status
            
            # If running, add job_id for progress tracking (if available from job manager)
            if status == 'running':
                if run_id in active_sync_run_ids:
                    # Ensure job_id is set for running syncs
                    if run_id in sync_run_id_to_jobs and sync_run_id_to_jobs[run_id]:
                        sync_run['job_id'] = sync_run_id_to_jobs[run_id][0]
                elif run_id in jobs_by_sync_run_id:
                    sync_run['job_id'] = jobs_by_sync_run_id[run_id]['job_id']
        
        # Add active jobs that don't have sync_run_id yet AND don't have any database logs
        # (jobs that just started before any sync logs were written)
        # Optimize: Create a lookup map of log start times to sync_run_ids for O(1) lookup
        log_start_times = {}
        for log in all_logs:
            if log.started_at and log.sync_run_id:
                # Round to nearest 10 seconds for matching (within 10 second window)
                rounded_time = int(log.started_at.timestamp() / 10) * 10
                if rounded_time not in log_start_times:
                    log_start_times[rounded_time] = []
                log_start_times[rounded_time].append((log.sync_run_id, log.started_at))
        
        for job_id, job in jobs_without_sync_run_id.items():
            # Only add if this job hasn't been added via database logs
            # We check by comparing started_at times - if a log exists with similar start time, skip
            job_start = job.get('started_at')
            if job_start:
                # Convert job_start to datetime if it's a string
                if isinstance(job_start, str):
                    try:
                        job_start = datetime.fromisoformat(job_start.replace('Z', '+00:00'))
                    except:
                        pass
                
                # Check if there's a log with similar start time (within 10 seconds)
                has_matching_log = False
                if isinstance(job_start, datetime):
                    # Use optimized lookup instead of scanning all_logs
                    rounded_time = int(job_start.timestamp() / 10) * 10
                    # Check rounded time and adjacent buckets (±10 seconds)
                    for check_time in [rounded_time - 10, rounded_time, rounded_time + 10]:
                        if check_time in log_start_times:
                            for sync_run_id, log_start in log_start_times[check_time]:
                                time_diff = abs((log_start - job_start).total_seconds())
                                if time_diff < 10:
                                    has_matching_log = True
                                    # Also mark this sync_run as running if it's not already
                                    if sync_run_id in sync_runs:
                                        sync_runs[sync_run_id]['status'] = 'running'
                                        sync_runs[sync_run_id]['job_id'] = job_id
                                    break
                            if has_matching_log:
                                break
                
                if not has_matching_log:
                    # This is a job that just started, hasn't generated sync_run_id yet
                    sync_runs[f'job_{job_id}'] = {
                        'sync_run_id': None,  # Will be set later
                        'job_id': job_id,
                        'sync_mode': job['sync_mode'],
                        'started_at': job_start.isoformat() if isinstance(job_start, datetime) else str(job_start),
                        'completed_at': None,
                        'status': 'running',
                        'sync_types': []
                    }
        
        # Final pass: ensure job_id is set for running syncs (if available)
        # Status is already determined from database logs above, so we only update job_id here
        for sync_run_id in active_sync_run_ids:
            if sync_run_id in sync_runs and sync_runs[sync_run_id]['status'] == 'running':
                if sync_run_id in sync_run_id_to_jobs and sync_run_id_to_jobs[sync_run_id]:
                    sync_runs[sync_run_id]['job_id'] = sync_run_id_to_jobs[sync_run_id][0]
        
        # Convert to list and sort by started_at
        result = list(sync_runs.values())
        result.sort(key=lambda x: x['started_at'] or '', reverse=True)
        
        return jsonify(result)
        
    finally:
        session.close()


@sync_bp.route('/api/running-status')
@approved_required
def api_running_status():
    """
    Get only running sync status - lightweight endpoint for polling.
    
    Returns minimal data for syncs that are currently running.
    Uses database logs as source of truth to determine running status.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        # Get recent logs (last 100 to find running syncs)
        # Query logs with sync_run_id to find potential running syncs
        recent_logs = session.query(SyncLog).filter(
            SyncLog.sync_run_id.isnot(None)
        ).order_by(SyncLog.started_at.desc()).limit(100).all()
        
        # Group by sync_run_id
        logs_by_run_id = {}
        for log in recent_logs:
            if log.sync_run_id not in logs_by_run_id:
                logs_by_run_id[log.sync_run_id] = []
            logs_by_run_id[log.sync_run_id].append(log)
        
        running_syncs = []
        
        # Check each sync run to see if it's still running
        for run_id, run_logs in logs_by_run_id.items():
            # Determine sync_mode from logs
            sync_mode = run_logs[0].sync_mode if run_logs and run_logs[0].sync_mode else 'full'
            
            # Determine status using database logs only (source of truth)
            status = determine_sync_status(run_id, run_logs, sync_mode)
            
            if status == 'running':
                # Only return running syncs
                earliest_started = min((log.started_at for log in run_logs if log.started_at), default=None)
                latest_completed = max((log.completed_at for log in run_logs if log.completed_at), default=None)
                
                sync_run = {
                    'sync_run_id': run_id,
                    'sync_mode': sync_mode,
                    'started_at': earliest_started.isoformat() if earliest_started else None,
                    'completed_at': latest_completed.isoformat() if latest_completed else None,
                    'status': 'running',
                    'sync_types': [{
                        'type': log.sync_type,
                        'records_processed': log.records_processed,
                        'records_created': log.records_created,
                        'records_updated': log.records_updated,
                        'status': log.status
                    } for log in run_logs]
                }
                
                # Add job_id if available (for progress polling)
                job_manager = get_job_manager()
                job = job_manager.get_job_by_sync_run_id(run_id)
                if job:
                    sync_run['job_id'] = job['job_id']
                
                running_syncs.append(sync_run)
        
        # Also check job manager for jobs without sync_run_id yet (just started, no logs yet)
        job_manager = get_job_manager()
        active_jobs = job_manager.get_all_active_jobs()
        jobs_without_sync_run_id = {job_id: job for job_id, job in active_jobs.items() if job.get('sync_run_id') is None}
        
        for job_id, job in jobs_without_sync_run_id.items():
            job_start = job.get('started_at')
            if isinstance(job_start, str):
                try:
                    job_start = datetime.fromisoformat(job_start.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
            
            running_syncs.append({
                'sync_run_id': None,
                'job_id': job_id,
                'sync_mode': job.get('sync_mode', 'full'),
                'started_at': job_start.isoformat() if isinstance(job_start, datetime) else str(job_start),
                'completed_at': None,
                'status': 'running',
                'sync_types': []
            })
        
        # Sort by started_at (newest first)
        running_syncs.sort(key=lambda x: x['started_at'] or '', reverse=True)
        
        return jsonify(running_syncs)
        
    finally:
        session.close()


@sync_bp.route('/<int:sync_run_id>/detail')
@approved_required
def sync_detail_page(sync_run_id):
    """Render sync detail page"""
    return render_template('sync/detail.html', sync_run_id=sync_run_id)


@sync_bp.route('/job/<job_id>/detail')
@approved_required
def sync_detail_page_by_job(job_id):
    """Render sync detail page by job_id (for jobs that don't have sync_run_id yet)"""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        return "Job not found", 404
    
    # If job has sync_run_id, redirect to sync_run_id detail page
    if job.get('sync_run_id'):
        from flask import redirect, url_for
        return redirect(url_for('sync.sync_detail_page', sync_run_id=job['sync_run_id']))
    
    # Otherwise, render with job_id (will show progress only)
    return render_template('sync/detail.html', sync_run_id=None, job_id=job_id)


@sync_bp.route('/api/job/<job_id>/detail')
@approved_required
def api_sync_detail_by_job(job_id):
    """Get sync detail by job_id (for jobs without sync_run_id yet)"""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    # If job has sync_run_id, redirect to sync_run_id API
    if job.get('sync_run_id'):
        from flask import redirect
        return redirect(f'/sync/api/{job["sync_run_id"]}/detail')
    
    # Return job progress
    progress = job.get('progress', {})
    return jsonify({
        'sync_run_id': None,
        'job_id': job_id,
        'sync_mode': job['sync_mode'],
        'started_at': job['started_at'].isoformat() if job.get('started_at') else None,
        'completed_at': None,
        'status': job['status'],
        'is_running': job['status'] in ('pending', 'running'),
        'progress': {
            'phase': progress.get('phase'),
            'processed': progress.get('processed', 0),
            'total': progress.get('total', 0),
            'created': progress.get('created', 0),
            'updated': progress.get('updated', 0),
            'errors': progress.get('errors', 0),
            'percentage': progress.get('percentage', 0.0)
        },
        'listings': []
    })


@sync_bp.route('/api/<int:sync_run_id>/detail')
@approved_required
def api_sync_detail(sync_run_id):
    """Get sync detail with per-listing breakdown"""
    db_path = get_database_path()
    job_manager = get_job_manager()
    
    # Note: Database tables should already be initialized at app startup
    # No need to call init_models() on every request - it's expensive
    session = get_session(db_path)
    
    try:
        # Get all sync logs for this sync_run_id - this is the source of truth
        logs = session.query(SyncLog).filter(
            SyncLog.sync_run_id == sync_run_id
        ).order_by(SyncLog.started_at).all()
        
        # If no logs found in database, check job manager (for cases where sync_run_id wasn't saved)
        if not logs:
            job_by_sync_run_id = job_manager.get_job_by_sync_run_id(sync_run_id)
            if job_by_sync_run_id and job_by_sync_run_id.get('results'):
                # Use job results to construct response
                results = job_by_sync_run_id.get('results', {})
                job_status = job_by_sync_run_id.get('status', 'unknown')
                
                # Build summary from job results
                summary_by_type = {}
                for sync_type in ['listings', 'reservations', 'messages', 'reviews', 'guests']:
                    if sync_type in results:
                        result_data = results[sync_type]
                        if isinstance(result_data, dict):
                            summary_by_type[sync_type] = {
                                'created': result_data.get('records_created', 0) or 0,
                                'updated': result_data.get('records_updated', 0) or 0,
                                'errors': len(result_data.get('errors', [])) if result_data.get('errors') else 0,
                                'processed': result_data.get('records_processed', 0) or 0
                            }
                
                return jsonify({
                    'sync_run_id': sync_run_id,
                    'sync_mode': job_by_sync_run_id.get('sync_mode', 'unknown'),
                    'started_at': job_by_sync_run_id.get('started_at').isoformat() if job_by_sync_run_id.get('started_at') else None,
                    'completed_at': job_by_sync_run_id.get('completed_at').isoformat() if job_by_sync_run_id.get('completed_at') else None,
                    'status': 'error' if job_status == 'error' else ('completed' if job_status == 'completed' else 'running'),
                    'is_running': job_status in ('pending', 'running'),
                    'listings': [],
                    'summary': summary_by_type
                })
            
            return jsonify({'error': 'Sync run not found'}), 404
        
        # Determine sync_mode from logs
        sync_mode = logs[0].sync_mode if logs and logs[0].sync_mode else 'full'
        
        # Determine status using database logs only (source of truth)
        status = determine_sync_status(sync_run_id, logs, sync_mode)
        is_running = (status == 'running')
        
        # Get job manager only for progress info (if running)
        # Job manager is optional - only used for progress updates, not status determination
        active_job = None
        if is_running:
            job_by_sync_run_id = job_manager.get_job_by_sync_run_id(sync_run_id)
            if job_by_sync_run_id and job_by_sync_run_id.get('status') in ('pending', 'running'):
                active_job = job_by_sync_run_id
        
        # Aggregate listing_stats from all sync types
        aggregated_stats = {}  # {listing_id: {messages: X, reviews: Y, reservations: Z, guests: W}}
        
        for log in logs:
            stats = log.get_listing_stats()
            for listing_id, listing_data in stats.items():
                listing_id_int = int(listing_id) if isinstance(listing_id, str) else listing_id
                if listing_id_int not in aggregated_stats:
                    aggregated_stats[listing_id_int] = {
                        'messages': 0,
                        'reviews': 0,
                        'reservations': 0,
                        'guests': 0
                    }
                
                # Merge stats
                if 'messages' in listing_data:
                    aggregated_stats[listing_id_int]['messages'] += listing_data['messages']
                if 'reviews' in listing_data:
                    aggregated_stats[listing_id_int]['reviews'] += listing_data['reviews']
                if 'reservations' in listing_data:
                    aggregated_stats[listing_id_int]['reservations'] += listing_data['reservations']
                if 'guests' in listing_data:
                    aggregated_stats[listing_id_int]['guests'] += listing_data['guests']
        
        # Get listing details
        listing_ids = list(aggregated_stats.keys())
        listings = session.query(Listing).filter(
            Listing.listing_id.in_(listing_ids)
        ).all()
        
        listing_map = {l.listing_id: l for l in listings}
        
        # Build result
        listings_data = []
        for listing_id, stats in aggregated_stats.items():
            listing = listing_map.get(listing_id)
            listings_data.append({
                'listing_id': listing_id,
                'name': listing.name if listing else f'Listing {listing_id}',
                'address': listing.address if listing else None,
                'messages': stats['messages'],
                'reviews': stats['reviews'],
                'reservations': stats['reservations'],
                'guests': stats['guests']
            })
        
        # Aggregate summary by sync_type (listings, reservations, messages, reviews, guests)
        summary_by_type = {}  # {sync_type: {created, updated, errors, processed}}
        for log in logs:
            sync_type = log.sync_type
            if sync_type not in summary_by_type:
                summary_by_type[sync_type] = {
                    'created': 0,
                    'updated': 0,
                    'errors': 0,
                    'processed': 0
                }
            
            summary_by_type[sync_type]['created'] += log.records_created or 0
            summary_by_type[sync_type]['updated'] += log.records_updated or 0
            summary_by_type[sync_type]['processed'] += log.records_processed or 0
            
            # Count errors
            error_list = log.get_errors_list()
            summary_by_type[sync_type]['errors'] += len(error_list) if error_list else 0
        
        # Get sync run metadata
        first_log = logs[0]
        # Find the latest log (by completed_at if available, otherwise by started_at)
        last_log = max(logs, key=lambda x: x.completed_at if x.completed_at else (x.started_at if x.started_at else datetime.min))
        
        result = {
            'sync_run_id': sync_run_id,
            'sync_mode': first_log.sync_mode,
            'started_at': first_log.started_at.isoformat() if first_log.started_at else None,
            'completed_at': last_log.completed_at.isoformat() if last_log.completed_at and not is_running else None,
            'status': status,
            'is_running': is_running,
            'listings': listings_data if not is_running else [],  # Don't show listings while running
            'summary': summary_by_type  # Summary by sync type
        }
        
        # Add job info if running
        if is_running:
            if active_job:
                result['job_id'] = active_job['job_id']
                # Get progress with current_item
                progress = active_job.get('progress', {})
                result['progress'] = {
                    'phase': progress.get('phase', 'Initializing...'),
                    'processed': progress.get('processed', 0),
                    'total': progress.get('total', 0),
                    'created': progress.get('created', 0),
                    'updated': progress.get('updated', 0),
                    'errors': progress.get('errors', 0),
                    'percentage': progress.get('percentage', 0.0),
                    'current_item': progress.get('current_item')
                }
            else:
                # Job not found but sync is running (has incomplete logs)
                # Create a basic progress object from logs
                incomplete_logs = [log for log in logs if log.completed_at is None]
                if incomplete_logs:
                    latest_incomplete = max(incomplete_logs, key=lambda x: x.started_at if x.started_at else datetime.min)
                    result['progress'] = {
                        'phase': f'Syncing {latest_incomplete.sync_type}...',
                        'processed': latest_incomplete.records_processed or 0,
                        'total': latest_incomplete.records_processed or 0,  # We don't know total yet
                        'created': latest_incomplete.records_created or 0,
                        'updated': latest_incomplete.records_updated or 0,
                        'errors': len(latest_incomplete.get_errors_list()) if latest_incomplete.errors else 0,
                        'percentage': 0.0,
                        'current_item': None
                    }
        
        return jsonify(result)
        
    finally:
        session.close()


@sync_bp.route('/api/full', methods=['POST'])
@admin_required
def api_trigger_full_sync():
    """Trigger full sync (async)"""
    job_manager = get_job_manager()
    job_id = job_manager.create_job('full')
    
    # Start background thread
    thread = threading.Thread(target=run_sync_async, args=(job_id, 'full'))
    thread.daemon = True
    thread.start()
    
    return jsonify({'job_id': job_id, 'status': 'pending'})


@sync_bp.route('/api/incremental', methods=['POST'])
@admin_required
def api_trigger_incremental_sync():
    """Trigger incremental sync (async)"""
    try:
        job_manager = get_job_manager()
        job_id = job_manager.create_job('incremental')
        
        # Start background thread
        thread = threading.Thread(target=run_sync_async, args=(job_id, 'incremental'))
        thread.daemon = True
        thread.start()
        
        logger.info(f"Incremental sync started with job_id: {job_id}")
        return jsonify({'job_id': job_id, 'status': 'pending'})
    except Exception as e:
        logger.error(f"Error starting incremental sync: {str(e)}", exc_info=True)
        return jsonify({'error': f'Failed to start incremental sync: {str(e)}'}), 500


@sync_bp.route('/api/status/<job_id>')
@approved_required
def api_sync_status(job_id):
    """Get sync progress status"""
    job_manager = get_job_manager()
    job = job_manager.get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    progress = job.get('progress', {})
    
    # Ensure progress has all required fields
    progress_data = {
        'phase': progress.get('phase', 'Initializing...'),
        'processed': progress.get('processed', 0),
        'total': progress.get('total', 0),
        'created': progress.get('created', 0),
        'updated': progress.get('updated', 0),
        'errors': progress.get('errors', 0),
        'percentage': progress.get('percentage', 0.0),
        'current_item': progress.get('current_item') or progress.get('item_name')
    }
    
    return jsonify({
        'status': job['status'],
        'progress': progress_data,
        'results': job.get('results'),
        'error': job.get('error')
    })


def register_sync_routes(app):
    """Register sync routes with Flask app"""
    app.register_blueprint(sync_bp)

