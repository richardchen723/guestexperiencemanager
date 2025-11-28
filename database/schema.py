#!/usr/bin/env python3
"""
Database schema definitions for Hostaway data system.
Creates SQLite database with all required tables.
"""

import sqlite3
import os
from pathlib import Path


def get_database_path():
    """Get the path to the SQLite database file"""
    # Create data directory if it doesn't exist
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    db_dir = data_dir / "database"
    db_dir.mkdir(exist_ok=True)
    
    return str(db_dir / "hostaway.db")


def create_schema(db_path: str):
    """Create all database tables"""
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON")
    
    # 1. Listings Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            listing_id INTEGER PRIMARY KEY,
            name TEXT,
            description TEXT,
            property_type_id INTEGER,
            accommodates INTEGER,
            bedrooms INTEGER,
            bathrooms REAL,
            beds INTEGER,
            square_meters REAL,
            address TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            zipcode TEXT,
            latitude REAL,
            longitude REAL,
            timezone_name TEXT,
            base_price REAL,
            currency TEXT,
            check_in_time_start INTEGER,
            check_in_time_end INTEGER,
            check_out_time INTEGER,
            status TEXT,
            amenities TEXT,
            account_id INTEGER,
            custom_fields TEXT,
            inserted_on TIMESTAMP,
            updated_on TIMESTAMP,
            last_synced_at TIMESTAMP
        )
    """)
    
    # 2. Listing Photos Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listing_photos (
            photo_id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            photo_url TEXT NOT NULL,
            thumbnail_url TEXT,
            photo_type TEXT,
            display_order INTEGER,
            caption TEXT,
            width INTEGER,
            height INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_synced_at TIMESTAMP,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE
        )
    """)
    
    # 3. Guests Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS guests (
            guest_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guest_external_account_id TEXT UNIQUE,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT,
            email TEXT,
            phone TEXT,
            country TEXT,
            city TEXT,
            address TEXT,
            zipcode TEXT,
            guest_picture TEXT,
            guest_recommendations INTEGER,
            guest_trips INTEGER,
            guest_work TEXT,
            is_guest_identity_verified INTEGER DEFAULT 0,
            is_guest_verified_by_email INTEGER DEFAULT 0,
            is_guest_verified_by_phone INTEGER DEFAULT 0,
            is_guest_verified_by_reviews INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            last_synced_at TIMESTAMP
        )
    """)
    
    # 4. Reservations Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL,
            guest_id INTEGER,
            channel_id INTEGER,
            channel_name TEXT,
            source TEXT,
            channel_reservation_id TEXT,
            confirmation_code TEXT,
            guest_name TEXT,
            guest_first_name TEXT,
            guest_last_name TEXT,
            guest_email TEXT,
            guest_phone TEXT,
            guest_country TEXT,
            guest_city TEXT,
            guest_address TEXT,
            guest_zipcode TEXT,
            arrival_date DATE,
            departure_date DATE,
            nights INTEGER,
            is_dates_unspecified INTEGER DEFAULT 0,
            number_of_guests INTEGER,
            adults INTEGER,
            children INTEGER,
            infants INTEGER,
            pets INTEGER,
            total_price REAL,
            currency TEXT,
            tax_amount REAL,
            cleaning_fee REAL,
            security_deposit_fee REAL,
            remaining_balance REAL,
            status TEXT,
            payment_status TEXT,
            is_paid INTEGER DEFAULT 0,
            is_starred INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0,
            is_pinned INTEGER DEFAULT 0,
            reservation_date TIMESTAMP,
            cancellation_date TIMESTAMP,
            cancelled_by TEXT,
            host_note TEXT,
            guest_note TEXT,
            comment TEXT,
            custom_field_values TEXT,
            inserted_on TIMESTAMP,
            updated_on TIMESTAMP,
            latest_activity_on TIMESTAMP,
            last_synced_at TIMESTAMP,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE,
            FOREIGN KEY (guest_id) REFERENCES guests(guest_id) ON DELETE SET NULL
        )
    """)
    
    # 5. Conversations Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id INTEGER PRIMARY KEY,
            reservation_id INTEGER,
            listing_id INTEGER,
            guest_id INTEGER,
            channel_id INTEGER,
            communication_type TEXT,
            status TEXT,
            message_count INTEGER DEFAULT 0,
            first_message_at TIMESTAMP,
            last_message_at TIMESTAMP,
            conversation_file_path TEXT,
            inserted_on TIMESTAMP,
            updated_on TIMESTAMP,
            last_synced_at TIMESTAMP,
            FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id) ON DELETE CASCADE,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE,
            FOREIGN KEY (guest_id) REFERENCES guests(guest_id) ON DELETE SET NULL
        )
    """)
    
    # 6. Messages Metadata Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages_metadata (
            message_id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            reservation_id INTEGER,
            listing_id INTEGER,
            guest_id INTEGER,
            sender_type TEXT,
            sender_name TEXT,
            is_incoming INTEGER DEFAULT 0,
            message_type TEXT,
            content_preview TEXT,
            has_attachment INTEGER DEFAULT 0,
            created_at TIMESTAMP,
            message_file_path TEXT,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id) ON DELETE CASCADE,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE,
            FOREIGN KEY (guest_id) REFERENCES guests(guest_id) ON DELETE SET NULL
        )
    """)
    
    # 7. Reviews Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            review_id INTEGER PRIMARY KEY,
            listing_id INTEGER NOT NULL,
            reservation_id INTEGER,
            guest_id INTEGER,
            channel_id INTEGER,
            channel_name TEXT,
            overall_rating REAL,
            review_text TEXT,
            reviewer_name TEXT,
            reviewer_picture TEXT,
            review_date DATE,
            response_text TEXT,
            response_date DATE,
            is_verified INTEGER DEFAULT 0,
            language TEXT,
            helpful_count INTEGER,
            inserted_on TIMESTAMP,
            updated_on TIMESTAMP,
            last_synced_at TIMESTAMP,
            FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE,
            FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id) ON DELETE CASCADE,
            FOREIGN KEY (guest_id) REFERENCES guests(guest_id) ON DELETE SET NULL
        )
    """)
    
    # 8. Review Sub-Ratings Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS review_sub_ratings (
            sub_rating_id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id INTEGER NOT NULL,
            rating_category TEXT NOT NULL,
            rating_value REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_synced_at TIMESTAMP,
            FOREIGN KEY (review_id) REFERENCES reviews(review_id) ON DELETE CASCADE
        )
    """)
    
    # 9. Sync Log Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_logs (
            sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_type TEXT NOT NULL,
            status TEXT NOT NULL,
            records_processed INTEGER DEFAULT 0,
            records_created INTEGER DEFAULT 0,
            records_updated INTEGER DEFAULT 0,
            errors TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            duration_seconds REAL
        )
    """)
    
    # Create indexes for better query performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_account ON listings(account_id)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_listing ON listing_photos(listing_id)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_guests_external_id ON guests(guest_external_account_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_guests_email ON guests(email)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_listing ON reservations(listing_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_guest ON reservations(guest_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_status ON reservations(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_arrival ON reservations(arrival_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reservations_departure ON reservations(departure_date)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_reservation ON conversations(reservation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_listing ON conversations(listing_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_guest ON conversations(guest_id)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages_metadata(conversation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_reservation ON messages_metadata(reservation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_listing ON messages_metadata(listing_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_guest ON messages_metadata(guest_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages_metadata(created_at)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_listing ON reviews(listing_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_reservation ON reviews(reservation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_guest ON reviews(guest_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_channel ON reviews(channel_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews(review_date)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sub_ratings_review ON review_sub_ratings(review_id)")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_logs_type ON sync_logs(sync_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_logs_status ON sync_logs(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_logs_started ON sync_logs(started_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sync_logs_run_id ON sync_logs(sync_run_id)")
    
    conn.commit()
    conn.close()
    
    print(f"Database schema created successfully at: {db_path}")


def init_database():
    """Initialize the database with schema"""
    db_path = get_database_path()
    create_schema(db_path)
    return db_path


if __name__ == "__main__":
    init_database()
