#!/usr/bin/env python3
"""
Sync job manager for async execution of sync operations.
Uses database table sync_jobs for persistence.
"""

import uuid
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional
from threading import Lock
from database.models import SyncJob, get_session
from database.schema import get_database_path
from sqlalchemy.orm.attributes import flag_modified


class SyncJobManager:
    """Thread-safe manager for sync jobs using database persistence"""
    
    def __init__(self):
        self.lock = Lock()
    
    def _job_to_dict(self, job: SyncJob) -> Dict:
        """Convert SyncJob model to dictionary format"""
        if not job:
            return None
        return {
            'job_id': job.job_id,
            'sync_mode': job.sync_mode,
            'sync_run_id': job.sync_run_id,
            'status': job.status,
            'progress': job.get_progress(),
            'results': None,  # Not stored in database, kept for compatibility
            'error': job.error_message,
            'started_at': job.started_at,
            'completed_at': job.completed_at
        }
    
    def create_job(self, sync_mode: str) -> str:
        """
        Create a new sync job in database.
        
        Args:
            sync_mode: 'full' or 'incremental'
            
        Returns:
            job_id: Unique job identifier
        """
        job_id = str(uuid.uuid4())
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            job = SyncJob(
                job_id=job_id,
                sync_run_id=0,  # Will be set when sync starts
                sync_mode=sync_mode,
                status='pending',
                progress={
                    'phase': None,
                    'processed': 0,
                    'total': 0,
                    'created': 0,
                    'updated': 0,
                    'errors': 0,
                    'percentage': 0.0
                },
                error_message=None,
                started_at=datetime.utcnow(),
                completed_at=None,
                updated_at=datetime.utcnow()
            )
            session.add(job)
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
        
        return job_id
    
    def set_sync_run_id(self, job_id: str, sync_run_id: int):
        """Set sync_run_id for a job"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            with self.lock:
                job = session.query(SyncJob).filter_by(job_id=job_id).first()
                if job:
                    job.sync_run_id = sync_run_id
                    job.updated_at = datetime.utcnow()
                    session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def get_job_by_sync_run_id(self, sync_run_id: int) -> Optional[Dict]:
        """Get job by sync_run_id - always gets fresh data from database"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            # Query with merge=False to ensure we get fresh data
            job = session.query(SyncJob).filter_by(sync_run_id=sync_run_id).first()
            if job:
                # Expire the object to force reload from database on next access
                session.expire(job)
                # Access progress to trigger reload
                _ = job.progress
            return self._job_to_dict(job)
        finally:
            session.close()
    
    def get_all_active_jobs(self) -> Dict[str, Dict]:
        """
        Get all active (pending or running) jobs from database.
        
        Note: Jobs remain 'running' until all sync logs are confirmed complete in the database.
        This allows the UI to correctly show sync status based on database logs rather than
        premature job completion.
        """
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            jobs = session.query(SyncJob).filter(
                SyncJob.status.in_(['pending', 'running'])
            ).all()
            return {job.job_id: self._job_to_dict(job) for job in jobs}
        finally:
            session.close()
    
    def get_job(self, job_id: str) -> Optional[Dict]:
        """Get job status by job_id - always gets fresh data from database"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            # Query with merge=False to ensure we get fresh data
            job = session.query(SyncJob).filter_by(job_id=job_id).first()
            if job:
                # Expire the object to force reload from database on next access
                session.expire(job)
                # Access progress to trigger reload
                _ = job.progress
            return self._job_to_dict(job)
        finally:
            session.close()
    
    def get_all_jobs(self) -> Dict[str, Dict]:
        """Get all jobs (including completed ones) from database"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            jobs = session.query(SyncJob).all()
            return {job.job_id: self._job_to_dict(job) for job in jobs}
        finally:
            session.close()
    
    def update_job_status(self, job_id: str, status: str, **kwargs):
        """Update job status and optional fields"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            with self.lock:
                job = session.query(SyncJob).filter_by(job_id=job_id).first()
                if job:
                    job.status = status
                    job.updated_at = datetime.utcnow()
                    
                    # Handle error_message from kwargs
                    if 'error' in kwargs:
                        job.error_message = kwargs['error']
                    elif 'error_message' in kwargs:
                        job.error_message = kwargs['error_message']
                    
                    if status in ('completed', 'error'):
                        job.completed_at = datetime.utcnow()
                    
                    session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def update_progress(self, job_id: str, **progress_data):
        """Update job progress in database"""
        db_path = get_database_path()
        session = get_session(db_path)
        
        try:
            with self.lock:
                job = session.query(SyncJob).filter_by(job_id=job_id).first()
                if job:
                    progress = job.get_progress()
                    progress.update(progress_data)
                    
                    # Calculate percentage
                    if progress.get('total', 0) > 0:
                        progress['percentage'] = (progress.get('processed', 0) / progress['total']) * 100
                    else:
                        progress['percentage'] = 0.0
                    
                    # Store current_item for display
                    if 'current_item' in progress_data:
                        progress['current_item'] = progress_data['current_item']
                    
                    job.set_progress(progress)
                    job.updated_at = datetime.utcnow()
                    # Mark progress field as modified so SQLAlchemy detects the change
                    flag_modified(job, 'progress')
                    session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()
    
    def set_results(self, job_id: str, results: Dict):
        """Set job results (stored in progress field for now)"""
        # Results are not stored separately in database
        # They can be retrieved from sync_logs if needed
        pass
    
    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove jobs older than max_age_hours from database"""
        db_path = get_database_path()
        session = get_session(db_path)
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        try:
            with self.lock:
                old_jobs = session.query(SyncJob).filter(
                    SyncJob.started_at < cutoff
                ).all()
                for job in old_jobs:
                    session.delete(job)
                session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()


# Global job manager instance
_job_manager = SyncJobManager()


def get_job_manager() -> SyncJobManager:
    """Get the global job manager instance"""
    return _job_manager

