#!/usr/bin/env python3
"""
Migrate data from SQLite to PostgreSQL.
Supports dry-run mode for testing.
"""

import sys
import os
import argparse
from datetime import datetime
from typing import Dict, List, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from database.models import Base, get_engine as get_sqlite_engine, get_session as get_sqlite_session
from database.models import (
    Listing, Reservation, Guest, Conversation, MessageMetadata, 
    ListingPhoto, Review, ReviewSubRating, SyncLog
)
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_postgres_engine(database_url: str):
    """Create PostgreSQL engine."""
    return create_engine(
        database_url,
        echo=False,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_pre_ping=True
    )


def migrate_table_data(
    sqlite_session,
    postgres_session,
    model_class,
    table_name: str,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Migrate data from SQLite to PostgreSQL for a given model.
    
    Returns:
        Dictionary with 'total', 'created', 'updated', 'errors' counts
    """
    logger.info(f"Migrating {table_name}...")
    
    results = {
        'total': 0,
        'created': 0,
        'updated': 0,
        'errors': 0
    }
    
    try:
        # Get all records from SQLite
        sqlite_records = sqlite_session.query(model_class).all()
        results['total'] = len(sqlite_records)
        
        if results['total'] == 0:
            logger.info(f"  No records to migrate for {table_name}")
            return results
        
        logger.info(f"  Found {results['total']} records to migrate")
        
        if dry_run:
            logger.info(f"  [DRY RUN] Would migrate {results['total']} records")
            return results
        
        # Migrate in batches
        batch_size = 100
        for i in range(0, len(sqlite_records), batch_size):
            batch = sqlite_records[i:i + batch_size]
            
            for record in batch:
                try:
                    # Convert SQLAlchemy object to dict
                    record_dict = {}
                    for column in model_class.__table__.columns:
                        value = getattr(record, column.name)
                        # Handle datetime objects
                        if hasattr(value, 'isoformat'):
                            value = value
                        record_dict[column.name] = value
                    
                    # Check if record exists in PostgreSQL
                    primary_key_cols = list(model_class.__table__.primary_key.columns)
                    primary_key = primary_key_cols[0].name if primary_key_cols else None
                    if not primary_key:
                        logger.warning(f"  No primary key found for {table_name}, skipping")
                        continue
                    existing = postgres_session.query(model_class).filter(
                        getattr(model_class, primary_key) == record_dict[primary_key]
                    ).first()
                    
                    if existing:
                        # Update existing record
                        for key, value in record_dict.items():
                            setattr(existing, key, value)
                        results['updated'] += 1
                    else:
                        # Create new record
                        new_record = model_class(**record_dict)
                        postgres_session.add(new_record)
                        results['created'] += 1
                    
                except Exception as e:
                    logger.error(f"  Error migrating {table_name} record {getattr(record, primary_key, 'unknown')}: {e}")
                    results['errors'] += 1
                    continue
            
            # Commit batch
            try:
                postgres_session.commit()
                logger.info(f"  Migrated batch {i//batch_size + 1} ({min(i+batch_size, len(sqlite_records))}/{len(sqlite_records)})")
            except Exception as e:
                postgres_session.rollback()
                logger.error(f"  Error committing batch: {e}")
                results['errors'] += len(batch)
        
        logger.info(f"  Completed {table_name}: {results['created']} created, {results['updated']} updated, {results['errors']} errors")
        
    except Exception as e:
        logger.error(f"  Fatal error migrating {table_name}: {e}", exc_info=True)
        results['errors'] = results['total']
    
    return results


def migrate_database(sqlite_db_path: str, postgres_url: str, dry_run: bool = False):
    """
    Migrate all data from SQLite to PostgreSQL.
    
    Args:
        sqlite_db_path: Path to SQLite database
        postgres_url: PostgreSQL connection URL
        dry_run: If True, only show what would be migrated
    """
    logger.info("=" * 80)
    logger.info("DATABASE MIGRATION: SQLite to PostgreSQL")
    logger.info("=" * 80)
    logger.info(f"SQLite DB: {sqlite_db_path}")
    logger.info(f"PostgreSQL: {postgres_url.split('@')[-1] if '@' in postgres_url else 'hidden'}")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    logger.info("=" * 80)
    
    # Create engines
    sqlite_engine = get_sqlite_engine(sqlite_db_path)
    postgres_engine = get_postgres_engine(postgres_url)
    
    # Create sessions
    SQLiteSession = sessionmaker(bind=sqlite_engine)
    PostgresSession = sessionmaker(bind=postgres_engine)
    
    sqlite_session = SQLiteSession()
    postgres_session = PostgresSession()
    
    try:
        # Create tables in PostgreSQL if they don't exist
        logger.info("Creating tables in PostgreSQL...")
        if not dry_run:
            Base.metadata.create_all(postgres_engine)
            logger.info("  Tables created/verified")
        else:
            logger.info("  [DRY RUN] Would create tables")
        
        # Define migration order (respecting foreign key constraints)
        migration_order = [
            (Listing, 'listings'),
            (Guest, 'guests'),
            (Reservation, 'reservations'),
            (Conversation, 'conversations'),
            (MessageMetadata, 'message_metadata'),
            (ListingPhoto, 'listing_photos'),
            (Review, 'reviews'),
            (ReviewSubRating, 'review_sub_ratings'),
            (SyncLog, 'sync_logs'),
        ]
        
        # Migrate each table
        all_results = {}
        for model_class, table_name in migration_order:
            results = migrate_table_data(
                sqlite_session,
                postgres_session,
                model_class,
                table_name,
                dry_run
            )
            all_results[table_name] = results
        
        # Summary
        logger.info("=" * 80)
        logger.info("MIGRATION SUMMARY")
        logger.info("=" * 80)
        
        total_created = sum(r['created'] for r in all_results.values())
        total_updated = sum(r['updated'] for r in all_results.values())
        total_errors = sum(r['errors'] for r in all_results.values())
        total_records = sum(r['total'] for r in all_results.values())
        
        for table_name, results in all_results.items():
            logger.info(f"{table_name:30} | Total: {results['total']:6} | "
                       f"Created: {results['created']:6} | Updated: {results['updated']:6} | "
                       f"Errors: {results['errors']:6}")
        
        logger.info("-" * 80)
        logger.info(f"{'TOTAL':30} | Total: {total_records:6} | "
                   f"Created: {total_created:6} | Updated: {total_updated:6} | "
                   f"Errors: {total_errors:6}")
        logger.info("=" * 80)
        
        if dry_run:
            logger.info("\nThis was a DRY RUN. No data was actually migrated.")
            logger.info("Run without --dry-run to execute the migration.")
        else:
            logger.info("\nMigration completed successfully!")
        
    except Exception as e:
        logger.error(f"Fatal error during migration: {e}", exc_info=True)
        if not dry_run:
            postgres_session.rollback()
        raise
    finally:
        sqlite_session.close()
        postgres_session.close()


def main():
    parser = argparse.ArgumentParser(description='Migrate SQLite database to PostgreSQL')
    parser.add_argument('--sqlite-db', required=True, help='Path to SQLite database file')
    parser.add_argument('--postgres-url', required=True, help='PostgreSQL connection URL (postgresql://user:password@host:port/database)')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no actual migration)')
    
    args = parser.parse_args()
    
    # Validate SQLite database exists
    if not os.path.exists(args.sqlite_db):
        logger.error(f"SQLite database not found: {args.sqlite_db}")
        sys.exit(1)
    
    # Validate PostgreSQL URL format
    if not args.postgres_url.startswith('postgresql://'):
        logger.error("PostgreSQL URL must start with 'postgresql://'")
        sys.exit(1)
    
    try:
        migrate_database(args.sqlite_db, args.postgres_url, dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("\nMigration interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\nMigration failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()

