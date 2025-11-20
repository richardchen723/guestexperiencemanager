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
    In Vercel, always enables DEBUG mode for detailed diagnostics.
    
    Args:
        log_file: Optional path to log file. If None, logs only to console.
                  In Vercel environment, file logging is disabled automatically.
    """
    # Detect Vercel environment
    is_vercel = os.environ.get('VERCEL', '0') == '1'
    
    # In Vercel, always use stdout/stderr (no file system writes)
    if is_vercel:
        log_file = None
        # Force VERBOSE mode in Vercel for debug deployment
        os.environ['VERBOSE'] = 'True'
    
    # Create logs directory if logging to file
    if log_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If we can't create log directory (e.g., read-only filesystem), disable file logging
            log_file = None
    
    # Determine log level: Always DEBUG in Vercel, or if VERBOSE is enabled
    # Reload VERBOSE after potentially setting it above
    from config import VERBOSE
    if is_vercel:
        log_level = logging.DEBUG  # Always DEBUG in Vercel for detailed diagnostics
    else:
        log_level = logging.DEBUG if VERBOSE else logging.INFO
    
    # Configure root logger
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    # Enhanced format with more context (includes function name for better debugging)
    log_format = '%(asctime)s [%(levelname)s] %(name)s:%(filename)s:%(lineno)d:%(funcName)s - %(message)s'
    
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=handlers,
        force=True  # Force reconfiguration if already set
    )
    
    # Set specific loggers to appropriate levels
    # Keep sync-related loggers at DEBUG for detailed diagnostics
    logging.getLogger('sync').setLevel(logging.DEBUG)
    logging.getLogger('dashboard.sync').setLevel(logging.DEBUG)
    logging.getLogger('dashboard').setLevel(logging.DEBUG)
    
    # In Vercel, enable DEBUG for API client to see all requests/responses
    if is_vercel:
        logging.getLogger('sync.api_client').setLevel(logging.DEBUG)
        logging.getLogger('requests').setLevel(logging.DEBUG)
        logging.getLogger('urllib3').setLevel(logging.DEBUG)  # Show connection details
    else:
        # Reduce noise from third-party libraries in local dev
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
    
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)  # Keep SQLAlchemy quiet
    logging.getLogger('werkzeug').setLevel(logging.INFO)  # Flask request logs
    
    # Log that debug mode is enabled
    logger = logging.getLogger(__name__)
    if is_vercel:
        logger.info("=" * 80)
        logger.info("DEBUG MODE ENABLED FOR VERCEL DEPLOYMENT")
        logger.info("=" * 80)
