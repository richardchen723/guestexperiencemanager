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


def get_engine():
    """Create SQLAlchemy engine for cache database."""
    import os
    
    # Check for PostgreSQL connection string
    database_url = os.getenv("DATABASE_URL")
    
    if database_url:
        # PostgreSQL connection - use 'cache' schema
        if '?' in database_url:
            database_url += "&options=-csearch_path%3Dcache,public"
        else:
            database_url += "?options=-csearch_path%3Dcache,public"
        
        engine = create_engine(
            database_url,
            echo=False,
            pool_size=5,
            max_overflow=2,
            pool_timeout=30,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 15,  # Increased from 10 to handle slower connections
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5
            }
        )
        return engine
    else:
        # SQLite connection (fallback)
        db_path = config.CACHE_DATABASE_PATH
        db_dir = Path(db_path).parent
        # Skip directory creation on Vercel (read-only filesystem)
        if not os.getenv("VERCEL"):
            db_dir.mkdir(parents=True, exist_ok=True)
        
        engine = create_engine(
            f'sqlite:///{db_path}',
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    session = Session()
    
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
