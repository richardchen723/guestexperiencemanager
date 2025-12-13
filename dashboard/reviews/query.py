#!/usr/bin/env python3
"""
Query functions for reviews with tag joins and filtering.
"""

import logging
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Optional
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import joinedload

from database.models import Review, Listing, ListingTag, Tag, ReviewFilter, Reservation, get_session
from sqlalchemy import or_
from database.schema import get_database_path

logger = logging.getLogger(__name__)


def get_unresponded_reviews(tag_ids: Optional[List[int]] = None) -> List[Dict]:
    """
    Query reviews where status='Submitted' AND origin='Guest'.
    Join with listings and listing_tags to include tag information.
    
    Args:
        tag_ids: Optional list of tag IDs to filter by. If provided, only reviews
                 for listings with at least one of these tags will be returned.
    
    Returns:
        List of review dictionaries with listing and tag information.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        # Query reviews with status='submitted' (lowercase) and origin='Guest'
        # Note: status is stored in lowercase in database
        query = session.query(Review).join(Listing).filter(
            and_(
                func.lower(Review.status) == 'submitted',
                Review.origin == 'Guest'
            )
        )
        
        # Filter by tag_ids if provided
        if tag_ids:
            # Join with listing_tags and tags to filter by tag_ids
            query = query.join(ListingTag).join(Tag).filter(
                Tag.tag_id.in_(tag_ids)
            )
        
        reviews = query.options(
            joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
            joinedload(Review.reservation)
        ).distinct().all()
        
        # Convert to dictionaries with tag information
        result = []
        for review in reviews:
            # Use internal_listing_name with fallback to name
            listing_name = None
            if review.listing:
                listing_name = review.listing.internal_listing_name or review.listing.name
            
            # Get departure date from reservation
            departure_date = None
            if review.reservation and review.reservation.departure_date:
                departure_date = review.reservation.departure_date.isoformat()
            
            review_dict = {
                'review_id': review.review_id,
                'listing_id': review.listing_id,
                'listing_name': listing_name,
                'overall_rating': review.overall_rating,
                'review_text': review.review_text,
                'review_date': review.review_date.isoformat() if review.review_date else None,
                'reviewer_name': review.reviewer_name,
                'departure_date': departure_date,
                'status': review.status,
                'origin': review.origin,
                'channel_name': review.channel_name,
                'tags': []
            }
            
            # Get tags from listing
            if review.listing and review.listing.tags:
                for listing_tag in review.listing.tags:
                    if listing_tag.tag:
                        review_dict['tags'].append({
                            'tag_id': listing_tag.tag.tag_id,
                            'name': listing_tag.tag.name,
                            'color': listing_tag.tag.color
                        })
            
            result.append(review_dict)
        
        return result
        
    except Exception as e:
        logger.error(f"Error querying unresponded reviews: {e}", exc_info=True)
        raise
    finally:
        session.close()


def get_reviews_by_filter(filter_obj: ReviewFilter, sort_by: str = 'review_date', sort_order: str = 'desc') -> List[Dict]:
    """
    Query reviews matching filter criteria.
    
    Args:
        filter_obj: ReviewFilter object with criteria.
        sort_by: Field to sort by ('review_date' or 'overall_rating'). Default: 'review_date'.
        sort_order: Sort order ('asc' or 'desc'). Default: 'desc'.
        
    Returns:
        List of review dictionaries with listing and tag information.
    """
    db_path = get_database_path()
    session = get_session(db_path)
    
    try:
        # Start with base query
        query = session.query(Review).join(Listing).filter(
            Review.status == 'published'
        )
        
        # Filter by tag_ids if provided
        if filter_obj.tag_ids:
            # Parse tag_ids (could be JSON string or list)
            tag_ids = filter_obj.tag_ids
            if isinstance(tag_ids, str):
                import json
                try:
                    tag_ids = json.loads(tag_ids)
                except:
                    tag_ids = []
            
            if tag_ids:
                # Join with listing_tags and tags to filter by tag_ids
                query = query.join(ListingTag).join(Tag).filter(
                    Tag.tag_id.in_(tag_ids)
                )
        
        # Filter by max_rating if provided
        # Note: max_rating is in 5-star scale (0-5), but overall_rating is in 10-point scale (0-10)
        # Convert 5-star rating to 10-point scale by multiplying by 2
        if filter_obj.max_rating is not None:
            max_rating_10_point = filter_obj.max_rating * 2.0
            query = query.filter(Review.overall_rating <= max_rating_10_point)
        
        # Filter by months_back if provided
        if filter_obj.months_back is not None:
            cutoff_date = datetime.utcnow() - relativedelta(months=filter_obj.months_back)
            # Use departure_date from reservation (when guest stayed) for date filtering
            # This makes more sense for "bad reviews in last X months" - we want reviews for stays in that period
            # Fall back to review_date if no reservation is linked
            query = query.outerjoin(Reservation, Review.reservation_id == Reservation.reservation_id).filter(
                or_(
                    and_(
                        Reservation.departure_date.isnot(None),
                        Reservation.departure_date >= cutoff_date.date()
                    ),
                    and_(
                        Reservation.departure_date.is_(None),
                        Review.review_date.isnot(None),
                        Review.review_date >= cutoff_date.date()
                    )
                    )
                )
        
        # Apply sorting
        # Use nullslast() if available (SQLAlchemy 1.1+), otherwise handle nulls in Python
        try:
            if sort_by == 'review_date':
                if sort_order == 'desc':
                    query = query.order_by(Review.review_date.desc().nullslast())
                else:
                    query = query.order_by(Review.review_date.asc().nullslast())
            elif sort_by == 'overall_rating':
                if sort_order == 'desc':
                    query = query.order_by(Review.overall_rating.desc().nullslast())
                else:
                    query = query.order_by(Review.overall_rating.asc().nullslast())
        except AttributeError:
            # Fallback for older SQLAlchemy versions - sort in Python
            if sort_by == 'review_date':
                if sort_order == 'desc':
                    query = query.order_by(Review.review_date.desc())
                else:
                    query = query.order_by(Review.review_date.asc())
            elif sort_by == 'overall_rating':
                if sort_order == 'desc':
                    query = query.order_by(Review.overall_rating.desc())
                else:
                    query = query.order_by(Review.overall_rating.asc())
        
        # Execute query with tag loading
        reviews = query.options(
            joinedload(Review.listing).joinedload(Listing.tags).joinedload(ListingTag.tag),
            joinedload(Review.reservation)
        ).distinct().all()
        
        # Convert to dictionaries with tag information
        result = []
        for review in reviews:
            # Use internal_listing_name with fallback to name
            listing_name = None
            if review.listing:
                listing_name = review.listing.internal_listing_name or review.listing.name
            
            # Get departure date from reservation
            departure_date = None
            if review.reservation and review.reservation.departure_date:
                departure_date = review.reservation.departure_date.isoformat()
            
            review_dict = {
                'review_id': review.review_id,
                'listing_id': review.listing_id,
                'listing_name': listing_name,
                'overall_rating': review.overall_rating,
                'review_text': review.review_text,
                'review_date': review.review_date.isoformat() if review.review_date else None,
                'reviewer_name': review.reviewer_name,
                'departure_date': departure_date,
                'status': review.status,
                'origin': review.origin,
                'channel_name': review.channel_name,
                'tags': []
            }
            
            # Get tags from listing
            if review.listing and review.listing.tags:
                for listing_tag in review.listing.tags:
                    if listing_tag.tag:
                        review_dict['tags'].append({
                            'tag_id': listing_tag.tag.tag_id,
                            'name': listing_tag.tag.name,
                            'color': listing_tag.tag.color
                        })
            
            result.append(review_dict)
        
        return result
        
    except Exception as e:
        logger.error(f"Error querying reviews by filter: {e}", exc_info=True)
        raise
    finally:
        session.close()

