#!/usr/bin/env python3
"""
SQLAlchemy ORM models for Hostaway data system.
"""

from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.pool import NullPool
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
    
    # Relationships
    photos = relationship('ListingPhoto', back_populates='listing', cascade='all, delete-orphan')
    reservations = relationship('Reservation', back_populates='listing')
    conversations = relationship('Conversation', back_populates='listing')
    reviews = relationship('Review', back_populates='listing', cascade='all, delete-orphan')
    
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


# Database connection utilities
def get_engine(db_path: str):
    """
    Create SQLAlchemy engine with support for PostgreSQL and SQLite.
    
    Checks for DATABASE_URL environment variable first (PostgreSQL).
    Falls back to SQLite if DATABASE_URL is not set.
    
    Args:
        db_path: Path to SQLite database (used only if DATABASE_URL not set)
        
    Returns:
        SQLAlchemy engine
    """
    import os
    
    # Check for PostgreSQL connection string
    database_url = os.getenv("DATABASE_URL")
    
    if database_url:
        # PostgreSQL connection
        # Connection string format: postgresql://user:password@host:port/database
        
        # For serverless (Vercel), use NullPool to avoid connection exhaustion
        # For local development, use connection pooling
        is_vercel = os.getenv("VERCEL") == "1"
        
        if is_vercel:
            # Serverless: No connection pooling (each request gets fresh connection)
            pool_class = NullPool
            pool_size = None
            max_overflow = None
        else:
            # Local: Use connection pooling
            pool_class = None
            pool_size = 5
            max_overflow = 2
        
        engine = create_engine(
            database_url,
            echo=False,
            poolclass=pool_class,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=30 if not is_vercel else None,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 15,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5
            }
        )
        return engine
    else:
        # SQLite connection (fallback)
        # Use connect_args to set WAL mode and other pragmas at connection time
        # This avoids the need to execute PRAGMA separately which can cause locking issues
        engine = create_engine(
            f'sqlite:///{db_path}',
            echo=False,
            connect_args={
                'check_same_thread': False,
                'timeout': 30.0  # 30 second timeout for database operations
            },
            pool_pre_ping=True  # Verify connections before using
        )
        
        # Try to enable WAL mode, but don't fail if database is locked
        # WAL mode will be set on first connection if possible
        try:
            with engine.connect() as conn:
                result = conn.execute(sqlalchemy.text("PRAGMA journal_mode"))
                current_mode = result.scalar()
                if current_mode != 'wal':
                    try:
                        conn.execute(sqlalchemy.text("PRAGMA journal_mode=WAL"))
                        conn.commit()
                    except Exception:
                        # If WAL mode can't be set, continue with default mode
                        pass
        except Exception:
            # If we can't connect to set WAL mode, continue anyway
            # The database might be locked by another process
            pass
        
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
