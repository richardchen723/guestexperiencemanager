#!/usr/bin/env python3
"""
Cache AI analysis results and track processed reviews/messages.
Supports both SQLite and PostgreSQL.
"""

import json
import os
import logging
from datetime import datetime
from typing import Set, Optional, Dict, List
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, DateTime, text, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import dashboard.config as config

# Configure logging
logger = logging.getLogger(__name__)

Base = declarative_base()


class ProcessedData(Base):
    """Model for tracking processed reviews and messages."""
    __tablename__ = 'processed_data'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, nullable=False, index=True)
    data_type = Column(String, nullable=False, index=True)
    data_id = Column(Integer, nullable=False)
    analyzed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        UniqueConstraint('listing_id', 'data_type', 'data_id', name='uq_processed_data'),
    )


class ListingInsights(Base):
    """Model for storing AI-generated insights per listing."""
    __tablename__ = 'listing_insights'
    
    listing_id = Column(Integer, primary_key=True)
    quality_rating = Column(String)
    issues_json = Column(String)  # JSON string
    action_items_json = Column(String)  # JSON string
    last_updated = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_reviews_analyzed = Column(Integer, default=0)
    total_messages_analyzed = Column(Integer, default=0)


def get_engine(db_path_or_url: str):
    """
    Create SQLAlchemy engine - supports both SQLite and PostgreSQL.
    
    Args:
        db_path_or_url: Database path (for SQLite) or connection URL (for PostgreSQL)
    """
    # Detect PostgreSQL connection string
    if db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://'):
        # Check if we're in a serverless environment (Vercel)
        is_vercel = os.getenv("VERCEL", "0") == "1"
        
        if is_vercel:
            # Serverless: Use NullPool to avoid connection pool exhaustion
            engine = create_engine(
                db_path_or_url,
                echo=False,
                poolclass=NullPool,  # No connection pooling in serverless
                pool_pre_ping=True,
                connect_args={
                    'connect_timeout': 10,
                    'application_name': 'hostaway-ai-cache'
                }
            )
        else:
            # Local/development: Use connection pooling
            engine = create_engine(
                db_path_or_url,
                echo=False,
                pool_pre_ping=True,
                pool_size=2,  # Smaller pool for local development
                max_overflow=5,
                pool_recycle=1800,  # Recycle connections after 30 minutes
                connect_args={
                    'connect_timeout': 10,
                    'application_name': 'hostaway-ai-cache'
                }
            )
        return engine
    
    # SQLite connection (backward compatibility)
    engine = create_engine(
        f'sqlite:///{db_path_or_url}',
        echo=False,
        connect_args={
            'check_same_thread': False,
            'timeout': 30.0
        },
        pool_pre_ping=True
    )
    return engine


def init_cache_db():
    """Initialize the cache database with required tables."""
    db_path_or_url = config.CACHE_DATABASE_PATH
    
    if not db_path_or_url:
        raise ValueError(
            "CACHE_DATABASE_PATH is not set. "
            "In Vercel, set CACHE_DATABASE_URL environment variable. "
            "Locally, it will fall back to SQLite if not set."
        )
    
    # Only create directory for SQLite (file-based)
    if not (db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://')):
        # SQLite: create directory if it doesn't exist
        try:
            db_dir = Path(db_path_or_url).parent
            db_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            # In read-only filesystem (like Vercel), this will fail
            raise ValueError(
                f"Cannot create SQLite database directory: {e}. "
                "In Vercel/production, use PostgreSQL (set CACHE_DATABASE_URL environment variable)."
            ) from e
    
    engine = get_engine(db_path_or_url)
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        import time
        time.sleep(0.5)
        try:
            Base.metadata.create_all(engine)
        except Exception:
            raise e
    return engine


def get_session():
    """Get a database session for cache operations."""
    engine = get_engine(config.CACHE_DATABASE_PATH)
    Session = sessionmaker(bind=engine)
    session = Session()
    return session


def get_processed_reviews(listing_id: int) -> Set[int]:
    """
    Get set of review IDs that have already been analyzed.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Set of review IDs
    """
    init_cache_db()
    session = get_session()
    try:
        results = session.query(ProcessedData.data_id).filter(
            ProcessedData.listing_id == listing_id,
            ProcessedData.data_type == 'review'
        ).all()
        return {row[0] for row in results}
    finally:
        session.close()


def get_processed_messages(listing_id: int) -> Set[int]:
    """
    Get set of message IDs that have already been analyzed.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Set of message IDs
    """
    init_cache_db()
    session = get_session()
    try:
        results = session.query(ProcessedData.data_id).filter(
            ProcessedData.listing_id == listing_id,
            ProcessedData.data_type == 'message'
        ).all()
        return {row[0] for row in results}
    finally:
        session.close()


def mark_reviews_processed(listing_id: int, review_ids: List[int]):
    """
    Mark reviews as processed.
    
    Args:
        listing_id: The listing ID
        review_ids: List of review IDs to mark as processed
    """
    if not review_ids:
        return
    
    init_cache_db()
    session = get_session()
    try:
        for review_id in review_ids:
            # Use PostgreSQL-compatible upsert
            db_path_or_url = config.CACHE_DATABASE_PATH
            is_postgresql = db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://')
            
            if is_postgresql:
                # PostgreSQL: Use ON CONFLICT DO NOTHING
                session.execute(
                    text("""
                        INSERT INTO processed_data (listing_id, data_type, data_id, analyzed_at)
                        VALUES (:listing_id, 'review', :data_id, :analyzed_at)
                        ON CONFLICT (listing_id, data_type, data_id) DO NOTHING
                    """),
                    {
                        'listing_id': listing_id,
                        'data_id': review_id,
                        'analyzed_at': datetime.utcnow()
                    }
                )
            else:
                # SQLite: Use INSERT OR IGNORE
                existing = session.query(ProcessedData).filter(
                    ProcessedData.listing_id == listing_id,
                    ProcessedData.data_type == 'review',
                    ProcessedData.data_id == review_id
                ).first()
                if not existing:
                    processed = ProcessedData(
                        listing_id=listing_id,
                        data_type='review',
                        data_id=review_id,
                        analyzed_at=datetime.utcnow()
                    )
                    session.add(processed)
        
        session.commit()
    finally:
        session.close()


def mark_messages_processed(listing_id: int, message_ids: List[int]):
    """
    Mark messages as processed.
    
    Args:
        listing_id: The listing ID
        message_ids: List of message IDs to mark as processed
    """
    if not message_ids:
        return
    
    init_cache_db()
    session = get_session()
    try:
        for message_id in message_ids:
            # Use PostgreSQL-compatible upsert
            db_path_or_url = config.CACHE_DATABASE_PATH
            is_postgresql = db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://')
            
            if is_postgresql:
                # PostgreSQL: Use ON CONFLICT DO NOTHING
                session.execute(
                    text("""
                        INSERT INTO processed_data (listing_id, data_type, data_id, analyzed_at)
                        VALUES (:listing_id, 'message', :data_id, :analyzed_at)
                        ON CONFLICT (listing_id, data_type, data_id) DO NOTHING
                    """),
                    {
                        'listing_id': listing_id,
                        'data_id': message_id,
                        'analyzed_at': datetime.utcnow()
                    }
                )
            else:
                # SQLite: Use INSERT OR IGNORE
                existing = session.query(ProcessedData).filter(
                    ProcessedData.listing_id == listing_id,
                    ProcessedData.data_type == 'message',
                    ProcessedData.data_id == message_id
                ).first()
                if not existing:
                    processed = ProcessedData(
                        listing_id=listing_id,
                        data_type='message',
                        data_id=message_id,
                        analyzed_at=datetime.utcnow()
                    )
                    session.add(processed)
        
        session.commit()
    finally:
        session.close()


def get_cached_insights(listing_id: int) -> Optional[Dict]:
    """
    Get cached insights for a listing.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Dictionary with insights or None if not cached
    """
    init_cache_db()
    session = get_session()
    try:
        insight = session.query(ListingInsights).filter(
            ListingInsights.listing_id == listing_id
        ).first()
        
        if not insight:
            return None
        
        return {
            'quality_rating': insight.quality_rating,
            'issues': json.loads(insight.issues_json) if insight.issues_json else [],
            'action_items': json.loads(insight.action_items_json) if insight.action_items_json else [],
            'last_updated': insight.last_updated,
            'total_reviews_analyzed': insight.total_reviews_analyzed,
            'total_messages_analyzed': insight.total_messages_analyzed
        }
    finally:
        session.close()


def update_listing_insights(listing_id: int, insights: Dict, review_count: int, message_count: int):
    """
    Update or create insights for a listing.
    
    Args:
        listing_id: The listing ID
        insights: Dictionary with quality_rating, issues, action_items
        review_count: Total number of reviews analyzed
        message_count: Total number of messages analyzed
    """
    init_cache_db()
    session = get_session()
    try:
        # Use PostgreSQL-compatible upsert
        db_path_or_url = config.CACHE_DATABASE_PATH
        is_postgresql = db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://')
        
        if is_postgresql:
            # PostgreSQL: Use ON CONFLICT UPDATE
            session.execute(
                text("""
                    INSERT INTO listing_insights
                    (listing_id, quality_rating, issues_json, action_items_json, 
                     last_updated, total_reviews_analyzed, total_messages_analyzed)
                    VALUES (:listing_id, :quality_rating, :issues_json, :action_items_json, 
                            :last_updated, :review_count, :message_count)
                    ON CONFLICT (listing_id) DO UPDATE SET
                        quality_rating = EXCLUDED.quality_rating,
                        issues_json = EXCLUDED.issues_json,
                        action_items_json = EXCLUDED.action_items_json,
                        last_updated = EXCLUDED.last_updated,
                        total_reviews_analyzed = EXCLUDED.total_reviews_analyzed,
                        total_messages_analyzed = EXCLUDED.total_messages_analyzed
                """),
                {
                    'listing_id': listing_id,
                    'quality_rating': insights.get('quality_rating'),
                    'issues_json': json.dumps(insights.get('issues', [])),
                    'action_items_json': json.dumps(insights.get('action_items', [])),
                    'last_updated': datetime.utcnow(),
                    'review_count': review_count,
                    'message_count': message_count
                }
            )
        else:
            # SQLite: Use INSERT OR REPLACE
            insight = ListingInsights(
                listing_id=listing_id,
                quality_rating=insights.get('quality_rating'),
                issues_json=json.dumps(insights.get('issues', [])),
                action_items_json=json.dumps(insights.get('action_items', [])),
                last_updated=datetime.utcnow(),
                total_reviews_analyzed=review_count,
                total_messages_analyzed=message_count
            )
            session.merge(insight)  # merge handles both insert and update
        
        session.commit()
    finally:
        session.close()


def clear_listing_cache(listing_id: int):
    """
    Clear all cached data for a listing (for force refresh).
    
    Args:
        listing_id: The listing ID
    """
    init_cache_db()
    session = get_session()
    try:
        session.query(ProcessedData).filter(
            ProcessedData.listing_id == listing_id
        ).delete()
        session.query(ListingInsights).filter(
            ListingInsights.listing_id == listing_id
        ).delete()
        session.commit()
    finally:
        session.close()
