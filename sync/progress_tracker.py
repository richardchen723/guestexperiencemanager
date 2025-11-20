#!/usr/bin/env python3
"""
Progress tracker for sync operations with real-time dashboard.
"""

import sys
import time
from datetime import datetime
from typing import Dict, Optional
from threading import Lock


class ProgressTracker:
    """Thread-safe progress tracker with terminal dashboard"""
    
    def __init__(self):
        self.lock = Lock()
        self.current_phase = None
        self.current_item = None
        self.total_items = 0
        self.processed_items = 0
        self.created_count = 0
        self.updated_count = 0
        self.error_count = 0
        self.start_time = None
        self.last_update = None
        
    def start_phase(self, phase_name: str, total_items: int = 0):
        """Start a new sync phase"""
        with self.lock:
            self.current_phase = phase_name
            self.total_items = total_items
            self.processed_items = 0
            self.created_count = 0
            self.updated_count = 0
            self.error_count = 0
            self.start_time = time.time()
            self._render()
    
    def update_total(self, new_total: int):
        """Update the total items count dynamically (useful for unknown totals)"""
        with self.lock:
            self.total_items = new_total
            self._render()
    
    def update_item(self, item_name: str, status: str = "processing"):
        """Update current item being processed"""
        with self.lock:
            self.current_item = item_name
            self._render()
    
    def increment(self, created: bool = False, updated: bool = False, error: bool = False, item_name: Optional[str] = None, allow_exceed_total: bool = False):
        """Increment progress counters
        
        Args:
            created: Whether a record was created
            updated: Whether a record was updated
            error: Whether an error occurred
            item_name: Optional item name to display
            allow_exceed_total: If True, allow incrementing past total (for dynamic totals
        """
        with self.lock:
            # Only increment if we haven't exceeded total (safety check)
            # But allow exceeding if explicitly requested (for dynamic totals)
            if allow_exceed_total or self.total_items == 0 or self.processed_items < self.total_items:
                self.processed_items += 1
            elif self.total_items > 0 and self.processed_items >= self.total_items:
                # Already at or past total, don't increment further (but still update counts)
                pass
            if created:
                self.created_count += 1
            if updated:
                self.updated_count += 1
            if error:
                self.error_count += 1
            if item_name:
                self.current_item = item_name
            self._render()
    
    def _render(self):
        """Render the progress dashboard"""
        # Clear previous lines (we'll use carriage return for in-place updates)
        sys.stdout.write('\r' + ' ' * 100 + '\r')
        
        if not self.current_phase:
            return
        
        # Calculate progress percentage
        if self.total_items > 0:
            progress_pct = (self.processed_items / self.total_items) * 100
            progress_bar = self._create_progress_bar(progress_pct, 40)
            progress_text = f"{self.processed_items}/{self.total_items} ({progress_pct:.1f}%)"
        else:
            progress_bar = ""
            progress_text = f"{self.processed_items} processed"
        
        # Calculate elapsed time
        if self.start_time:
            elapsed = time.time() - self.start_time
            elapsed_str = f"{elapsed:.1f}s"
        else:
            elapsed_str = "0s"
        
        # Build status line
        status_parts = [
            f"[{self.current_phase}]",
            progress_bar,
            progress_text,
            f"✓{self.created_count}",
            f"↻{self.updated_count}",
            f"✗{self.error_count}",
            elapsed_str
        ]
        
        if self.current_item:
            # Truncate item name if too long
            item_display = self.current_item[:40] + "..." if len(self.current_item) > 40 else self.current_item
            status_parts.insert(1, item_display)
        
        status_line = " | ".join(status_parts)
        sys.stdout.write(status_line)
        sys.stdout.flush()
    
    def _create_progress_bar(self, percentage: float, width: int = 40) -> str:
        """Create a text-based progress bar"""
        filled = int(width * percentage / 100)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"
    
    def complete_phase(self):
        """Complete the current phase and print summary"""
        with self.lock:
            if self.start_time:
                elapsed = time.time() - self.start_time
            else:
                elapsed = 0
            
            # Clear the progress line
            sys.stdout.write('\r' + ' ' * 100 + '\r')
            
            # Print summary
            summary = (
                f"✓ {self.current_phase} complete: "
                f"{self.processed_items} processed, "
                f"{self.created_count} created, "
                f"{self.updated_count} updated, "
                f"{self.error_count} errors, "
                f"{elapsed:.1f}s"
            )
            print(summary)
            
            # Reset for next phase
            self.current_item = None
    
    def print_summary(self, results: Dict):
        """Print final summary of all sync operations"""
        print("\n" + "=" * 80)
        print("SYNC SUMMARY")
        print("=" * 80)
        
        for sync_type, result in results.items():
            if result.get('status') == 'skipped':
                print(f"  {sync_type.upper()}: SKIPPED")
            elif result.get('status') == 'error':
                print(f"  {sync_type.upper()}: ERROR - {result.get('error', 'Unknown error')}")
            else:
                processed = result.get('records_processed', 0)
                created = result.get('records_created', 0)
                updated = result.get('records_updated', 0)
                errors = len(result.get('errors', []))
                print(f"  {sync_type.upper()}: {processed} processed, {created} created, {updated} updated, {errors} errors")
        
        print("=" * 80)


# Global progress tracker instance
_progress_tracker = ProgressTracker()


def get_progress_tracker() -> ProgressTracker:
    """Get the global progress tracker instance"""
    return _progress_tracker
