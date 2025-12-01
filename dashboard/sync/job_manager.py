#!/usr/bin/env python3
"""
Sync job manager for async execution of sync operations.
"""

import uuid
import threading
from datetime import datetime
from typing import Dict, Optional
from threading import Lock


class SyncJobManager:
    """Thread-safe manager for sync jobs"""
    
    def __init__(self):
        self.jobs: Dict[str, Dict] = {}
        self.lock = Lock()
    
    def create_job(self, sync_mode: str) -> str:
        """
        Create a new sync job.
        
        Args:
            sync_mode: 'full' or 'incremental'
            
        Returns:
            job_id: Unique job identifier
        """
        job_id = str(uuid.uuid4())
        
        with self.lock:
            self.jobs[job_id] = {
                'job_id': job_id,
                'sync_mode': sync_mode,
                'sync_run_id': None,  # Will be set when sync starts
                'status': 'pending',
                'progress': {
                    'phase': None,
                    'processed': 0,
                    'total': 0,
                    'created': 0,
                    'updated': 0,
                    'errors': 0,
                    'percentage': 0.0
                },
                'results': None,
                'error': None,
                'started_at': datetime.utcnow(),
                'completed_at': None
            }
        
        return job_id
    
    def set_sync_run_id(self, job_id: str, sync_run_id: int):
        """Set sync_run_id for a job"""
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]['sync_run_id'] = sync_run_id
    
    def get_job_by_sync_run_id(self, sync_run_id: int) -> Optional[Dict]:
        """Get job by sync_run_id"""
        with self.lock:
            for job in self.jobs.values():
                if job.get('sync_run_id') == sync_run_id:
                    return job
        return None
    
    def get_all_active_jobs(self) -> Dict[str, Dict]:
        """
        Get all active (pending or running) jobs.
        
        Note: Jobs remain 'running' until all sync logs are confirmed complete in the database.
        This allows the UI to correctly show sync status based on database logs rather than
        premature job completion.
        """
        with self.lock:
            return {
                job_id: job for job_id, job in self.jobs.items()
                if job['status'] in ('pending', 'running')
            }
    
    def get_job(self, job_id: str) -> Optional[Dict]:
        """Get job status by job_id"""
        with self.lock:
            return self.jobs.get(job_id)
    
    def get_all_jobs(self) -> Dict[str, Dict]:
        """Get all jobs (including completed ones)"""
        with self.lock:
            return self.jobs.copy()
    
    def update_job_status(self, job_id: str, status: str, **kwargs):
        """Update job status and optional fields"""
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]['status'] = status
                for key, value in kwargs.items():
                    self.jobs[job_id][key] = value
                if status in ('completed', 'error'):
                    self.jobs[job_id]['completed_at'] = datetime.utcnow()
    
    def update_progress(self, job_id: str, **progress_data):
        """Update job progress"""
        with self.lock:
            if job_id in self.jobs:
                progress = self.jobs[job_id]['progress']
                progress.update(progress_data)
                
                # Calculate percentage
                if progress.get('total', 0) > 0:
                    progress['percentage'] = (progress.get('processed', 0) / progress['total']) * 100
                else:
                    progress['percentage'] = 0.0
                
                # Store current_item for display
                if 'current_item' in progress_data:
                    progress['current_item'] = progress_data['current_item']
    
    def set_results(self, job_id: str, results: Dict):
        """Set job results"""
        with self.lock:
            if job_id in self.jobs:
                self.jobs[job_id]['results'] = results
    
    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove jobs older than max_age_hours"""
        cutoff = datetime.utcnow().timestamp() - (max_age_hours * 3600)
        
        with self.lock:
            to_remove = []
            for job_id, job in self.jobs.items():
                started = job.get('started_at')
                if started and started.timestamp() < cutoff:
                    to_remove.append(job_id)
            
            for job_id in to_remove:
                del self.jobs[job_id]


# Global job manager instance
_job_manager = SyncJobManager()


def get_job_manager() -> SyncJobManager:
    """Get the global job manager instance"""
    return _job_manager

