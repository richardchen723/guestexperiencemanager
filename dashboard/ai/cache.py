#!/usr/bin/env python3
"""
Cache AI analysis results and track processed reviews/messages.
Supports both PostgreSQL and SQLite.
"""

import json
import os
from datetime import datetime
from typing import Set, Optional, Dict, List
from pathlib import Path
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import sqlalchemy

import dashboard.config as config

Base = declarative_base()


class ProcessedData(Base):
    """Track individual processed items."""
    __tablename__ = 'processed_data'
    __table_args__ = (
        {'schema': 'cache'} if os.getenv("DATABASE_URL") else {},
    )
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, nullable=False, index=True)
    data_type = Column(String, nullable=False, index=True)
    data_id = Column(Integer, nullable=False)
    analyzed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ListingInsights(Base):
    """Store overall insights per listing."""
    __tablename__ = 'listing_insights'
    __table_args__ = (
        {'schema': 'cache'} if os.getenv("DATABASE_URL") else {},
    )
    
    listing_id = Column(Integer, primary_key=True)
    quality_rating = Column(String)
    issues_json = Column(Text)
    action_items_json = Column(Text)
    last_updated = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_reviews_analyzed = Column(Integer, default=0)
    total_messages_analyzed = Column(Integer, default=0)


# Engine cache to prevent connection leaks
_engine_cache = {}
_sessionmaker_cache = {}

def get_engine():
    """
    Create or retrieve cached SQLAlchemy engine for cache database.
    Uses singleton pattern to prevent connection leaks.
    """
    import os
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is required. "
            "PostgreSQL is required for this application. "
            "Example: postgresql://user@localhost:5432/hostaway_dev"
        )
    
    # PostgreSQL connection - use 'cache' schema
    if '?' in database_url:
        database_url_with_schema = database_url + "&options=-csearch_path%3Dcache,public"
    else:
        database_url_with_schema = database_url + "?options=-csearch_path%3Dcache,public"
    
    # Use modified database_url as cache key
    cache_key = database_url_with_schema
    
    # Return cached engine if it exists
    if cache_key in _engine_cache:
        return _engine_cache[cache_key]
    
    # Create new engine with optimized pool settings
    engine = create_engine(
        database_url_with_schema,
        echo=False,
        pool_size=3,           # Reduced from 5 to prevent exhaustion
        max_overflow=1,        # Reduced from 2 to limit total connections
        pool_timeout=30,
        pool_pre_ping=True,
        pool_recycle=3600,     # Recycle connections after 1 hour
        pool_reset_on_return='commit',  # Reset connection state on return
        connect_args={
            "connect_timeout": 15,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
            "application_name": "hostaway_cache"  # Set application name for monitoring
        }
    )
    
    # Cache the engine
    _engine_cache[cache_key] = engine
    return engine


def _get_session():
    """
    Get a database session using cached engine and sessionmaker.
    This prevents connection leaks by reusing the same sessionmaker.
    """
    engine = get_engine()
    
    # Cache sessionmaker per engine to avoid recreating
    if engine not in _sessionmaker_cache:
        _sessionmaker_cache[engine] = sessionmaker(bind=engine)
    
    Session = _sessionmaker_cache[engine]
    return Session()


def init_cache_db():
    """Initialize the cache database with required tables."""
    import os
    
    engine = get_engine()
    database_url = os.getenv("DATABASE_URL")
    
    # For PostgreSQL, create schema if it doesn't exist
    if database_url:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS cache"))
    
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        if not database_url:
            # SQLite: retry after short wait
            import time
            time.sleep(0.5)
            try:
                Base.metadata.create_all(engine)
            except Exception:
                raise e
        else:
            raise e


def get_processed_reviews(listing_id: int) -> Set[int]:
    """
    Get set of review IDs that have already been analyzed.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Set of review IDs
    """
    init_cache_db()
    session = _get_session()
    
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
    session = _get_session()
    
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
    session = _get_session()
    
    try:
        for review_id in review_ids:
            # Use merge to handle duplicates (PostgreSQL) or insert (SQLite)
            processed = ProcessedData(
                listing_id=listing_id,
                data_type='review',
                data_id=review_id,
                analyzed_at=datetime.utcnow()
            )
            session.merge(processed)  # Works for both PostgreSQL and SQLite
        
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
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
    session = _get_session()
    
    try:
        for message_id in message_ids:
            processed = ProcessedData(
                listing_id=listing_id,
                data_type='message',
                data_id=message_id,
                analyzed_at=datetime.utcnow()
            )
            session.merge(processed)
        
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
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
    session = _get_session()
    
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
            'last_updated': insight.last_updated.isoformat() if insight.last_updated else None,
            'total_reviews_analyzed': insight.total_reviews_analyzed,
            'total_messages_analyzed': insight.total_messages_analyzed
        }
    except Exception as e:
        # Gracefully return None on error to prevent breaking the listings page
        return None
    finally:
        session.close()


def get_cached_insights_batch(listing_ids: List[int]) -> Dict[int, Dict]:
    """
    Batch load cached insights for multiple listings in a single query.
    This fixes the N+1 query problem when loading listings.
    
    Args:
        listing_ids: List of listing IDs
        
    Returns:
        Dictionary mapping listing_id to insights dict
    """
    if not listing_ids:
        return {}
    
    init_cache_db()
    session = _get_session()
    
    try:
        insights = session.query(ListingInsights).filter(
            ListingInsights.listing_id.in_(listing_ids)
        ).all()
        
        result = {}
        for insight in insights:
            result[insight.listing_id] = {
                'quality_rating': insight.quality_rating,
                'issues': json.loads(insight.issues_json) if insight.issues_json else [],
                'action_items': json.loads(insight.action_items_json) if insight.action_items_json else [],
                'last_updated': insight.last_updated.isoformat() if insight.last_updated else None,
                'total_reviews_analyzed': insight.total_reviews_analyzed,
                'total_messages_analyzed': insight.total_messages_analyzed
            }
        
        return result
    except Exception as e:
        # Gracefully return empty dict on error to prevent breaking the listings page
        return {}
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
    session = _get_session()
    
    try:
        insight = ListingInsights(
            listing_id=listing_id,
            quality_rating=insights.get('quality_rating'),
            issues_json=json.dumps(insights.get('issues', [])),
            action_items_json=json.dumps(insights.get('action_items', [])),
            last_updated=datetime.utcnow(),
            total_reviews_analyzed=review_count,
            total_messages_analyzed=message_count
        )
        session.merge(insight)
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def clear_listing_cache(listing_id: int):
    """
    Clear all cached data for a listing (for force refresh).
    
    Args:
        listing_id: The listing ID
    """
    init_cache_db()
    session = _get_session()
    
    try:
        session.query(ProcessedData).filter(
            ProcessedData.listing_id == listing_id
        ).delete()
        session.query(ListingInsights).filter(
            ListingInsights.listing_id == listing_id
        ).delete()
        session.commit()
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()
