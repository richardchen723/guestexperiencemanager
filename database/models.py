#!/usr/bin/env python3
"""
SQLAlchemy ORM models for Hostaway data system.
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Boolean, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.sql import func
import sqlalchemy
from datetime import datetime
import json
import os

Base = declarative_base()


class Listing(Base):
    """Listing model - maps to Hostaway API listings endpoint"""
    __tablename__ = 'listings'
    
    listing_id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(Text)
    property_type_id = Column(Integer)
    accommodates = Column(Integer)
    bedrooms = Column(Integer)
    bathrooms = Column(Float)
    beds = Column(Integer)
    square_meters = Column(Float)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    zipcode = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    timezone_name = Column(String)
    base_price = Column(Float)
    currency = Column(String)
    check_in_time_start = Column(Integer)
    check_in_time_end = Column(Integer)
    check_out_time = Column(Integer)
    status = Column(String)
    amenities = Column(Text)  # JSON array
    account_id = Column(Integer)
    custom_fields = Column(Text)  # JSON object
    inserted_on = Column(DateTime)
    updated_on = Column(DateTime)
    last_synced_at = Column(DateTime)
    internal_listing_name = Column(String)  # Internal listing name from Hostaway API
    
    # Relationships
    photos = relationship('ListingPhoto', back_populates='listing', cascade='all, delete-orphan')
    reservations = relationship('Reservation', back_populates='listing')
    conversations = relationship('Conversation', back_populates='listing')
    reviews = relationship('Review', back_populates='listing', cascade='all, delete-orphan')
    tags = relationship('ListingTag', back_populates='listing', cascade='all, delete-orphan')
    
    def get_amenities_list(self):
        """Parse amenities JSON string to list"""
        if self.amenities:
            try:
                return json.loads(self.amenities)
            except:
                return []
        return []
    
    def get_custom_fields_dict(self):
        """Parse custom_fields JSON string to dict"""
        if self.custom_fields:
            try:
                return json.loads(self.custom_fields)
            except:
                return {}
        return {}


class ListingPhoto(Base):
    """Listing photo model - stores photo URLs and metadata"""
    __tablename__ = 'listing_photos'
    
    photo_id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'), nullable=False)
    photo_url = Column(String, nullable=False)
    thumbnail_url = Column(String)
    photo_type = Column(String)
    display_order = Column(Integer)
    caption = Column(String)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime)
    
    # Relationships
    listing = relationship('Listing', back_populates='photos')


class Guest(Base):
    """Guest model - extracted and deduplicated from reservations"""
    __tablename__ = 'guests'
    
    guest_id = Column(Integer, primary_key=True, autoincrement=True)
    guest_external_account_id = Column(String, unique=True)
    first_name = Column(String)
    last_name = Column(String)
    full_name = Column(String)
    email = Column(String)
    phone = Column(String)
    country = Column(String)
    city = Column(String)
    address = Column(String)
    zipcode = Column(String)
    guest_picture = Column(String)
    guest_recommendations = Column(Integer)
    guest_trips = Column(Integer)
    guest_work = Column(String)
    is_guest_identity_verified = Column(Integer, default=0)
    is_guest_verified_by_email = Column(Integer, default=0)
    is_guest_verified_by_phone = Column(Integer, default=0)
    is_guest_verified_by_reviews = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime)
    last_synced_at = Column(DateTime)
    
    # Relationships
    reservations = relationship('Reservation', back_populates='guest')
    conversations = relationship('Conversation', back_populates='guest')
    reviews = relationship('Review', back_populates='guest', cascade='all, delete-orphan')


class Reservation(Base):
    """Reservation model - maps to Hostaway API reservations endpoint"""
    __tablename__ = 'reservations'
    
    reservation_id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'), nullable=False)
    guest_id = Column(Integer, ForeignKey('guests.guest_id', ondelete='SET NULL'))
    channel_id = Column(Integer)
    channel_name = Column(String)
    source = Column(String)
    channel_reservation_id = Column(String)
    confirmation_code = Column(String)
    guest_name = Column(String)
    guest_first_name = Column(String)
    guest_last_name = Column(String)
    guest_email = Column(String)
    guest_phone = Column(String)
    guest_country = Column(String)
    guest_city = Column(String)
    guest_address = Column(String)
    guest_zipcode = Column(String)
    arrival_date = Column(Date)
    departure_date = Column(Date)
    nights = Column(Integer)
    is_dates_unspecified = Column(Integer, default=0)
    number_of_guests = Column(Integer)
    adults = Column(Integer)
    children = Column(Integer)
    infants = Column(Integer)
    pets = Column(Integer)
    total_price = Column(Float)
    currency = Column(String)
    tax_amount = Column(Float)
    cleaning_fee = Column(Float)
    security_deposit_fee = Column(Float)
    remaining_balance = Column(Float)
    status = Column(String)
    payment_status = Column(String)
    is_paid = Column(Integer, default=0)
    is_starred = Column(Integer, default=0)
    is_archived = Column(Integer, default=0)
    is_pinned = Column(Integer, default=0)
    reservation_date = Column(DateTime)
    cancellation_date = Column(DateTime)
    cancelled_by = Column(String)
    host_note = Column(Text)
    guest_note = Column(Text)
    comment = Column(Text)
    custom_field_values = Column(Text)  # JSON array
    inserted_on = Column(DateTime)
    updated_on = Column(DateTime)
    latest_activity_on = Column(DateTime)
    last_synced_at = Column(DateTime)
    
    # Relationships
    listing = relationship('Listing', back_populates='reservations')
    guest = relationship('Guest', back_populates='reservations')
    conversations = relationship('Conversation', back_populates='reservation')
    reviews = relationship('Review', back_populates='reservation', cascade='all, delete-orphan')
    
    def get_custom_field_values_list(self):
        """Parse custom_field_values JSON string to list"""
        if self.custom_field_values:
            try:
                return json.loads(self.custom_field_values)
            except:
                return []
        return []


class Conversation(Base):
    """Conversation model - links to message files"""
    __tablename__ = 'conversations'
    
    conversation_id = Column(Integer, primary_key=True)
    reservation_id = Column(Integer, ForeignKey('reservations.reservation_id', ondelete='CASCADE'))
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'))
    guest_id = Column(Integer, ForeignKey('guests.guest_id', ondelete='SET NULL'))
    channel_id = Column(Integer)
    communication_type = Column(String)
    status = Column(String)
    message_count = Column(Integer, default=0)
    first_message_at = Column(DateTime)
    last_message_at = Column(DateTime)
    conversation_file_path = Column(String)
    inserted_on = Column(DateTime)
    updated_on = Column(DateTime)
    last_synced_at = Column(DateTime)
    
    # Relationships
    reservation = relationship('Reservation', back_populates='conversations')
    listing = relationship('Listing', back_populates='conversations')
    guest = relationship('Guest', back_populates='conversations')
    messages = relationship('MessageMetadata', back_populates='conversation')


class MessageMetadata(Base):
    """Message metadata model - indexes messages for search"""
    __tablename__ = 'messages_metadata'
    
    message_id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.conversation_id', ondelete='CASCADE'), nullable=False)
    reservation_id = Column(Integer, ForeignKey('reservations.reservation_id', ondelete='CASCADE'))
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'))
    guest_id = Column(Integer, ForeignKey('guests.guest_id', ondelete='SET NULL'))
    sender_type = Column(String)
    sender_name = Column(String)
    is_incoming = Column(Integer, default=0)
    message_type = Column(String)
    content_preview = Column(Text)
    has_attachment = Column(Integer, default=0)
    created_at = Column(DateTime)
    message_file_path = Column(String)
    
    # Relationships
    conversation = relationship('Conversation', back_populates='messages')
    reservation = relationship('Reservation')
    listing = relationship('Listing')
    guest = relationship('Guest')


class Review(Base):
    """Review model - maps to Hostaway API reviews endpoint"""
    __tablename__ = 'reviews'
    
    review_id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'), nullable=False)
    reservation_id = Column(Integer, ForeignKey('reservations.reservation_id', ondelete='CASCADE'))
    guest_id = Column(Integer, ForeignKey('guests.guest_id', ondelete='SET NULL'))
    channel_id = Column(Integer)
    channel_name = Column(String)
    overall_rating = Column(Float)
    review_text = Column(Text)
    reviewer_name = Column(String)
    reviewer_picture = Column(String)
    review_date = Column(Date)
    response_text = Column(Text)
    response_date = Column(Date)
    is_verified = Column(Integer, default=0)
    language = Column(String)
    helpful_count = Column(Integer)
    status = Column(String)  # Review status: 'published', 'pending', 'rejected', etc.
    inserted_on = Column(DateTime)
    updated_on = Column(DateTime)
    last_synced_at = Column(DateTime)
    
    # Relationships
    listing = relationship('Listing', back_populates='reviews')
    reservation = relationship('Reservation', back_populates='reviews')
    guest = relationship('Guest', back_populates='reviews')
    sub_ratings = relationship('ReviewSubRating', back_populates='review', cascade='all, delete-orphan')


class ReviewSubRating(Base):
    """Review sub-rating model - stores individual rating categories"""
    __tablename__ = 'review_sub_ratings'
    
    sub_rating_id = Column(Integer, primary_key=True, autoincrement=True)
    review_id = Column(Integer, ForeignKey('reviews.review_id', ondelete='CASCADE'), nullable=False)
    rating_category = Column(String, nullable=False)
    rating_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_synced_at = Column(DateTime)
    
    # Relationships
    review = relationship('Review', back_populates='sub_ratings')


class SyncLog(Base):
    """Sync log model - tracks all sync operations"""
    __tablename__ = 'sync_logs'
    __table_args__ = (
        {'schema': 'public'} if os.getenv("DATABASE_URL") else {},
    )
    
    sync_id = Column(Integer, primary_key=True, autoincrement=True)
    sync_run_id = Column(Integer, nullable=True, index=True)  # Groups multiple sync_types from same sync run - indexed for performance
    sync_type = Column(String, nullable=False)
    sync_mode = Column(String, nullable=True)  # 'full' or 'incremental'
    status = Column(String, nullable=False)
    records_processed = Column(Integer, default=0)
    records_created = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    errors = Column(Text)  # JSON array
    listing_stats = Column(Text)  # JSON object: {listing_id: {messages: count, reviews: count, reservations: count}}
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)
    
    def get_errors_list(self):
        """Parse errors JSON string to list"""
        if self.errors:
            try:
                return json.loads(self.errors)
            except:
                return []
        return []
    
    def get_listing_stats(self):
        """Parse listing_stats JSON string to dict"""
        if self.listing_stats:
            try:
                return json.loads(self.listing_stats)
            except:
                return {}
        return {}


class SyncJob(Base):
    """Sync job model - tracks sync job status and progress"""
    __tablename__ = 'sync_jobs'
    __table_args__ = (
        {'schema': 'public'} if os.getenv("DATABASE_URL") else {},
    )
    
    job_id = Column(String(36), primary_key=True)  # UUID string
    sync_run_id = Column(Integer, nullable=False, index=True)  # Links to sync_logs.sync_run_id
    sync_mode = Column(String(20), nullable=False)  # 'full' or 'incremental'
    status = Column(String(20), nullable=False)  # 'pending', 'running', 'completed', 'error', 'cancelled'
    progress = Column(JSONB if os.getenv("DATABASE_URL") else JSON)  # Progress data as JSON
    error_message = Column(Text)  # Error message if status='error'
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def get_progress(self) -> dict:
        """Get progress data as dictionary"""
        if self.progress:
            if isinstance(self.progress, str):
                try:
                    return json.loads(self.progress)
                except:
                    return {}
            return self.progress
        return {}
    
    def set_progress(self, progress_data: dict):
        """Set progress data from dictionary"""
        self.progress = progress_data


class Tag(Base):
    """Tag model - stores tag definitions"""
    __tablename__ = 'tags'
    
    tag_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True, index=True)
    color = Column(String(7), nullable=True)  # Hex color code (e.g., #FF5733)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    listing_tags = relationship('ListingTag', back_populates='tag', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f"<Tag(tag_id={self.tag_id}, name='{self.name}')>"
    
    @staticmethod
    def normalize_name(name: str) -> str:
        """Normalize tag name: lowercase, trim whitespace, validate"""
        if not name:
            raise ValueError("Tag name cannot be empty")
        normalized = name.strip().lower()
        if len(normalized) == 0:
            raise ValueError("Tag name cannot be empty")
        if len(normalized) > 50:
            raise ValueError("Tag name cannot exceed 50 characters")
        # Allow alphanumeric, spaces, hyphens, underscores
        if not all(c.isalnum() or c in (' ', '-', '_') for c in normalized):
            raise ValueError("Tag name can only contain letters, numbers, spaces, hyphens, and underscores")
        return normalized


class ListingTag(Base):
    """Junction table for many-to-many relationship between listings and tags"""
    __tablename__ = 'listing_tags'
    
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey('tags.tag_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    listing = relationship('Listing', back_populates='tags')
    tag = relationship('Tag', back_populates='listing_tags')
    
    __table_args__ = (
        UniqueConstraint('listing_id', 'tag_id', name='uq_listing_tag'),
    )
    
    def __repr__(self):
        return f"<ListingTag(listing_id={self.listing_id}, tag_id={self.tag_id})>"


# Database connection utilities
def get_engine(db_path: str):
    """
    Create SQLAlchemy engine for PostgreSQL.
    PostgreSQL is required - no SQLite fallback.
    
    Args:
        db_path: Ignored for PostgreSQL (kept for interface compatibility)
        
    Returns:
        SQLAlchemy engine
    """
    import os
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is required. "
            "PostgreSQL is required for this application. "
            "Example: postgresql://user@localhost:5432/hostaway_dev"
        )
    
    # PostgreSQL connection
    # Connection string format: postgresql://user:password@host:port/database
    engine = create_engine(
        database_url,
        echo=False,
        pool_size=5,           # Small pool for 10-20 users
        max_overflow=2,        # Minimal overflow
        pool_timeout=30,       # Prevent hanging connections
        pool_pre_ping=True,     # Verify connections before using
        connect_args={
            "connect_timeout": 15,  # Increased from 10 to handle slower connections
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }
    )
    return engine


def get_session(db_path: str):
    """Create database session"""
    engine = get_engine(db_path)
    Session = sessionmaker(bind=engine)
    return Session()


def init_models(db_path: str):
    """
    Initialize database with all models.
    Supports both PostgreSQL and SQLite.
    """
    import os
    
    # Check if using PostgreSQL
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        # SQLite: Ensure database directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    engine = get_engine(db_path)
    
    # Create tables if they don't exist
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        # If database is locked (SQLite), try again after a short wait
        if not database_url:
            import time
            time.sleep(0.5)
            try:
                Base.metadata.create_all(engine)
            except Exception:
                # If still locked, raise the original error
                raise e
        else:
            # For PostgreSQL, just raise the error
            raise e
    
    # Create indexes for PostgreSQL (SQLite indexes are created in schema.py)
    if database_url:
        # Create index on sync_run_id if it doesn't exist (for PostgreSQL)
        # Note: The index is also defined in the model column, but we ensure it exists here
        with engine.begin() as conn:
            # Check if index exists
            if 'postgresql' in database_url.lower():
                # PostgreSQL - check in public schema
                result = conn.execute(sqlalchemy.text("""
                    SELECT 1 FROM pg_indexes 
                    WHERE schemaname = 'public' AND tablename = 'sync_logs' AND indexname = 'idx_sync_logs_run_id'
                """))
                if not result.fetchone():
                    try:
                        conn.execute(sqlalchemy.text("CREATE INDEX idx_sync_logs_run_id ON public.sync_logs(sync_run_id)"))
                    except Exception:
                        # Index might already exist or table might not exist yet, ignore
                        pass
            # For other databases, indexes should be created via schema.py or migrations
    
    # SQLite-specific migrations (skip for PostgreSQL)
    if not database_url:
        # Migrate sync_logs table if needed (add new columns)
        _migrate_sync_logs_table(engine)
        
        # Migrate reviews table if needed (add status column)
        _migrate_reviews_table(engine)
        
        # Migrate tags tables if needed
        _migrate_tags_tables(engine)
        
        # Migrate listings table if needed (add internal_listing_name column)
        _migrate_listings_table(engine)
    else:
        # PostgreSQL migrations
        _migrate_listings_table(engine)
    
    return engine


def _migrate_sync_logs_table(engine):
    """Add new columns to sync_logs table if they don't exist"""
    with engine.connect() as conn:
        # Check if sync_logs table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_logs'"
        ))
        if not result.fetchone():
            return  # Table doesn't exist, create_all will handle it
        
        # Get existing columns
        result = conn.execute(sqlalchemy.text("PRAGMA table_info(sync_logs)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # Add missing columns
        if 'sync_run_id' not in existing_columns:
            try:
                conn.execute(sqlalchemy.text("ALTER TABLE sync_logs ADD COLUMN sync_run_id INTEGER"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                # Column might already exist, ignore
                pass
        
        if 'sync_mode' not in existing_columns:
            try:
                conn.execute(sqlalchemy.text("ALTER TABLE sync_logs ADD COLUMN sync_mode TEXT"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                pass
        
        if 'listing_stats' not in existing_columns:
            try:
                conn.execute(sqlalchemy.text("ALTER TABLE sync_logs ADD COLUMN listing_stats TEXT"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                pass


def _migrate_reviews_table(engine):
    """Add status column to reviews table if it doesn't exist"""
    with engine.connect() as conn:
        # Check if reviews table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reviews'"
        ))
        if not result.fetchone():
            return  # Table doesn't exist, create_all will handle it
        
        # Get existing columns
        result = conn.execute(sqlalchemy.text("PRAGMA table_info(reviews)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # Add status column if missing
        if 'status' not in existing_columns:
            try:
                conn.execute(sqlalchemy.text("ALTER TABLE reviews ADD COLUMN status TEXT"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                # Column might already exist, ignore
                pass


def _migrate_listings_table(engine):
    """Add internal_listing_name column to listings table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            # Check if listings table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
            ))
            if not result.fetchone():
                return  # Table doesn't exist, create_all will handle it
            
            # Get existing columns
            result = conn.execute(sqlalchemy.text("PRAGMA table_info(listings)"))
            existing_columns = {row[1] for row in result.fetchall()}
            
            # Add internal_listing_name column if missing
            if 'internal_listing_name' not in existing_columns:
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE listings ADD COLUMN internal_listing_name TEXT"))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    # Column might already exist, ignore
                    pass
        else:
            # PostgreSQL migration
            # Check if listings table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'listings')"
            ))
            if not result.scalar():
                return  # Table doesn't exist, create_all will handle it
            
            # Check if internal_listing_name column exists
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = 'listings' 
                    AND column_name = 'internal_listing_name'
                )
            """))
            if not result.scalar():
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE public.listings ADD COLUMN internal_listing_name VARCHAR"))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    # Column might already exist, ignore
                    pass


def _migrate_tags_tables(engine):
    """Create tags tables if they don't exist (SQLite only)"""
    with engine.connect() as conn:
        # Check if tags table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tags'"
        ))
        if not result.fetchone():
            # Create tags table
            conn.execute(sqlalchemy.text("""
                CREATE TABLE tags (
                    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    color TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)"))
            conn.commit()
        
        # Check if listing_tags table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listing_tags'"
        ))
        if not result.fetchone():
            # Create listing_tags table
            conn.execute(sqlalchemy.text("""
                CREATE TABLE listing_tags (
                    listing_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (listing_id, tag_id),
                    FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
                )
            """))
            conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_listing_tags_listing ON listing_tags(listing_id)"))
            conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS idx_listing_tags_tag ON listing_tags(tag_id)"))
            conn.commit()
