#!/usr/bin/env python3
"""
Web-based progress tracker for sync operations.
"""

import time
from typing import Optional
from dashboard.sync.job_manager import get_job_manager


class WebProgressTracker:
    """Progress tracker that stores state for web polling"""
    
    def __init__(self, job_id: str):
        """
        Initialize web progress tracker.
        
        Args:
            job_id: Job ID to track progress for
        """
        self.job_id = job_id
        self.job_manager = get_job_manager()
        self.current_phase = None
        self.current_item = None
        self.total_items = 0
        self.processed_items = 0
        self.created_count = 0
        self.updated_count = 0
        self.error_count = 0
        self.start_time = None
    
    def start_phase(self, phase_name: str, total_items: int = 0):
        """Start a new sync phase"""
        self.current_phase = phase_name
        self.total_items = total_items
        self.processed_items = 0
        self.created_count = 0
        self.updated_count = 0
        self.error_count = 0
        self.start_time = time.time()
        self._update_progress()
    
    def update_total(self, new_total: int):
        """Update the total items count dynamically"""
        self.total_items = new_total
        self._update_progress()
    
    def update_item(self, item_name: str, status: str = "processing"):
        """Update current item being processed"""
        self.current_item = item_name
        self._update_progress()
    
    def increment(self, created: bool = False, updated: bool = False, error: bool = False, 
                  item_name: Optional[str] = None, allow_exceed_total: bool = False):
        """
        Increment progress counters.
        
        Args:
            created: Whether a record was created
            updated: Whether a record was updated
            error: Whether an error occurred
            item_name: Optional item name to display
            allow_exceed_total: If True, allow incrementing past total
        """
        if allow_exceed_total or self.total_items == 0 or self.processed_items < self.total_items:
            self.processed_items += 1
        
        if created:
            self.created_count += 1
        if updated:
            self.updated_count += 1
        if error:
            self.error_count += 1
        if item_name:
            self.current_item = item_name
        
        self._update_progress()
    
    def _update_progress(self):
        """Update progress in job manager (which writes to database)"""
        self.job_manager.update_progress(
            self.job_id,
            phase=self.current_phase,
            processed=self.processed_items,
            total=self.total_items,
            created=self.created_count,
            updated=self.updated_count,
            errors=self.error_count,
            current_item=self.current_item
        )
    
    def complete_phase(self):
        """Complete the current phase"""
        self._update_progress()
        # Reset item name and phase for next phase
        self.current_item = None
        # Note: current_phase is intentionally NOT reset here
        # The next phase will call start_phase() which will set it
    
    def get_progress(self) -> dict:
        """Get current progress state"""
        return {
            'phase': self.current_phase,
            'processed': self.processed_items,
            'total': self.total_items,
            'created': self.created_count,
            'updated': self.updated_count,
            'errors': self.error_count,
            'percentage': (self.processed_items / self.total_items * 100) if self.total_items > 0 else 0.0
        }

