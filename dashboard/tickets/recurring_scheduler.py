#!/usr/bin/env python3
"""
Scheduled job runner for recurring tasks.
Can be called via systemd timer, cron, or manually.
"""

import sys
import os
import logging

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.recurring_tasks import process_recurring_tasks
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def main():
    """Main entry point for recurring tasks scheduler."""
    setup_logging()
    
    logger.info("=" * 60)
    logger.info("Running Recurring Tasks Processor")
    logger.info("=" * 60)
    
    try:
        results = process_recurring_tasks()
        logger.info(f"Processing complete: {results}")
        return 0
    except Exception as e:
        logger.error(f"Fatal error in recurring tasks processor: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

