#!/usr/bin/env python3
"""
Database migration system for Hostaway Messages.
All migrations are idempotent and safe to run multiple times.
"""

import os
import logging
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)


def get_main_engine():
    """Get the main database engine (for listings, sync_logs, etc.)"""
    from database.models import get_engine
    from database.schema import get_database_path
    import dashboard.config as config
    
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # PostgreSQL - use DATABASE_URL directly
        return get_engine(None)
    else:
        # SQLite - use config path
        db_path = get_database_path() or config.MAIN_DATABASE_PATH
        return get_engine(db_path)


def run_all_migrations(engine=None):
    """
    Run all database migrations.
    
    Args:
        engine: SQLAlchemy engine. If None, will create one automatically.
    
    Returns:
        True if migrations completed successfully, False otherwise
    """
    if engine is None:
        engine = get_main_engine()
    
    try:
        # Import migration functions from models
        from database.models import (
            _migrate_sync_logs_table,
            _migrate_reviews_table,
            _migrate_tags_tables,
            _migrate_listings_table
        )
        
        database_url = os.getenv("DATABASE_URL")
        
        logger.info("Running database migrations...")
        
        # Run migrations based on database type
        if not database_url:
            # SQLite migrations
            logger.info("Running SQLite migrations...")
            _migrate_sync_logs_table(engine)
            _migrate_reviews_table(engine)
            _migrate_tags_tables(engine)
            _migrate_listings_table(engine)
        else:
            # PostgreSQL migrations
            logger.info("Running PostgreSQL migrations...")
            _migrate_listings_table(engine)
            # Note: Other migrations are SQLite-specific
        
        logger.info("Database migrations completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error running migrations: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    # Allow running migrations as a standalone script
    import sys
    from utils.logging_config import setup_logging
    
    setup_logging()
    
    logger.info("=" * 60)
    logger.info("Running Database Migrations")
    logger.info("=" * 60)
    
    success = run_all_migrations()
    
    if success:
        logger.info("Migrations completed successfully")
        sys.exit(0)
    else:
        logger.error("Migrations failed")
        sys.exit(1)

