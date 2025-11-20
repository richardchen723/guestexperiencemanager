#!/usr/bin/env python3
"""
Logging configuration for the Hostaway sync system.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from config import VERBOSE


def setup_logging(log_file: Optional[str] = None) -> None:
    """
    Configure logging for the application.
    Detects Vercel environment and uses stdout logging there.
    
    Args:
        log_file: Optional path to log file. If None, logs only to console.
                  In Vercel environment, file logging is disabled automatically.
    """
    # Detect Vercel environment
    is_vercel = os.environ.get('VERCEL', '0') == '1'
    
    # In Vercel, always use stdout/stderr (no file system writes)
    if is_vercel:
        log_file = None
    
    # Create logs directory if logging to file
    if log_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If we can't create log directory (e.g., read-only filesystem), disable file logging
            log_file = None
    
    # Always use DEBUG level for detailed diagnostics (can be overridden by VERBOSE)
    # This ensures we capture all errors and debug information
    log_level = logging.DEBUG if VERBOSE else logging.DEBUG  # Always DEBUG for now
    
    # Configure root logger
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    # Enhanced format with more context
    log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers
    )
    
    # Set specific loggers to appropriate levels
    # Keep sync-related loggers at DEBUG for detailed diagnostics
    logging.getLogger('sync').setLevel(logging.DEBUG)
    logging.getLogger('dashboard.sync').setLevel(logging.DEBUG)
    
    # Reduce noise from third-party libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.INFO)  # Flask request logs
