#!/usr/bin/env python3
"""
Deep investigation of review duplicates.
Checks for various patterns and missing data that might hide duplicates.
"""

import sys
import os
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, get_session, init_models
from database.schema import get_database_path

def deep_investigate():
    """Deep investigation of duplicates with various patterns."""
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        total_reviews = session.query(Review).count()
        print(f"Total reviews in database: {total_reviews}")
        print("=" * 80)
        
        # Check how many reviews are missing key fields
        print("\n1. Missing data analysis:")
        missing_name = session.query(Review).filter(
            Review.reviewer_name.in_([None, '', 'Unknown'])
        ).count()
        missing_date = session.query(Review).filter(Review.review_date.is_(None)).count()
        
        print(f"   Reviews with missing/empty reviewer_name: {missing_name} ({missing_name*100/total_reviews:.1f}%)")
        print(f"   Reviews with missing review_date: {missing_date} ({missing_date*100/total_reviews:.1f}%)")
        
        # Check for duplicates by text only (most aggressive)
        print("\n2. Duplicates by review_text only (same text = duplicate):")
        reviews_with_text = session.query(Review).filter(
            Review.review_text.isnot(None),
            Review.review_text != ''
        ).all()
        
        text_groups = defaultdict(list)
        for review in reviews_with_text:
            normalized_text = review.review_text.lower().strip()
            if len(normalized_text) > 20:  # Substantial reviews only
                text_groups[normalized_text].append(review)
        
        text_duplicates = {text: reviews for text, reviews in text_groups.items() if len(reviews) > 1}
        print(f"   Found {len(text_duplicates)} unique texts that appear multiple times")
        if text_duplicates:
            total_dup = sum(len(reviews) - 1 for reviews in text_duplicates.values())
            print(f"   Total duplicates by text: {total_dup}")
            
            # Group by listing to see impact
            listing_dup_counts = defaultdict(int)
            for text, reviews in text_duplicates.items():
                for review in reviews:
                    listing_dup_counts[review.listing_id] += 1
            
            print(f"   Affected listings: {len(listing_dup_counts)}")
            # Show top affected listings
            top_listings = sorted(listing_dup_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            print(f"   Top affected listings:")
            for listing_id, count in top_listings:
                print(f"     Listing {listing_id}: {count} duplicate reviews")
        
        # Check listing 146889 specifically
        print("\n3. Detailed analysis of listing 146889:")
        listing_146889 = session.query(Review).filter(Review.listing_id == 146889).all()
        print(f"   Total reviews: {len(listing_146889)}")
        
        # Group by text
        text_groups_146889 = defaultdict(list)
        for review in listing_146889:
            if review.review_text:
                normalized = review.review_text.lower().strip()
                if len(normalized) > 20:
                    text_groups_146889[normalized].append(review)
        
        text_dup_146889 = {text: reviews for text, reviews in text_groups_146889.items() if len(reviews) > 1}
        print(f"   Duplicate texts: {len(text_dup_146889)}")
        if text_dup_146889:
            total_dup_146889 = sum(len(reviews) - 1 for reviews in text_dup_146889.values())
            print(f"   Total duplicates by text: {total_dup_146889}")
            print(f"   Expected unique reviews: {len(listing_146889) - total_dup_146889}")
        
        print("\n" + "=" * 80)
        print("DEEP INVESTIGATION COMPLETE")
        print("=" * 80)
        
        return {
            'total_reviews': total_reviews,
            'text_duplicates': len(text_duplicates),
            'text_dup_total': sum(len(reviews) - 1 for reviews in text_duplicates.values()) if text_duplicates else 0,
            'listing_146889_total': len(listing_146889),
            'listing_146889_text_dup': sum(len(reviews) - 1 for reviews in text_dup_146889.values()) if text_dup_146889 else 0,
        }
        
    except Exception as e:
        print(f"Error during investigation: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 80)
    print("DEEP REVIEW DUPLICATE INVESTIGATION")
    print("=" * 80)
    print()
    
    try:
        results = deep_investigate()
        print(f"\nSummary:")
        print(f"  Total reviews: {results['total_reviews']}")
        print(f"  Text duplicates (all listings): {results['text_duplicates']}")
        print(f"  Total duplicates by text: {results['text_dup_total']}")
        print(f"  Listing 146889 total: {results['listing_146889_total']}")
        print(f"  Listing 146889 text duplicates: {results['listing_146889_text_dup']}")
    except KeyboardInterrupt:
        print("\n\nInvestigation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


