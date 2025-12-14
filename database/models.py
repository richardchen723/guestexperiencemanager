#!/usr/bin/env python3
"""
SQLAlchemy ORM models for Hostaway data system.
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Boolean, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.types import JSON, TypeDecorator
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
    documents = relationship('DocumentListing', back_populates='listing', cascade='all, delete-orphan')
    
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
    origin = Column(String)  # Review origin: 'Guest', 'Host', etc.
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


class ReviewFilter(Base):
    """Review filter model - stores saved filter criteria for bad reviews"""
    __tablename__ = 'review_filters'
    __table_args__ = (
        {'schema': 'public'} if os.getenv("DATABASE_URL") else {},
    )
    
    filter_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=True)  # Optional name for the filter
    tag_ids = Column(JSONB if os.getenv("DATABASE_URL") else JSON)  # Array of tag IDs to filter by
    max_rating = Column(Float, nullable=True)  # Maximum rating (e.g., 4.0 for <= 4 stars)
    months_back = Column(Integer, nullable=True)  # Number of months to look back (e.g., 2 for last 2 months)
    created_by = Column(Integer, nullable=False, index=True)  # FK to users.user_id (cross-schema, so using Integer)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    document_tags = relationship('DocumentTag', back_populates='tag', cascade='all, delete-orphan')
    
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


class Document(Base):
    """Document model for knowledge base"""
    __tablename__ = 'documents'
    
    document_id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)  # User-provided or filename
    file_name = Column(String, nullable=False)  # Original filename
    file_path = Column(String, nullable=False)  # Relative path on filesystem
    file_size = Column(Integer, nullable=False)  # Bytes
    mime_type = Column(String, nullable=False)  # application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document
    file_hash = Column(String, nullable=True)  # SHA256 hash for duplicate detection
    content_text = Column(Text, nullable=True)  # Extracted text content
    is_admin_only = Column(Boolean, default=False, nullable=False)  # True = visible/searchable by admins only, False = all users
    uploaded_by = Column(Integer, nullable=False, index=True)  # FK to users.users.user_id (cross-schema, no FK constraint)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    listings = relationship('DocumentListing', back_populates='document', cascade='all, delete-orphan')
    tags = relationship('DocumentTag', back_populates='document', cascade='all, delete-orphan')
    
    def __repr__(self):
        return f"<Document(document_id={self.document_id}, title='{self.title}')>"


class DocumentListing(Base):
    """Junction table for many-to-many relationship between documents and listings"""
    __tablename__ = 'document_listings'
    
    document_id = Column(Integer, ForeignKey('documents.document_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    listing_id = Column(Integer, ForeignKey('listings.listing_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    document = relationship('Document', back_populates='listings')
    listing = relationship('Listing', back_populates='documents')
    
    def __repr__(self):
        return f"<DocumentListing(document_id={self.document_id}, listing_id={self.listing_id})>"


class DocumentTag(Base):
    """Junction table for many-to-many relationship between documents and tags"""
    __tablename__ = 'document_tags'
    
    document_id = Column(Integer, ForeignKey('documents.document_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    tag_id = Column(Integer, ForeignKey('tags.tag_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    is_inherited = Column(Boolean, default=False, nullable=False)  # True if inherited from listing
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    document = relationship('Document', back_populates='tags')
    tag = relationship('Tag', back_populates='document_tags')
    
    def __repr__(self):
        return f"<DocumentTag(document_id={self.document_id}, tag_id={self.tag_id}, is_inherited={self.is_inherited})>"


# Database connection utilities
# Engine cache to prevent connection leaks
_engine_cache = {}
_sessionmaker_cache = {}

def get_engine(db_path: str):
    """
    Create or retrieve cached SQLAlchemy engine for PostgreSQL.
    Uses singleton pattern to prevent connection leaks.
    
    Args:
        db_path: Ignored for PostgreSQL (kept for interface compatibility)
        
    Returns:
        SQLAlchemy engine (cached singleton)
    """
    import os
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is required. "
            "PostgreSQL is required for this application. "
            "Example: postgresql://user@localhost:5432/hostaway_dev"
        )
    
    # Use database_url as cache key to ensure one engine per database
    cache_key = database_url
    
    # Return cached engine if it exists
    if cache_key in _engine_cache:
        return _engine_cache[cache_key]
    
    # Create new engine with optimized pool settings
    # Reduced pool_size to prevent connection exhaustion
    engine = create_engine(
        database_url,
        echo=False,
        pool_size=3,           # Reduced from 5 to prevent exhaustion
        max_overflow=1,        # Reduced from 2 to limit total connections
        pool_timeout=30,       # Prevent hanging connections
        pool_pre_ping=True,    # Verify connections before using
        pool_recycle=3600,     # Recycle connections after 1 hour (prevents stale connections)
        pool_reset_on_return='commit',  # Reset connection state on return
        connect_args={
            "connect_timeout": 15,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
            "application_name": "hostaway_main"  # Set application name for monitoring
        }
    )
    
    # Cache the engine
    _engine_cache[cache_key] = engine
    return engine


def get_session(db_path: str):
    """
    Create database session using cached engine and sessionmaker.
    Ensures proper connection pooling and prevents leaks.
    """
    engine = get_engine(db_path)
    
    # Cache sessionmaker per engine to avoid recreating
    if engine not in _sessionmaker_cache:
        _sessionmaker_cache[engine] = sessionmaker(bind=engine)
    
    Session = _sessionmaker_cache[engine]
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


def _migrate_review_origin_column(engine):
    """Add origin column to reviews table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            # Check if reviews table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='reviews'"
            ))
            if not result.fetchone():
                return  # Table doesn't exist, create_all will handle it
            
            # Get existing columns
            result = conn.execute(sqlalchemy.text("PRAGMA table_info(reviews)"))
            existing_columns = {row[1] for row in result.fetchall()}
            
            # Add origin column if missing
            if 'origin' not in existing_columns:
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE reviews ADD COLUMN origin TEXT"))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    # Column might already exist, ignore
                    pass
        else:
            # PostgreSQL migration
            # Check if reviews table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'reviews')"
            ))
            if not result.scalar():
                return  # Table doesn't exist, create_all will handle it
            
            # Check if origin column exists
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = 'reviews' 
                    AND column_name = 'origin'
                )
            """))
            if not result.scalar():
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE public.reviews ADD COLUMN origin VARCHAR"))
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    # Column might already exist, ignore
                    pass


def _migrate_documents_table(engine):
    """Create documents table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
            ))
            if result.fetchone():
                return  # Table already exists
            
            # Table will be created by create_all, but we ensure it exists
            # create_all will handle it
            return
        else:
            # PostgreSQL migration
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'documents'
                )
            """))
            if result.scalar():
                return  # Table already exists
            
            # Create documents table
            conn.execute(sqlalchemy.text("""
                CREATE TABLE documents (
                    document_id SERIAL PRIMARY KEY,
                    title VARCHAR NOT NULL,
                    file_name VARCHAR NOT NULL,
                    file_path VARCHAR NOT NULL,
                    file_size INTEGER NOT NULL,
                    mime_type VARCHAR NOT NULL,
                    file_hash VARCHAR,
                    content_text TEXT,
                    is_admin_only BOOLEAN NOT NULL DEFAULT FALSE,
                    uploaded_by INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # Create indexes
            conn.execute(sqlalchemy.text("CREATE INDEX idx_documents_created_at ON documents(created_at)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_documents_file_hash ON documents(file_hash)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_documents_is_admin_only ON documents(is_admin_only)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_documents_uploaded_by ON documents(uploaded_by)"))
            
            # Add tsvector column for full-text search
            conn.execute(sqlalchemy.text("ALTER TABLE documents ADD COLUMN content_tsvector tsvector"))
            
            # Create GIN index for full-text search
            conn.execute(sqlalchemy.text("""
                CREATE INDEX idx_documents_content_tsvector 
                ON documents USING GIN(content_tsvector)
            """))
            
            # Create trigger to auto-update tsvector
            conn.execute(sqlalchemy.text("""
                CREATE TRIGGER documents_tsvector_update 
                BEFORE INSERT OR UPDATE ON documents
                FOR EACH ROW EXECUTE FUNCTION 
                tsvector_update_trigger(content_tsvector, 'pg_catalog.english', content_text)
            """))
            
            conn.commit()
            logger.info("Created documents table with full-text search support")


def _migrate_document_listings_table(engine):
    """Create document_listings junction table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_listings'"
            ))
            if result.fetchone():
                return  # Table already exists
            return  # create_all will handle it
        else:
            # PostgreSQL migration
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'document_listings'
                )
            """))
            if result.scalar():
                return  # Table already exists
            
            # Create document_listings table
            conn.execute(sqlalchemy.text("""
                CREATE TABLE document_listings (
                    document_id INTEGER NOT NULL,
                    listing_id INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (document_id, listing_id),
                    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE
                )
            """))
            
            # Create indexes
            conn.execute(sqlalchemy.text("CREATE INDEX idx_document_listings_document ON document_listings(document_id)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_document_listings_listing ON document_listings(listing_id)"))
            
            conn.commit()
            logger.info("Created document_listings table")


def _migrate_document_tags_table(engine):
    """Create document_tags junction table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_tags'"
            ))
            if result.fetchone():
                return  # Table already exists
            return  # create_all will handle it
        else:
            # PostgreSQL migration
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = 'document_tags'
                )
            """))
            if result.scalar():
                return  # Table already exists
            
            # Create document_tags table
            conn.execute(sqlalchemy.text("""
                CREATE TABLE document_tags (
                    document_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    is_inherited BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (document_id, tag_id),
                    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
                )
            """))
            
            # Create indexes
            conn.execute(sqlalchemy.text("CREATE INDEX idx_document_tags_document ON document_tags(document_id)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_document_tags_tag ON document_tags(tag_id)"))
            
            conn.commit()
            logger.info("Created document_tags table")


def _migrate_review_filters_table(engine):
    """Create review_filters table if it doesn't exist"""
    import os
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            # Check if review_filters table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='review_filters'"
            ))
            if not result.fetchone():
                # Create review_filters table
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE review_filters (
                        filter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT,
                        tag_ids TEXT,
                        max_rating REAL,
                        months_back INTEGER,
                        created_by INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS ix_review_filters_created_by ON review_filters(created_by)"))
                conn.execute(sqlalchemy.text("CREATE INDEX IF NOT EXISTS ix_review_filters_created_at ON review_filters(created_at)"))
                conn.commit()
        else:
            # PostgreSQL migration
            # Check if review_filters table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'review_filters')"
            ))
            if not result.scalar():
                # Create review_filters table
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE public.review_filters (
                        filter_id SERIAL PRIMARY KEY,
                        name VARCHAR,
                        tag_ids JSONB,
                        max_rating REAL,
                        months_back INTEGER,
                        created_by INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(sqlalchemy.text("CREATE INDEX ix_review_filters_created_by ON public.review_filters(created_by)"))
                conn.execute(sqlalchemy.text("CREATE INDEX ix_review_filters_created_at ON public.review_filters(created_at)"))
                conn.commit()
