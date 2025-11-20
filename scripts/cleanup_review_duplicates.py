#!/usr/bin/env python3
"""
Cleanup script to remove duplicate reviews from the database.
Uses multiple methods to identify duplicates:
1. Same (reviewer_name, review_date, listing_id) - most reliable
2. Same review_text content - catches duplicates with missing dates/names
"""

import sys
import os
from collections import defaultdict
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, ReviewSubRating, get_session, init_models
from database.schema import get_database_path

def cleanup_duplicates(dry_run: bool = True, method: str = 'combo'):
    """
    Remove duplicate reviews from the database.
    
    Args:
        dry_run: If True, only report what would be deleted without actually deleting.
        method: Method to identify duplicates:
            - 'combo': Same (reviewer_name, review_date, listing_id) - RECOMMENDED
            - 'text': Same review_text content
            - 'both': Use both methods (most aggressive)
    
    Returns:
        Dictionary with cleanup statistics.
    """
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        reviews_to_delete = []
        total_to_delete = 0
        
        if method in ['combo', 'both']:
            # Method 1: Find duplicates by (reviewer_name, review_date, listing_id)
            print("Finding duplicate reviews by (reviewer_name, review_date, listing_id)...")
            
            reviewer_date_listing = defaultdict(list)
            for review in session.query(Review).all():
                if review.review_date and review.listing_id:
                    # Use reviewer_name or 'Unknown'
                    name = (review.reviewer_name or 'Unknown').lower().strip()
                    key = (name, review.review_date, review.listing_id)
                    reviewer_date_listing[key].append(review)
            
            duplicates_combo = {key: reviews for key, reviews in reviewer_date_listing.items() if len(reviews) > 1}
            
            if duplicates_combo:
                print(f"Found {len(duplicates_combo)} (reviewer, date, listing) combinations with duplicates")
                
                for (name, date, listing_id), reviews in duplicates_combo.items():
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
                    
                    print(f"  ({name}, {date}, listing {listing_id}): {len(reviews)} copies")
                    print(f"    Keeping: review_id={keep_review.review_id}, last_synced_at={keep_review.last_synced_at}")
                    
                    for delete_review in delete_reviews:
                        print(f"    Will delete: review_id={delete_review.review_id}, last_synced_at={delete_review.last_synced_at}")
                        reviews_to_delete.append(delete_review)
                        total_to_delete += 1
        
        if method in ['text', 'both']:
            # Method 2: Find duplicates by review_text content
            print("\nFinding duplicate reviews by review_text content...")
            
            reviews_with_text = session.query(Review).filter(
                Review.review_text.isnot(None),
                Review.review_text != ''
            ).all()
            
            # Group by normalized review text
            text_groups = defaultdict(list)
            for review in reviews_with_text:
                normalized_text = review.review_text.lower().strip()
                if len(normalized_text) > 20:  # Only check substantial reviews
                    text_groups[normalized_text].append(review)
            
            duplicates_text = {text: reviews for text, reviews in text_groups.items() if len(reviews) > 1}
            
            if duplicates_text:
                print(f"Found {len(duplicates_text)} unique review texts that appear multiple times")
                
                for text, reviews in duplicates_text.items():
                    if len(reviews) <= 1:
                        continue
                    
                    # Skip if already marked for deletion by combo method
                    reviews_to_check = [r for r in reviews if r not in reviews_to_delete]
                    if len(reviews_to_check) <= 1:
                        continue
                    
                    # Sort by last_synced_at (most recent first), then by review_id
                    reviews_sorted = sorted(
                        reviews_to_check,
                        key=lambda r: (
                            r.last_synced_at if r.last_synced_at else datetime.min,
                            r.review_id
                        ),
                        reverse=True
                    )
                    
                    # Keep the first one, mark others for deletion
                    keep_review = reviews_sorted[0]
                    delete_reviews = reviews_sorted[1:]
                    
                    print(f"  Text: {text[:80]}... ({len(reviews_to_check)} copies)")
                    print(f"    Keeping: review_id={keep_review.review_id}, listing={keep_review.listing_id}")
                    
                    for delete_review in delete_reviews:
                        print(f"    Will delete: review_id={delete_review.review_id}, listing={delete_review.listing_id}")
                        reviews_to_delete.append(delete_review)
                        total_to_delete += 1
        
        if not reviews_to_delete:
            print("\nNo duplicate reviews found to delete!")
            return {
                'duplicates_found': 0,
                'reviews_to_delete': 0,
                'reviews_deleted': 0
            }
        
        # Remove duplicates from reviews_to_delete list (same review might be found by both methods)
        unique_reviews_to_delete = list({r.review_id: r for r in reviews_to_delete}.values())
        total_to_delete = len(unique_reviews_to_delete)
        
        print(f"\n{'DRY RUN - ' if dry_run else ''}Summary:")
        print(f"  Total duplicate reviews to delete: {total_to_delete}")
        
        if not dry_run and unique_reviews_to_delete:
            print("\nDeleting duplicate reviews...")
            
            # Delete sub-ratings first (foreign key constraint)
            for review in unique_reviews_to_delete:
                try:
                    # Delete sub-ratings for this review
                    session.query(ReviewSubRating).filter(
                        ReviewSubRating.review_id == review.review_id
                    ).delete()
                except Exception as e:
                    print(f"  Warning: Error deleting sub-ratings for review {review.review_id}: {e}")
            
            # Delete the duplicate reviews
            total_deleted = 0
            for review in unique_reviews_to_delete:
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
            
            return {
                'duplicates_found': len(duplicates_combo) if method in ['combo', 'both'] else (len(duplicates_text) if method == 'text' else 0),
                'reviews_to_delete': total_to_delete,
                'reviews_deleted': total_deleted
            }
        else:
            return {
                'duplicates_found': len(duplicates_combo) if method in ['combo', 'both'] else (len(duplicates_text) if method == 'text' else 0),
                'reviews_to_delete': total_to_delete,
                'reviews_deleted': 0
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
  python3 scripts/cleanup_review_duplicates.py --dry-run --method combo    # Preview combo method
  python3 scripts/cleanup_review_duplicates.py --dry-run --method text     # Preview text method
  python3 scripts/cleanup_review_duplicates.py --dry-run --method both      # Preview both methods
  python3 scripts/cleanup_review_duplicates.py --execute --method both     # Actually delete duplicates
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
        choices=['combo', 'text', 'both'],
        default='both',
        help='Method to identify duplicates: combo=(reviewer,date,listing), text=same text, both=use both (default: both)'
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
        results = cleanup_duplicates(dry_run=dry_run, method=args.method)
        
        print("\n" + "=" * 80)
        print("CLEANUP COMPLETE")
        print("=" * 80)
        print(f"Duplicate groups found: {results['duplicates_found']}")
        print(f"Reviews {'to delete' if dry_run else 'deleted'}: {results['reviews_to_delete'] if dry_run else results['reviews_deleted']}")
        
        if dry_run:
            print("\nTo actually delete these duplicates, run:")
            print(f"  python3 scripts/cleanup_review_duplicates.py --execute --method {args.method}")
        
    except KeyboardInterrupt:
        print("\n\nCleanup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


