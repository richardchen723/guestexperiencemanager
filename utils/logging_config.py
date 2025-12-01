#!/usr/bin/env python3
"""
Logging configuration for the Hostaway sync system.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from config import VERBOSE


def setup_logging(log_file: Optional[str] = None) -> None:
    """
    Configure logging for the application.
    
    Args:
        log_file: Optional path to log file. If None, logs only to console.
    """
    # Create logs directory if logging to file
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Use DEBUG level for detailed diagnostics
    # This ensures we capture all errors and debug information
    log_level = logging.DEBUG
    
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
