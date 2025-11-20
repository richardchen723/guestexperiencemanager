#!/usr/bin/env python3
"""
Cache AI analysis results and track processed reviews/messages.
"""

import sqlite3
import json
from datetime import datetime
from typing import Set, Optional, Dict, List
from pathlib import Path
import os

import dashboard.config as config


def init_cache_db():
    """Initialize the cache database with required tables."""
    db_dir = Path(config.CACHE_DATABASE_PATH).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    # Table to track individual processed items
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            data_type TEXT NOT NULL,
            data_id INTEGER NOT NULL,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(listing_id, data_type, data_id)
        )
    """)
    
    # Table to store overall insights per listing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listing_insights (
            listing_id INTEGER PRIMARY KEY,
            quality_rating TEXT,
            issues_json TEXT,
            action_items_json TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_reviews_analyzed INTEGER DEFAULT 0,
            total_messages_analyzed INTEGER DEFAULT 0
        )
    """)
    
    # Create indexes
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_listing_type 
        ON processed_data(listing_id, data_type)
    """)
    
    conn.commit()
    conn.close()


def get_processed_reviews(listing_id: int) -> Set[int]:
    """
    Get set of review IDs that have already been analyzed.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Set of review IDs
    """
    init_cache_db()
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT data_id FROM processed_data
        WHERE listing_id = ? AND data_type = 'review'
    """, (listing_id,))
    
    result = {row[0] for row in cursor.fetchall()}
    conn.close()
    return result


def get_processed_messages(listing_id: int) -> Set[int]:
    """
    Get set of message IDs that have already been analyzed.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Set of message IDs
    """
    init_cache_db()
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT data_id FROM processed_data
        WHERE listing_id = ? AND data_type = 'message'
    """, (listing_id,))
    
    result = {row[0] for row in cursor.fetchall()}
    conn.close()
    return result


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
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    for review_id in review_ids:
        cursor.execute("""
            INSERT OR IGNORE INTO processed_data (listing_id, data_type, data_id, analyzed_at)
            VALUES (?, 'review', ?, ?)
        """, (listing_id, review_id, datetime.utcnow()))
    
    conn.commit()
    conn.close()


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
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    for message_id in message_ids:
        cursor.execute("""
            INSERT OR IGNORE INTO processed_data (listing_id, data_type, data_id, analyzed_at)
            VALUES (?, 'message', ?, ?)
        """, (listing_id, message_id, datetime.utcnow()))
    
    conn.commit()
    conn.close()


def get_cached_insights(listing_id: int) -> Optional[Dict]:
    """
    Get cached insights for a listing.
    
    Args:
        listing_id: The listing ID
        
    Returns:
        Dictionary with insights or None if not cached
    """
    init_cache_db()
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT quality_rating, issues_json, action_items_json, last_updated,
               total_reviews_analyzed, total_messages_analyzed
        FROM listing_insights
        WHERE listing_id = ?
    """, (listing_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return {
        'quality_rating': row[0],
        'issues': json.loads(row[1]) if row[1] else [],
        'action_items': json.loads(row[2]) if row[2] else [],
        'last_updated': row[3],
        'total_reviews_analyzed': row[4],
        'total_messages_analyzed': row[5]
    }


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
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR REPLACE INTO listing_insights
        (listing_id, quality_rating, issues_json, action_items_json, 
         last_updated, total_reviews_analyzed, total_messages_analyzed)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        listing_id,
        insights.get('quality_rating'),
        json.dumps(insights.get('issues', [])),
        json.dumps(insights.get('action_items', [])),
        datetime.utcnow(),
        review_count,
        message_count
    ))
    
    conn.commit()
    conn.close()


def clear_listing_cache(listing_id: int):
    """
    Clear all cached data for a listing (for force refresh).
    
    Args:
        listing_id: The listing ID
    """
    init_cache_db()
    conn = sqlite3.connect(config.CACHE_DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM processed_data WHERE listing_id = ?", (listing_id,))
    cursor.execute("DELETE FROM listing_insights WHERE listing_id = ?", (listing_id,))
    
    conn.commit()
    conn.close()

