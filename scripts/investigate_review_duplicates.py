#!/usr/bin/env python3
"""
Investigate review duplicates in the database.
Checks for various types of duplicates:
1. Same review_id (primary key duplicates - shouldn't happen)
2. Same review content but different IDs
3. Same review appearing for multiple listings
"""

import sys
import os
from collections import defaultdict
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, get_session, init_models
from database.schema import get_database_path
from sqlalchemy import func, and_

def investigate_duplicates():
    """Investigate different types of duplicates in reviews."""
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        # Get total count
        total_reviews = session.query(Review).count()
        print(f"Total reviews in database: {total_reviews}")
        print("=" * 80)
        
        # 1. Check for duplicate review_ids (shouldn't happen with primary key)
        print("\n1. Checking for duplicate review_ids (primary key)...")
        duplicate_ids = (
            session.query(Review.review_id, func.count(Review.review_id).label('count'))
            .group_by(Review.review_id)
            .having(func.count(Review.review_id) > 1)
            .all()
        )
        print(f"   Found {len(duplicate_ids)} review_ids with duplicates")
        if duplicate_ids:
            for review_id, count in duplicate_ids[:10]:
                print(f"   Review ID {review_id}: {count} copies")
        
        # 2. Check for reviews with same content but different IDs
        print("\n2. Checking for reviews with same text content...")
        reviews_with_text = session.query(Review).filter(
            Review.review_text.isnot(None),
            Review.review_text != ''
        ).all()
        
        # Group by review text (normalized)
        text_groups = defaultdict(list)
        for review in reviews_with_text:
            # Normalize text: lowercase, strip whitespace
            normalized_text = review.review_text.lower().strip() if review.review_text else ""
            if len(normalized_text) > 20:  # Only check substantial reviews
                text_groups[normalized_text].append(review)
        
        duplicate_texts = {text: reviews for text, reviews in text_groups.items() if len(reviews) > 1}
        print(f"   Found {len(duplicate_texts)} unique review texts that appear multiple times")
        
        if duplicate_texts:
            total_duplicates_by_text = sum(len(reviews) - 1 for reviews in duplicate_texts.values())
            print(f"   Total duplicate reviews by text content: {total_duplicates_by_text}")
            # Show first few examples
            for i, (text, reviews) in enumerate(list(duplicate_texts.items())[:5]):
                print(f"\n   Example {i+1}: {len(reviews)} reviews with same text")
                print(f"   Text preview: {text[:100]}...")
                for review in reviews[:3]:
                    print(f"     - Review ID: {review.review_id}, Listing: {review.listing_id}, "
                          f"Reviewer: {review.reviewer_name}, Date: {review.review_date}")
        
        # 3. Check for same reviewer_name + review_date + listing combinations
        print("\n3. Checking for duplicate (reviewer_name, review_date, listing_id) combinations...")
        reviewer_date_listing = defaultdict(list)
        for review in session.query(Review).all():
            if review.reviewer_name and review.review_date and review.listing_id:
                key = (review.reviewer_name.lower(), review.review_date, review.listing_id)
                reviewer_date_listing[key].append(review)
        
        duplicate_combos = {key: reviews for key, reviews in reviewer_date_listing.items() if len(reviews) > 1}
        print(f"   Found {len(duplicate_combos)} (reviewer, date, listing) combinations with duplicates")
        
        if duplicate_combos:
            total_duplicates_by_combo = sum(len(reviews) - 1 for reviews in duplicate_combos.values())
            print(f"   Total duplicate reviews by (reviewer, date, listing): {total_duplicates_by_combo}")
            # Show first few examples
            for i, ((name, date, listing_id), reviews) in enumerate(list(duplicate_combos.items())[:5]):
                print(f"\n   Example {i+1}: {len(reviews)} reviews for {name} on {date} for listing {listing_id}")
                for review in reviews[:3]:
                    print(f"     - Review ID: {review.review_id}, Text: {review.review_text[:50] if review.review_text else 'N/A'}...")
        
        # 4. Check specific listing mentioned by user
        print("\n4. Checking listing_id 146889...")
        listing_146889_reviews = session.query(Review).filter(Review.listing_id == 146889).all()
        print(f"   Total reviews for listing 146889: {len(listing_146889_reviews)}")
        
        # Group by review_id for this listing
        review_ids_for_listing = defaultdict(list)
        for review in listing_146889_reviews:
            review_ids_for_listing[review.review_id].append(review)
        
        duplicates_in_listing = {rid: reviews for rid, reviews in review_ids_for_listing.items() if len(reviews) > 1}
        print(f"   Duplicate review_ids for this listing: {len(duplicates_in_listing)}")
        
        # Group by text for this listing
        text_groups_listing = defaultdict(list)
        for review in listing_146889_reviews:
            if review.review_text:
                normalized = review.review_text.lower().strip()
                if len(normalized) > 20:
                    text_groups_listing[normalized].append(review)
        
        duplicate_texts_listing = {text: reviews for text, reviews in text_groups_listing.items() if len(reviews) > 1}
        print(f"   Duplicate review texts for this listing: {len(duplicate_texts_listing)}")
        if duplicate_texts_listing:
            total_dup = sum(len(reviews) - 1 for reviews in duplicate_texts_listing.values())
            print(f"   Total duplicate reviews by text for listing 146889: {total_dup}")
        
        # 5. Check for reviews with same review_id but different listing_ids (cross-listing duplicates)
        print("\n5. Checking for reviews with same review_id but different listing_ids...")
        review_id_to_listings = defaultdict(set)
        for review in session.query(Review).all():
            review_id_to_listings[review.review_id].add(review.listing_id)
        
        cross_listing_duplicates = {rid: listings for rid, listings in review_id_to_listings.items() if len(listings) > 1}
        print(f"   Found {len(cross_listing_duplicates)} review_ids associated with multiple listings")
        if cross_listing_duplicates:
            print(f"   Total cross-listing duplicates: {sum(len(listings) - 1 for listings in cross_listing_duplicates.values())}")
            # Show examples
            for i, (review_id, listings) in enumerate(list(cross_listing_duplicates.items())[:5]):
                print(f"   Review ID {review_id} appears in listings: {sorted(listings)}")
        
        print("\n" + "=" * 80)
        print("INVESTIGATION COMPLETE")
        print("=" * 80)
        
        return {
            'total_reviews': total_reviews,
            'duplicate_ids': len(duplicate_ids),
            'duplicate_texts': len(duplicate_texts),
            'duplicate_combos': len(duplicate_combos),
            'cross_listing_duplicates': len(cross_listing_duplicates)
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
    print("REVIEW DUPLICATE INVESTIGATION")
    print("=" * 80)
    print()
    
    try:
        results = investigate_duplicates()
        print(f"\nSummary:")
        print(f"  Total reviews: {results['total_reviews']}")
        print(f"  Duplicate review_ids: {results['duplicate_ids']}")
        print(f"  Duplicate texts: {results['duplicate_texts']}")
        print(f"  Duplicate (reviewer, date, listing) combos: {results['duplicate_combos']}")
        print(f"  Cross-listing duplicates: {results['cross_listing_duplicates']}")
    except KeyboardInterrupt:
        print("\n\nInvestigation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


