#!/usr/bin/env python3
"""
Check database for consistency and duplicate data.
Verifies idempotency of sync processes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.schema import get_database_path
from database.models import get_session, init_models
from database.models import Listing, Reservation, Guest, Review, Conversation, MessageMetadata, ListingPhoto, ReviewSubRating
import sqlite3

def check_duplicates(session):
    """Check for duplicate records in key tables"""
    print("=" * 80)
    print("CHECKING FOR DUPLICATES")
    print("=" * 80)
    
    issues = []
    
    # Check listings - should be unique by listing_id (primary key)
    listings = session.query(Listing).all()
    listing_ids = [l.listing_id for l in listings]
    if len(listing_ids) != len(set(listing_ids)):
        issues.append(f"Listings: Found duplicate listing_ids")
        duplicates = [x for x in listing_ids if listing_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate listing_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Listings: {len(listings)} unique records")
    
    # Check reservations - should be unique by reservation_id (primary key)
    reservations = session.query(Reservation).all()
    reservation_ids = [r.reservation_id for r in reservations]
    if len(reservation_ids) != len(set(reservation_ids)):
        issues.append(f"Reservations: Found duplicate reservation_ids")
        duplicates = [x for x in reservation_ids if reservation_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate reservation_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Reservations: {len(reservations)} unique records")
    
    # Check guests - should be unique by guest_id (primary key)
    guests = session.query(Guest).all()
    guest_ids = [g.guest_id for g in guests]
    if len(guest_ids) != len(set(guest_ids)):
        issues.append(f"Guests: Found duplicate guest_ids")
        duplicates = [x for x in guest_ids if guest_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate guest_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Guests: {len(guests)} unique records")
    
    # Check for duplicate guest_external_account_id (should be UNIQUE)
    conn = sqlite3.connect(get_database_path())
    cursor = conn.cursor()
    cursor.execute("""
        SELECT guest_external_account_id, COUNT(*) as cnt
        FROM guests
        WHERE guest_external_account_id IS NOT NULL
        GROUP BY guest_external_account_id
        HAVING cnt > 1
    """)
    duplicate_external_ids = cursor.fetchall()
    if duplicate_external_ids:
        issues.append(f"Guests: Found duplicate guest_external_account_id")
        print(f"  ⚠️  Duplicate guest_external_account_id: {duplicate_external_ids}")
    else:
        print(f"  ✓ Guests: All guest_external_account_id values are unique")
    conn.close()
    
    # Check reviews - should be unique by review_id (primary key)
    reviews = session.query(Review).all()
    review_ids = [r.review_id for r in reviews]
    if len(review_ids) != len(set(review_ids)):
        issues.append(f"Reviews: Found duplicate review_ids")
        duplicates = [x for x in review_ids if review_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate review_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Reviews: {len(reviews)} unique records")
    
    # Check conversations - should be unique by conversation_id (primary key)
    conversations = session.query(Conversation).all()
    conversation_ids = [c.conversation_id for c in conversations]
    if len(conversation_ids) != len(set(conversation_ids)):
        issues.append(f"Conversations: Found duplicate conversation_ids")
        duplicates = [x for x in conversation_ids if conversation_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate conversation_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Conversations: {len(conversations)} unique records")
    
    # Check messages - should be unique by message_id (primary key)
    messages = session.query(MessageMetadata).all()
    message_ids = [m.message_id for m in messages]
    if len(message_ids) != len(set(message_ids)):
        issues.append(f"Messages: Found duplicate message_ids")
        duplicates = [x for x in message_ids if message_ids.count(x) > 1]
        print(f"  ⚠️  Duplicate message_ids: {set(duplicates)}")
    else:
        print(f"  ✓ Messages: {len(messages)} unique records")
    
    return issues


def check_foreign_keys(session):
    """Check foreign key relationships"""
    print("\n" + "=" * 80)
    print("CHECKING FOREIGN KEY RELATIONSHIPS")
    print("=" * 80)
    
    issues = []
    
    # Check reservations -> listings
    orphaned_reservations = session.query(Reservation).filter(
        ~Reservation.listing_id.in_(session.query(Listing.listing_id))
    ).count()
    if orphaned_reservations > 0:
        issues.append(f"Reservations: {orphaned_reservations} reservations with invalid listing_id")
        print(f"  ⚠️  Found {orphaned_reservations} reservations with invalid listing_id")
    else:
        print(f"  ✓ All reservations have valid listing_id")
    
    # Check reservations -> guests
    orphaned_guest_refs = session.query(Reservation).filter(
        Reservation.guest_id.isnot(None),
        ~Reservation.guest_id.in_(session.query(Guest.guest_id))
    ).count()
    if orphaned_guest_refs > 0:
        issues.append(f"Reservations: {orphaned_guest_refs} reservations with invalid guest_id")
        print(f"  ⚠️  Found {orphaned_guest_refs} reservations with invalid guest_id")
    else:
        print(f"  ✓ All reservations have valid guest_id (or NULL)")
    
    # Check reviews -> listings
    orphaned_reviews = session.query(Review).filter(
        ~Review.listing_id.in_(session.query(Listing.listing_id))
    ).count()
    if orphaned_reviews > 0:
        issues.append(f"Reviews: {orphaned_reviews} reviews with invalid listing_id")
        print(f"  ⚠️  Found {orphaned_reviews} reviews with invalid listing_id")
    else:
        print(f"  ✓ All reviews have valid listing_id")
    
    # Check reviews -> reservations
    orphaned_review_reservations = session.query(Review).filter(
        Review.reservation_id.isnot(None),
        ~Review.reservation_id.in_(session.query(Reservation.reservation_id))
    ).count()
    if orphaned_review_reservations > 0:
        issues.append(f"Reviews: {orphaned_review_reservations} reviews with invalid reservation_id")
        print(f"  ⚠️  Found {orphaned_review_reservations} reviews with invalid reservation_id")
    else:
        print(f"  ✓ All reviews have valid reservation_id (or NULL)")
    
    # Check reviews -> guests
    orphaned_review_guests = session.query(Review).filter(
        Review.guest_id.isnot(None),
        ~Review.guest_id.in_(session.query(Guest.guest_id))
    ).count()
    if orphaned_review_guests > 0:
        issues.append(f"Reviews: {orphaned_review_guests} reviews with invalid guest_id")
        print(f"  ⚠️  Found {orphaned_review_guests} reviews with invalid guest_id")
    else:
        print(f"  ✓ All reviews have valid guest_id (or NULL)")
    
    # Check listing_photos -> listings
    orphaned_photos = session.query(ListingPhoto).filter(
        ~ListingPhoto.listing_id.in_(session.query(Listing.listing_id))
    ).count()
    if orphaned_photos > 0:
        issues.append(f"ListingPhotos: {orphaned_photos} photos with invalid listing_id")
        print(f"  ⚠️  Found {orphaned_photos} photos with invalid listing_id")
    else:
        print(f"  ✓ All listing photos have valid listing_id")
    
    # Check review_sub_ratings -> reviews
    orphaned_sub_ratings = session.query(ReviewSubRating).filter(
        ~ReviewSubRating.review_id.in_(session.query(Review.review_id))
    ).count()
    if orphaned_sub_ratings > 0:
        issues.append(f"ReviewSubRatings: {orphaned_sub_ratings} sub-ratings with invalid review_id")
        print(f"  ⚠️  Found {orphaned_sub_ratings} sub-ratings with invalid review_id")
    else:
        print(f"  ✓ All review sub-ratings have valid review_id")
    
    return issues


def check_data_consistency(session):
    """Check for data consistency issues"""
    print("\n" + "=" * 80)
    print("CHECKING DATA CONSISTENCY")
    print("=" * 80)
    
    issues = []
    
    # Check for reservations without guest_id but with guest information
    reservations_without_guest_id = session.query(Reservation).filter(
        Reservation.guest_id.is_(None),
        Reservation.guest_email.isnot(None)
    ).count()
    if reservations_without_guest_id > 0:
        print(f"  ℹ️  Found {reservations_without_guest_id} reservations with guest info but no guest_id (may need guest linking)")
    else:
        print(f"  ✓ All reservations with guest info have guest_id")
    
    # Check for reviews without guest_id but with reviewer_name
    reviews_without_guest_id = session.query(Review).filter(
        Review.guest_id.is_(None),
        Review.reviewer_name.isnot(None),
        Review.reviewer_name != 'Unknown'
    ).count()
    if reviews_without_guest_id > 0:
        print(f"  ℹ️  Found {reviews_without_guest_id} reviews with reviewer_name but no guest_id (may need guest linking)")
    else:
        print(f"  ✓ All reviews with reviewer_name have guest_id")
    
    # Check for duplicate guest emails (case-insensitive)
    conn = sqlite3.connect(get_database_path())
    cursor = conn.cursor()
    cursor.execute("""
        SELECT LOWER(email) as email_lower, COUNT(*) as cnt
        FROM guests
        WHERE email IS NOT NULL AND email != ''
        GROUP BY email_lower
        HAVING cnt > 1
    """)
    duplicate_emails = cursor.fetchall()
    if duplicate_emails:
        print(f"  ℹ️  Found {len(duplicate_emails)} email addresses with multiple guest records (case-insensitive)")
        print(f"     This may be expected if guests have different external_account_ids")
    else:
        print(f"  ✓ No duplicate email addresses found")
    conn.close()
    
    return issues


def print_summary(duplicate_issues, fk_issues, consistency_issues):
    """Print summary of all checks"""
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    total_issues = len(duplicate_issues) + len(fk_issues) + len(consistency_issues)
    
    if total_issues == 0:
        print("✓ Database is consistent! No duplicate data or integrity issues found.")
        print("✓ Sync process appears to be idempotent.")
    else:
        print(f"⚠️  Found {total_issues} potential issues:")
        if duplicate_issues:
            print(f"  - {len(duplicate_issues)} duplicate record issues")
        if fk_issues:
            print(f"  - {len(fk_issues)} foreign key integrity issues")
        if consistency_issues:
            print(f"  - {len(consistency_issues)} data consistency issues")
    
    print("\n" + "=" * 80)


def main():
    db_path = get_database_path()
    init_models(db_path)
    session = get_session(db_path)
    
    try:
        duplicate_issues = check_duplicates(session)
        fk_issues = check_foreign_keys(session)
        consistency_issues = check_data_consistency(session)
        
        print_summary(duplicate_issues, fk_issues, consistency_issues)
        
        return 0 if (len(duplicate_issues) + len(fk_issues)) == 0 else 1
        
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())

