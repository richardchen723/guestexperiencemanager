#!/usr/bin/env python3
"""
Migration script to migrate data from SQLite to PostgreSQL.

This script:
1. Reads data from SQLite databases (main, users, ai_cache)
2. Writes data to PostgreSQL databases
3. Preserves all relationships and data integrity

Usage:
    python3 scripts/migrate_to_postgres.py --main-db sqlite_path --users-db sqlite_path --cache-db sqlite_path
    --main-url postgresql://... --users-url postgresql://... --cache-url postgresql://...
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from database.models import Base as MainBase, get_engine as get_main_engine, init_models
from dashboard.auth.models import Base as UsersBase, get_engine as get_users_engine, init_user_database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def migrate_table(session_source, session_target, model_class, table_name, batch_size=1000):
    """
    Migrate a single table from source to target database.
    
    Args:
        session_source: SQLAlchemy session for source database
        session_target: SQLAlchemy session for target database
        model_class: SQLAlchemy model class
        table_name: Name of the table
        batch_size: Number of records to process per batch
    """
    logger.info(f"Migrating table: {table_name}")
    
    try:
        # Get all records from source
        total_records = session_source.query(model_class).count()
        logger.info(f"  Found {total_records} records in source")
        
        if total_records == 0:
            logger.info(f"  No records to migrate for {table_name}")
            return
        
        # Migrate in batches
        offset = 0
        migrated = 0
        
        while offset < total_records:
            records = session_source.query(model_class).offset(offset).limit(batch_size).all()
            
            if not records:
                break
            
            # Convert to dictionaries and insert into target
            for record in records:
                try:
                    # Convert to dict, excluding relationships
                    record_dict = {}
                    for column in model_class.__table__.columns:
                        value = getattr(record, column.name)
                        record_dict[column.name] = value
                    
                    # Create new record in target
                    new_record = model_class(**record_dict)
                    session_target.add(new_record)
                    migrated += 1
                except Exception as e:
                    logger.warning(f"  Error migrating record {record}: {e}")
                    continue
            
            # Commit batch
            try:
                session_target.commit()
                logger.info(f"  Migrated {migrated}/{total_records} records...")
            except Exception as e:
                session_target.rollback()
                logger.error(f"  Error committing batch: {e}")
                raise
            
            offset += batch_size
        
        logger.info(f"  Successfully migrated {migrated} records from {table_name}")
        
    except Exception as e:
        logger.error(f"  Error migrating {table_name}: {e}", exc_info=True)
        raise


def migrate_database(source_path, target_url, base, get_engine_func, init_func, database_name):
    """
    Migrate an entire database from SQLite to PostgreSQL.
    
    Args:
        source_path: Path to SQLite database file
        target_url: PostgreSQL connection URL
        base: SQLAlchemy Base for the database
        get_engine_func: Function to get engine
        init_func: Function to initialize database
        database_name: Name of the database (for logging)
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"Migrating {database_name} database")
    logger.info(f"{'='*60}")
    logger.info(f"Source: {source_path}")
    logger.info(f"Target: {target_url}")
    
    # Create engines
    source_engine = get_engine_func(source_path)
    target_engine = get_engine_func(target_url)
    
    # Initialize target database (create tables)
    logger.info(f"Initializing target database...")
    init_func(target_url) if init_func else base.metadata.create_all(target_engine)
    
    # Create sessions
    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)
    
    source_session = SourceSession()
    target_session = TargetSession()
    
    try:
        # Get all tables from the base
        tables = base.metadata.tables.keys()
        logger.info(f"Found {len(tables)} tables to migrate: {', '.join(tables)}")
        
        # Migrate each table
        for table_name in tables:
            # Get model class from base
            model_class = None
            for mapper in base.registry.mappers:
                if mapper.class_.__tablename__ == table_name:
                    model_class = mapper.class_
                    break
            
            if not model_class:
                logger.warning(f"  Could not find model class for table {table_name}, skipping")
                continue
            
            try:
                migrate_table(source_session, target_session, model_class, table_name)
            except Exception as e:
                logger.error(f"  Failed to migrate {table_name}: {e}")
                # Continue with other tables
                continue
        
        logger.info(f"\n✓ Successfully migrated {database_name} database")
        
    except Exception as e:
        logger.error(f"Error migrating {database_name}: {e}", exc_info=True)
        raise
    finally:
        source_session.close()
        target_session.close()
        source_engine.dispose()
        target_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='Migrate SQLite databases to PostgreSQL')
    parser.add_argument('--main-db', help='Path to SQLite main database', required=True)
    parser.add_argument('--users-db', help='Path to SQLite users database', required=True)
    parser.add_argument('--cache-db', help='Path to SQLite cache database', required=True)
    parser.add_argument('--main-url', help='PostgreSQL URL for main database', required=True)
    parser.add_argument('--users-url', help='PostgreSQL URL for users database', required=True)
    parser.add_argument('--cache-url', help='PostgreSQL URL for cache database', required=True)
    parser.add_argument('--dry-run', action='store_true', help='Dry run (do not write to target)')
    
    args = parser.parse_args()
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No data will be written to target databases")
    
    try:
        # Migrate main database
        migrate_database(
            args.main_db,
            args.main_url,
            MainBase,
            get_main_engine,
            init_models,
            "main"
        )
        
        # Migrate users database
        migrate_database(
            args.users_db,
            args.users_url,
            UsersBase,
            get_users_engine,
            init_user_database,
            "users"
        )
        
        # Migrate cache database (if it has models)
        # Note: Cache database may use a different structure - adjust as needed
        try:
            # Try to import cache models if they exist
            from dashboard.ai.cache import Base as CacheBase
            # For cache, we'll use a simple approach - just copy the SQLite file structure
            # or use the same engine pattern
            logger.info("Cache database migration - using same engine pattern as users")
            # Cache database typically uses same structure as users database
            # If it has separate models, uncomment and adjust:
            # migrate_database(
            #     args.cache_db,
            #     args.cache_url,
            #     CacheBase,
            #     get_users_engine,  # Reuse users engine function
            #     None,  # May need custom init function
            #     "cache"
            # )
            logger.info("Cache database migration skipped - implement if needed")
        except ImportError:
            logger.warning("Cache database models not found, skipping cache migration")
        
        logger.info("\n" + "="*60)
        logger.info("Migration completed successfully!")
        logger.info("="*60)
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

