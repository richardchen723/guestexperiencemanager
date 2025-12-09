#!/usr/bin/env python3
"""
Migration script to add internal_name column to listings table.
This script is idempotent - safe to run multiple times.
"""

import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import get_session, init_models, Listing
from database.schema import get_database_path
from sqlalchemy import text
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_internal_name_column():
    """Add internal_name column to listings table if it doesn't exist."""
    
    # Initialize models
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        # Check if using PostgreSQL
        database_url = os.getenv("DATABASE_URL")
        
        if database_url:
            # PostgreSQL
            logger.info("Detected PostgreSQL database")
            
            # Check if column already exists
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'listings' 
                AND column_name = 'internal_name'
            """)
            result = session.execute(check_query).fetchone()
            
            if result:
                logger.info("Column 'internal_name' already exists in listings table")
                return
            
            # Add column
            logger.info("Adding internal_name column to listings table...")
            alter_query = text("ALTER TABLE listings ADD COLUMN internal_name VARCHAR")
            session.execute(alter_query)
            session.commit()
            logger.info("✓ Successfully added internal_name column")
            
        else:
            # SQLite
            logger.info("Detected SQLite database")
            
            # Check if column already exists by trying to query it
            try:
                session.query(Listing.internal_name).limit(1).all()
                logger.info("Column 'internal_name' already exists in listings table")
                return
            except Exception:
                # Column doesn't exist, add it
                pass
            
            # Add column
            logger.info("Adding internal_name column to listings table...")
            alter_query = text("ALTER TABLE listings ADD COLUMN internal_name TEXT")
            session.execute(alter_query)
            session.commit()
            logger.info("✓ Successfully added internal_name column")
            
    except Exception as e:
        logger.error(f"Error adding internal_name column: {e}")
        session.rollback()
        raise
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 50)
    print("Adding internal_name column to listings table")
    print("=" * 50)
    add_internal_name_column()
    print("=" * 50)
    print("Migration completed!")
    print("=" * 50)

