#!/usr/bin/env python3
"""
Script to delete all reviews from the database.
This will also delete all associated review_sub_ratings due to CASCADE.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, ReviewSubRating, get_session, init_models
from database.schema import get_database_path

def delete_all_reviews(dry_run: bool = True):
    """
    Delete all reviews from the database.
    
    Args:
        dry_run: If True, only report what would be deleted without actually deleting.
    
    Returns:
        Dictionary with deletion statistics.
    """
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        # Count reviews before deletion
        total_reviews = session.query(Review).count()
        total_sub_ratings = session.query(ReviewSubRating).count()
        
        print(f"Total reviews in database: {total_reviews}")
        print(f"Total review sub-ratings in database: {total_sub_ratings}")
        
        if total_reviews == 0:
            print("\nNo reviews to delete!")
            return {
                'reviews_deleted': 0,
                'sub_ratings_deleted': 0
            }
        
        if dry_run:
            print(f"\nDRY RUN: Would delete {total_reviews} reviews and {total_sub_ratings} sub-ratings")
            print("To actually delete, run with --execute flag")
            return {
                'reviews_deleted': 0,
                'sub_ratings_deleted': 0
            }
        
        # Actually delete
        print(f"\nDeleting all reviews...")
        
        # Delete sub-ratings first (though CASCADE should handle this)
        deleted_sub_ratings = session.query(ReviewSubRating).delete()
        print(f"Deleted {deleted_sub_ratings} review sub-ratings")
        
        # Delete all reviews
        deleted_reviews = session.query(Review).delete()
        print(f"Deleted {deleted_reviews} reviews")
        
        # Commit the deletions
        try:
            session.commit()
            print(f"\nSuccessfully deleted all reviews!")
            return {
                'reviews_deleted': deleted_reviews,
                'sub_ratings_deleted': deleted_sub_ratings
            }
        except Exception as e:
            session.rollback()
            print(f"\nError committing deletions: {e}")
            raise
        
    except Exception as e:
        session.rollback()
        print(f"Error during deletion: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Delete all reviews from the database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WARNING: This will permanently delete ALL reviews and their sub-ratings!

Examples:
  python3 scripts/delete_all_reviews.py --dry-run    # Preview what would be deleted
  python3 scripts/delete_all_reviews.py --execute    # Actually delete all reviews
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
    
    args = parser.parse_args()
    
    # If --execute is provided, set dry_run to False
    dry_run = not args.execute
    
    print("=" * 80)
    print("DELETE ALL REVIEWS")
    print("=" * 80)
    print(f"Mode: {'DRY RUN (preview only)' if dry_run else 'EXECUTE (will delete ALL reviews)'}")
    if not dry_run:
        print("WARNING: This will permanently delete ALL reviews and sub-ratings!")
    print("=" * 80)
    print()
    
    try:
        results = delete_all_reviews(dry_run=dry_run)
        
        print("\n" + "=" * 80)
        print("DELETION COMPLETE")
        print("=" * 80)
        print(f"Reviews {'to delete' if dry_run else 'deleted'}: {results['reviews_deleted']}")
        print(f"Sub-ratings {'to delete' if dry_run else 'deleted'}: {results['sub_ratings_deleted']}")
        
        if dry_run:
            print("\nTo actually delete all reviews, run:")
            print("  python3 scripts/delete_all_reviews.py --execute")
        
    except KeyboardInterrupt:
        print("\n\nDeletion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
