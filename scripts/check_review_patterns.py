#!/usr/bin/env python3
"""
Check for patterns that might explain why listing 146889 has 650 reviews.
"""

import sys
import os
from collections import defaultdict
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Review, get_session, init_models
from database.schema import get_database_path

def check_patterns():
    """Check various patterns that might explain the high review count."""
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        # Focus on listing 146889
        listing_146889 = session.query(Review).filter(Review.listing_id == 146889).all()
        print(f"Listing 146889: {len(listing_146889)} total reviews")
        print("=" * 80)
        
        # Check distribution by review_date
        print("\n1. Reviews by date:")
        date_counts = defaultdict(int)
        no_date = 0
        for review in listing_146889:
            if review.review_date:
                date_counts[review.review_date] += 1
            else:
                no_date += 1
        
        print(f"   Reviews without date: {no_date}")
        print(f"   Reviews with date: {len(date_counts)} unique dates")
        
        # Show dates with most reviews
        sorted_dates = sorted(date_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        print(f"   Top dates with multiple reviews:")
        for date, count in sorted_dates:
            if count > 1:
                print(f"     {date}: {count} reviews")
        
        # Check for reviews with same date but different reviewer names
        print("\n2. Same date, different reviewers:")
        date_reviewer_groups = defaultdict(list)
        for review in listing_146889:
            if review.review_date:
                name = (review.reviewer_name or 'Unknown').lower().strip()
                date_reviewer_groups[review.review_date].append((name, review.review_id, review.review_text[:50] if review.review_text else ''))
        
        same_date_multiple = {date: reviewers for date, reviewers in date_reviewer_groups.items() if len(reviewers) > 1}
        print(f"   Dates with multiple reviews: {len(same_date_multiple)}")
        
        # Check for reviews with very similar text (first 100 chars)
        print("\n3. Reviews with similar text (first 100 chars):")
        text_prefix_groups = defaultdict(list)
        for review in listing_146889:
            if review.review_text:
                prefix = review.review_text.lower().strip()[:100]
                if len(prefix) > 30:
                    text_prefix_groups[prefix].append(review)
        
        similar_texts = {prefix: reviews for prefix, reviews in text_prefix_groups.items() if len(reviews) > 1}
        print(f"   Found {len(similar_texts)} text prefixes with multiple reviews")
        if similar_texts:
            total_similar = sum(len(reviews) - 1 for reviews in similar_texts.values())
            print(f"   Potential near-duplicates: {total_similar}")
            
            # Show examples
            for i, (prefix, reviews) in enumerate(list(similar_texts.items())[:5]):
                print(f"\n   Example {i+1}: {len(reviews)} reviews with similar start")
                print(f"   Text start: {prefix[:80]}...")
                for review in reviews[:3]:
                    print(f"     - ID: {review.review_id}, Reviewer: {review.reviewer_name or 'Unknown'}, Date: {review.review_date}")
        
        # Check for reviews with same reviewer_name and similar dates
        print("\n4. Same reviewer, different dates (within 30 days):")
        reviewer_groups = defaultdict(list)
        for review in listing_146889:
            name = (review.reviewer_name or 'Unknown').lower().strip()
            if name != 'unknown' and review.review_date:
                reviewer_groups[name].append((review.review_date, review.review_id))
        
        # Find reviewers with multiple reviews
        multi_reviewers = {name: dates for name, dates in reviewer_groups.items() if len(dates) > 1}
        print(f"   Reviewers with multiple reviews: {len(multi_reviewers)}")
        
        # Check for reviews that might be the same but with different dates
        potential_dups = 0
        for name, dates in list(multi_reviewers.items())[:10]:
            if len(dates) > 1:
                # Sort by date
                sorted_dates = sorted(dates, key=lambda x: x[0])
                # Check if dates are close together
                for i in range(len(sorted_dates) - 1):
                    date1, id1 = sorted_dates[i]
                    date2, id2 = sorted_dates[i + 1]
                    if (date2 - date1).days <= 30:
                        potential_dups += 1
                        print(f"     {name}: {date1} and {date2} (within 30 days)")
        
        # Check review_id patterns
        print("\n5. Review ID analysis:")
        review_ids = sorted([r.review_id for r in listing_146889])
        print(f"   Review ID range: {review_ids[0]} to {review_ids[-1]}")
        print(f"   Total unique review_ids: {len(set(review_ids))}")
        
        # Check if there are gaps or clusters in review_ids
        consecutive_groups = []
        current_group = [review_ids[0]]
        for i in range(1, len(review_ids)):
            if review_ids[i] - review_ids[i-1] <= 10:  # IDs within 10 of each other
                current_group.append(review_ids[i])
            else:
                if len(current_group) > 1:
                    consecutive_groups.append(current_group)
                current_group = [review_ids[i]]
        if len(current_group) > 1:
            consecutive_groups.append(current_group)
        
        print(f"   Groups of consecutive review_ids: {len(consecutive_groups)}")
        if consecutive_groups:
            print(f"   Largest group: {max(len(g) for g in consecutive_groups)} review_ids")
        
        print("\n" + "=" * 80)
        
        return {
            'total': len(listing_146889),
            'no_date': no_date,
            'unique_dates': len(date_counts),
            'similar_texts': len(similar_texts),
            'similar_total': sum(len(reviews) - 1 for reviews in similar_texts.values()) if similar_texts else 0,
            'multi_reviewers': len(multi_reviewers)
        }
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 80)
    print("REVIEW PATTERN ANALYSIS FOR LISTING 146889")
    print("=" * 80)
    print()
    
    try:
        results = check_patterns()
        print(f"\nSummary:")
        print(f"  Total reviews: {results['total']}")
        print(f"  Reviews without date: {results['no_date']}")
        print(f"  Unique dates: {results['unique_dates']}")
        print(f"  Similar texts: {results['similar_texts']}")
        print(f"  Potential near-duplicates: {results['similar_total']}")
        print(f"  Reviewers with multiple reviews: {results['multi_reviewers']}")
    except Exception as e:
        print(f"\n\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


