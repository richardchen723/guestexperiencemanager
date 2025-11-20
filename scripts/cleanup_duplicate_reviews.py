#!/usr/bin/env python3
"""
Cleanup script to remove duplicate reviews from the database.

This script identifies and removes duplicate reviews based on review_id.
For duplicates, it keeps the most recently synced review and deletes the others.
"""

import sys
import os
from collections import defaultdict
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, ReviewSubRating, get_session, init_models
from database.schema import get_database_path
from sqlalchemy import func

def cleanup_duplicate_reviews(dry_run: bool = True, method: str = 'combo'):
    """
    Remove duplicate reviews from the database.
    
    Args:
        dry_run: If True, only report what would be deleted without actually deleting.
        method: Method to identify duplicates:
            - 'combo': Same (reviewer_name, review_date, listing_id) - RECOMMENDED
            - 'text': Same review_text content
            - 'id': Same review_id (shouldn't happen with primary key)
    
    Returns:
        Dictionary with cleanup statistics.
    """
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        reviews_to_delete = []
        total_to_delete = 0
        
        if method == 'combo':
            # Method 1: Find duplicates by (reviewer_name, review_date, listing_id)
            print("Finding duplicate reviews by (reviewer_name, review_date, listing_id)...")
            
            # Group reviews by (reviewer_name, review_date, listing_id)
            reviewer_date_listing = defaultdict(list)
            for review in session.query(Review).all():
                if review.reviewer_name and review.review_date and review.listing_id:
                    key = (review.reviewer_name.lower().strip(), review.review_date, review.listing_id)
                    reviewer_date_listing[key].append(review)
            
            duplicates = {key: reviews for key, reviews in reviewer_date_listing.items() if len(reviews) > 1}
            
            if not duplicates:
                print("No duplicate reviews found by (reviewer_name, review_date, listing_id)!")
            else:
                print(f"Found {len(duplicates)} (reviewer, date, listing) combinations with duplicates")
                
                for (name, date, listing_id), reviews in duplicates.items():
                    if len(reviews) <= 1:
                        continue
                    
                    # Sort by last_synced_at (most recent first), then by review_id
                    reviews_sorted = sorted(
                        reviews,
                        key=lambda r: (
                            r.last_synced_at if r.last_synced_at else datetime.min,
                            r.review_id
                        ),
                        reverse=True
                    )
                    
                    # Keep the first one (most recently synced), mark others for deletion
                    keep_review = reviews_sorted[0]
                    delete_reviews = reviews_sorted[1:]
                    
                    print(f"\n  ({name}, {date}, listing {listing_id}): {len(reviews)} copies")
                    print(f"    Keeping: review_id={keep_review.review_id}, last_synced_at={keep_review.last_synced_at}")
                    
                    for delete_review in delete_reviews:
                        print(f"    Will delete: review_id={delete_review.review_id}, last_synced_at={delete_review.last_synced_at}")
                        reviews_to_delete.append(delete_review)
                        total_to_delete += 1
        
        elif method == 'text':
            # Method 2: Find duplicates by review_text content
            print("Finding duplicate reviews by review_text content...")
            
            reviews_with_text = session.query(Review).filter(
                Review.review_text.isnot(None),
                Review.review_text != ''
            ).all()
            
            # Group by normalized review text
            text_groups = defaultdict(list)
            for review in reviews_with_text:
                normalized_text = review.review_text.lower().strip() if review.review_text else ""
                if len(normalized_text) > 20:  # Only check substantial reviews
                    text_groups[normalized_text].append(review)
            
            duplicates = {text: reviews for text, reviews in text_groups.items() if len(reviews) > 1}
            
            if not duplicates:
                print("No duplicate reviews found by text content!")
            else:
                print(f"Found {len(duplicates)} unique review texts that appear multiple times")
                
                for text, reviews in duplicates.items():
                    if len(reviews) <= 1:
                        continue
                    
                    # Sort by last_synced_at (most recent first), then by review_id
                    reviews_sorted = sorted(
                        reviews,
                        key=lambda r: (
                            r.last_synced_at if r.last_synced_at else datetime.min,
                            r.review_id
                        ),
                        reverse=True
                    )
                    
                    # Keep the first one, mark others for deletion
                    keep_review = reviews_sorted[0]
                    delete_reviews = reviews_sorted[1:]
                    
                    print(f"\n  Text: {text[:80]}... ({len(reviews)} copies)")
                    print(f"    Keeping: review_id={keep_review.review_id}, listing={keep_review.listing_id}")
                    
                    for delete_review in delete_reviews:
                        print(f"    Will delete: review_id={delete_review.review_id}, listing={delete_review.listing_id}")
                        reviews_to_delete.append(delete_review)
                        total_to_delete += 1
        
        else:  # method == 'id'
            # Method 3: Find duplicate review_ids (shouldn't happen with primary key)
            print("Finding duplicate reviews by review_id...")
            duplicate_query = (
                session.query(Review.review_id, func.count(Review.review_id).label('count'))
                .group_by(Review.review_id)
                .having(func.count(Review.review_id) > 1)
            )
            duplicates = duplicate_query.all()
            
            if not duplicates:
                print("No duplicate review_ids found!")
            else:
                print(f"Found {len(duplicates)} review_ids with duplicates")
                
                for review_id, count in duplicates:
                    reviews = session.query(Review).filter(Review.review_id == review_id).all()
                    
                    if len(reviews) <= 1:
                        continue
                    
                    # Sort by last_synced_at (most recent first)
                    reviews_sorted = sorted(
                        reviews,
                        key=lambda r: (
                            r.last_synced_at if r.last_synced_at else datetime.min,
                            r.review_id
                        ),
                        reverse=True
                    )
                    
                    keep_review = reviews_sorted[0]
                    delete_reviews = reviews_sorted[1:]
                    
                    print(f"\n  Review ID {review_id}: {len(reviews)} copies")
                    print(f"    Keeping: review_id={keep_review.review_id}, listing={keep_review.listing_id}")
                    
                    for delete_review in delete_reviews:
                        print(f"    Will delete: review_id={delete_review.review_id}, listing={delete_review.listing_id}")
                        reviews_to_delete.append(delete_review)
                        total_to_delete += 1
        
        total_deleted = 0
        
        if not reviews_to_delete:
            print("\nNo duplicate reviews found to delete!")
            return {
                'duplicates_found': 0,
                'reviews_to_delete': 0,
                'reviews_deleted': 0
            }
        
        duplicates_count = len(duplicates) if 'duplicates' in locals() and duplicates else 0
        
        print(f"\n{'DRY RUN - ' if dry_run else ''}Summary:")
        print(f"  Duplicate groups found: {duplicates_count}")
        print(f"  Total duplicate reviews to delete: {total_to_delete}")
        
        if not dry_run and reviews_to_delete:
            print("\nDeleting duplicate reviews...")
            
            # Delete sub-ratings first (foreign key constraint)
            for review in reviews_to_delete:
                try:
                    # Delete sub-ratings for this review
                    session.query(ReviewSubRating).filter(
                        ReviewSubRating.review_id == review.review_id
                    ).delete()
                except Exception as e:
                    print(f"  Warning: Error deleting sub-ratings for review {review.review_id}: {e}")
            
            # Delete the duplicate reviews
            for review in reviews_to_delete:
                try:
                    session.delete(review)
                    total_deleted += 1
                except Exception as e:
                    print(f"  Error deleting review {review.review_id}: {e}")
            
            # Commit the deletions
            try:
                session.commit()
                print(f"\nSuccessfully deleted {total_deleted} duplicate reviews")
            except Exception as e:
                session.rollback()
                print(f"\nError committing deletions: {e}")
                raise
        
        duplicates_count = len(duplicates) if 'duplicates' in locals() and duplicates else 0
        
        return {
            'duplicates_found': duplicates_count,
            'reviews_to_delete': total_to_delete,
            'reviews_deleted': total_deleted if not dry_run else 0
        }
        
    except Exception as e:
        session.rollback()
        print(f"Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Clean up duplicate reviews from the database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/cleanup_duplicate_reviews.py --dry-run    # Preview what would be deleted
  python3 scripts/cleanup_duplicate_reviews.py              # Actually delete duplicates
        """
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Preview what would be deleted without actually deleting (default: True)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Actually perform the deletion (overrides --dry-run)'
    )
    parser.add_argument(
        '--method',
        choices=['combo', 'text', 'id'],
        default='combo',
        help='Method to identify duplicates: combo=(reviewer,date,listing), text=same text, id=same review_id (default: combo)'
    )
    
    args = parser.parse_args()
    
    # If --execute is provided, set dry_run to False
    dry_run = not args.execute
    
    print("=" * 80)
    print("DUPLICATE REVIEW CLEANUP")
    print("=" * 80)
    print(f"Mode: {'DRY RUN (preview only)' if dry_run else 'EXECUTE (will delete duplicates)'}")
    print(f"Method: {args.method}")
    print("=" * 80)
    print()
    
    try:
        results = cleanup_duplicate_reviews(dry_run=dry_run, method=args.method)
        
        print("\n" + "=" * 80)
        print("CLEANUP COMPLETE")
        print("=" * 80)
        print(f"Duplicate review_ids found: {results['duplicates_found']}")
        print(f"Reviews {'to delete' if dry_run else 'deleted'}: {results['reviews_to_delete'] if dry_run else results['reviews_deleted']}")
        
        if dry_run:
            print("\nTo actually delete these duplicates, run:")
            print("  python3 scripts/cleanup_duplicate_reviews.py --execute")
        
    except KeyboardInterrupt:
        print("\n\nCleanup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

