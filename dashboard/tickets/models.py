#!/usr/bin/env python3
"""
Ticket database models and utilities.
"""

import sys
import os
import logging
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Date, Boolean, UniqueConstraint
import sqlalchemy
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON
from pathlib import Path

logger = logging.getLogger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import dashboard.config as config
from dashboard.auth.models import Base, get_engine, get_session as get_user_session

# Alias for external use
get_session = get_user_session

# Ticket statuses
TICKET_STATUSES = ['Open', 'Assigned', 'In Progress', 'Blocked', 'Resolved', 'Closed']
TICKET_PRIORITIES = ['Low', 'Medium', 'High', 'Critical']
TICKET_CATEGORIES = ['cleaning', 'maintenance', 'online', 'other']


class Ticket(Base):
    """Ticket model for action items tied to listing issues."""
    __tablename__ = 'tickets'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    ticket_id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, nullable=True, index=True)  # References main database listings (nullable for general tickets)
    issue_title = Column(String, nullable=False)  # The issue title this ticket addresses
    title = Column(String, nullable=False)
    description = Column(Text)
    # Foreign key reference - adjust schema prefix for PostgreSQL
    _users_fk_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    assigned_user_id = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=True, index=True)
    status = Column(String, nullable=False, default='Open', index=True)
    priority = Column(String, default='Low')
    category = Column(String, nullable=False, default='other', index=True)
    due_date = Column(Date, nullable=True)
    created_by = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Recurring task fields
    is_recurring = Column(Boolean, default=False, nullable=False)
    is_recurring_active = Column(Boolean, default=True, nullable=False)
    frequency_value = Column(Integer, nullable=True)  # Number in frequency (e.g., 30 for "30 days")
    frequency_unit = Column(String, nullable=True)  # Unit: "days" or "months"
    initial_due_date = Column(Date, nullable=True)  # Original due date when recurring was set
    recurring_admin_id = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=True)  # Admin to assign on reopen
    reopen_days_before_due_date = Column(Integer, nullable=True, default=10)  # Days before due date to reopen
    
    # Relationships
    assigned_user = relationship('User', foreign_keys=[assigned_user_id])
    creator = relationship('User', foreign_keys=[created_by])
    comments = relationship('TicketComment', back_populates='ticket', cascade='all, delete-orphan', order_by='TicketComment.created_at')
    tags = relationship('TicketTag', back_populates='ticket', cascade='all, delete-orphan')
    images = relationship('TicketImage', back_populates='ticket', cascade='all, delete-orphan', order_by='TicketImage.created_at')
    
    def __repr__(self):
        return f"<Ticket(ticket_id={self.ticket_id}, title='{self.title}', status='{self.status}')>"
    
    def to_dict(self, include_comments=False):
        """Convert ticket to dictionary."""
        result = {
            'ticket_id': self.ticket_id,
            'listing_id': self.listing_id,
            'issue_title': self.issue_title,
            'title': self.title,
            'description': self.description,
            'assigned_user_id': self.assigned_user_id,
            'assigned_user_name': self.assigned_user.name if self.assigned_user else None,
            'assigned_user_email': self.assigned_user.email if self.assigned_user else None,
            'status': self.status,
            'priority': self.priority,
            'category': self.category,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'created_by': self.created_by,
            'created_by_name': self.creator.name if self.creator else None,
            'created_by_email': self.creator.email if self.creator else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'is_recurring': self.is_recurring,
            'is_recurring_active': self.is_recurring_active,
            'frequency_value': self.frequency_value,
            'frequency_unit': self.frequency_unit,
            'initial_due_date': self.initial_due_date.isoformat() if self.initial_due_date else None,
            'recurring_admin_id': self.recurring_admin_id,
            'reopen_days_before_due_date': self.reopen_days_before_due_date if self.reopen_days_before_due_date is not None else 10,
        }
        
        if include_comments:
            result['comments'] = [comment.to_dict() for comment in self.comments]
        
        # Include images
        result['images'] = [img.to_dict() for img in self.images] if hasattr(self, 'images') else []
        
        return result


class TicketComment(Base):
    """Comment model for ticket discussions."""
    __tablename__ = 'ticket_comments'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    comment_id = Column(Integer, primary_key=True, autoincrement=True)
    # Foreign key references - adjust schema prefix for PostgreSQL
    _tickets_fk_schema = 'tickets.' if os.getenv("DATABASE_URL") else ''
    _users_fk_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    ticket_id = Column(Integer, ForeignKey(f'{_tickets_fk_schema}tickets.ticket_id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=False)
    comment_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    ticket = relationship('Ticket', back_populates='comments')
    user = relationship('User', foreign_keys=[user_id])
    images = relationship('CommentImage', back_populates='comment', cascade='all, delete-orphan', order_by='CommentImage.created_at')
    
    def __repr__(self):
        return f"<TicketComment(comment_id={self.comment_id}, ticket_id={self.ticket_id})>"
    
    def to_dict(self):
        """Convert comment to dictionary."""
        result = {
            'comment_id': self.comment_id,
            'ticket_id': self.ticket_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else None,
            'user_email': self.user.email if self.user else None,
            'user_picture_url': self.user.picture_url if self.user else None,
            'comment_text': self.comment_text,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        
        # Include images - safely access the relationship
        try:
            if hasattr(self, 'images'):
                # Access images to ensure they're loaded
                images_list = list(self.images) if self.images else []
                result['images'] = [img.to_dict() for img in images_list]
            else:
                result['images'] = []
        except Exception as e:
            # If there's an error accessing images (e.g., detached instance), return empty list
            result['images'] = []
        
        return result


class TicketTag(Base):
    """Junction table for many-to-many relationship between tickets and tags"""
    __tablename__ = 'ticket_tags'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    # Foreign key references - adjust schema prefix for PostgreSQL
    _tickets_fk_schema = 'tickets.' if os.getenv("DATABASE_URL") else ''
    
    ticket_id = Column(Integer, ForeignKey(f'{_tickets_fk_schema}tickets.ticket_id', ondelete='CASCADE'), primary_key=True, nullable=False, index=True)
    # tag_id references tags table in main database (public schema) - no FK constraint since it's cross-database
    tag_id = Column(Integer, primary_key=True, nullable=False, index=True)
    is_inherited = Column(Boolean, default=False, nullable=False)  # True if inherited from property, False if ticket-specific
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    ticket = relationship('Ticket', back_populates='tags')
    # Note: Tag relationship would need to be set up separately since Tag is in different schema
    
    def __repr__(self):
        return f"<TicketTag(ticket_id={self.ticket_id}, tag_id={self.tag_id}, is_inherited={self.is_inherited})>"


class TicketImage(Base):
    """Image model for ticket attachments."""
    __tablename__ = 'ticket_images'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    image_id = Column(Integer, primary_key=True, autoincrement=True)
    # Foreign key references - adjust schema prefix for PostgreSQL
    _tickets_fk_schema = 'tickets.' if os.getenv("DATABASE_URL") else ''
    _users_fk_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    ticket_id = Column(Integer, ForeignKey(f'{_tickets_fk_schema}tickets.ticket_id', ondelete='CASCADE'), nullable=False, index=True)
    file_path = Column(String, nullable=False)  # Relative path to image file
    file_name = Column(String, nullable=False)  # Original filename
    file_size = Column(Integer, nullable=False)  # Size in bytes
    mime_type = Column(String, nullable=False)  # image/jpeg, image/png, etc.
    width = Column(Integer, nullable=True)  # Image width in pixels
    height = Column(Integer, nullable=True)  # Image height in pixels
    thumbnail_path = Column(String, nullable=True)  # Optional thumbnail path
    uploaded_by = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    ticket = relationship('Ticket', back_populates='images')
    uploader = relationship('User', foreign_keys=[uploaded_by])
    
    def __repr__(self):
        return f"<TicketImage(image_id={self.image_id}, ticket_id={self.ticket_id}, file_name='{self.file_name}')>"
    
    def to_dict(self):
        """Convert image to dictionary."""
        return {
            'image_id': self.image_id,
            'ticket_id': self.ticket_id,
            'file_path': self.file_path,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'width': self.width,
            'height': self.height,
            'thumbnail_path': self.thumbnail_path,
            'uploaded_by': self.uploaded_by,
            'uploaded_by_name': self.uploader.name if self.uploader else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CommentImage(Base):
    """Image model for comment attachments."""
    __tablename__ = 'comment_images'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    image_id = Column(Integer, primary_key=True, autoincrement=True)
    # Foreign key references - adjust schema prefix for PostgreSQL
    _tickets_fk_schema = 'tickets.' if os.getenv("DATABASE_URL") else ''
    _users_fk_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    comment_id = Column(Integer, ForeignKey(f'{_tickets_fk_schema}ticket_comments.comment_id', ondelete='CASCADE'), nullable=False, index=True)
    file_path = Column(String, nullable=False)  # Relative path to image file
    file_name = Column(String, nullable=False)  # Original filename
    file_size = Column(Integer, nullable=False)  # Size in bytes
    mime_type = Column(String, nullable=False)  # image/jpeg, image/png, etc.
    width = Column(Integer, nullable=True)  # Image width in pixels
    height = Column(Integer, nullable=True)  # Image height in pixels
    thumbnail_path = Column(String, nullable=True)  # Optional thumbnail path
    uploaded_by = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    comment = relationship('TicketComment', back_populates='images')
    uploader = relationship('User', foreign_keys=[uploaded_by])
    
    def __repr__(self):
        return f"<CommentImage(image_id={self.image_id}, comment_id={self.comment_id}, file_name='{self.file_name}')>"
    
    def to_dict(self):
        """Convert image to dictionary."""
        return {
            'image_id': self.image_id,
            'comment_id': self.comment_id,
            'file_path': self.file_path,
            'file_name': self.file_name,
            'file_size': self.file_size,
            'mime_type': self.mime_type,
            'width': self.width,
            'height': self.height,
            'thumbnail_path': self.thumbnail_path,
            'uploaded_by': self.uploaded_by,
            'uploaded_by_name': self.uploader.name if self.uploader else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ActivityLog(Base):
    """Activity log model for tracking user activities."""
    __tablename__ = 'activity_logs'
    __table_args__ = (
        {'schema': 'tickets'} if os.getenv("DATABASE_URL") else {},
    )
    
    activity_id = Column(Integer, primary_key=True, autoincrement=True)
    # Foreign key references - adjust schema prefix for PostgreSQL
    _users_fk_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    user_id = Column(Integer, ForeignKey(f'{_users_fk_schema}users.user_id'), nullable=False, index=True)
    activity_type = Column(String, nullable=False, index=True)  # 'ticket', 'auth', 'comment'
    entity_type = Column(String, nullable=False)  # 'ticket', 'user', 'comment'
    entity_id = Column(Integer, nullable=True, index=True)  # ID of the entity (ticket_id, user_id, etc.)
    action = Column(String, nullable=False, index=True)  # 'create', 'update', 'delete', 'login', 'logout', 'status_change', 'assign', etc.
    # Use JSONB for PostgreSQL, JSON for SQLite - will be set in __init_subclass__ or migration
    # Note: 'metadata' is reserved in SQLAlchemy, so we use 'activity_metadata'
    activity_metadata = Column(JSON, nullable=True)  # Flexible JSON for additional context (JSONB for PostgreSQL handled in migration)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = relationship('User', foreign_keys=[user_id])
    
    def __repr__(self):
        return f"<ActivityLog(activity_id={self.activity_id}, user_id={self.user_id}, activity_type='{self.activity_type}', action='{self.action}')>"
    
    def to_dict(self):
        """Convert activity log to dictionary."""
        return {
            'activity_id': self.activity_id,
            'user_id': self.user_id,
            'user_name': self.user.name if self.user else None,
            'user_email': self.user.email if self.user else None,
            'activity_type': self.activity_type,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'action': self.action,
            'metadata': self.activity_metadata,  # Expose as 'metadata' in API for consistency
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


def init_ticket_database():
    """
    Initialize the ticket tables in the user database.
    Supports PostgreSQL (creates schema) and SQLite.
    """
    import os
    
    # Tickets use the same database as users
    db_path = config.USERS_DATABASE_PATH
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        # SQLite: Ensure database directory exists
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    engine = get_engine(db_path)
    
    # For PostgreSQL, create schema if it doesn't exist
    if database_url:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS tickets"))
    
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        if not database_url:
            # SQLite: retry after short wait
            import time
            time.sleep(0.5)
            try:
                Base.metadata.create_all(engine)
            except Exception:
                raise e
        else:
            raise e
    
    # SQLite-specific migrations (skip for PostgreSQL)
    if not database_url:
        # Migrate tickets table if needed (add category column, make listing_id nullable)
        _migrate_tickets_table(engine)
        # Migrate image tables if needed
        _migrate_image_tables(engine)
        # Migrate recurring fields to tickets table
        _migrate_tickets_recurring_table(engine)
        # Migrate activity_logs table
        _migrate_activity_logs_table(engine)
    else:
        # PostgreSQL: Migrate to make listing_id nullable
        _migrate_listing_id_nullable(engine)
        # Migrate recurring fields to tickets table
        _migrate_tickets_recurring_table(engine)
        # Migrate activity_logs table
        _migrate_activity_logs_table(engine)
    
    return engine


def _migrate_tickets_table(engine):
    """Add category column to tickets table if it doesn't exist and set default for existing records"""
    import sqlalchemy
    with engine.connect() as conn:
        # Check if tickets table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'"
        ))
        if not result.fetchone():
            return  # Table doesn't exist, create_all will handle it
        
        # Get existing columns
        result = conn.execute(sqlalchemy.text("PRAGMA table_info(tickets)"))
        existing_columns = {row[1] for row in result.fetchall()}
        
        # Add category column if missing
        if 'category' not in existing_columns:
            try:
                # SQLite doesn't support NOT NULL with default in ALTER TABLE, so add as nullable first
                conn.execute(sqlalchemy.text("ALTER TABLE tickets ADD COLUMN category TEXT"))
                # Update all existing records to 'other'
                conn.execute(sqlalchemy.text("UPDATE tickets SET category = 'other' WHERE category IS NULL"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                # Column might already exist, ignore
                pass
        else:
            # Column exists, but ensure all NULL values are set to 'other'
            try:
                conn.execute(sqlalchemy.text("UPDATE tickets SET category = 'other' WHERE category IS NULL"))
                conn.commit()
            except Exception as e:
                conn.rollback()
                pass
        
        # Migrate 'communication' to 'online' for existing records
        try:
            conn.execute(sqlalchemy.text("UPDATE tickets SET category = 'online' WHERE category = 'communication'"))
            conn.commit()
        except Exception as e:
            conn.rollback()
            pass


def _migrate_listing_id_nullable(engine):
    """Make listing_id nullable in tickets table (PostgreSQL only)."""
    import sqlalchemy
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        return  # SQLite migration handled separately
    
    try:
        with engine.connect() as conn:
            # Check if tickets table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'tickets' AND table_name = 'tickets')"
            ))
            if not result.scalar():
                return  # Table doesn't exist, create_all will handle it
            
            # Check if listing_id is already nullable
            result = conn.execute(sqlalchemy.text("""
                SELECT is_nullable 
                FROM information_schema.columns 
                WHERE table_schema = 'tickets' 
                AND table_name = 'tickets' 
                AND column_name = 'listing_id'
            """))
            row = result.fetchone()
            if row and row[0] == 'YES':
                return  # Already nullable
            
            # Make listing_id nullable
            conn.execute(sqlalchemy.text("ALTER TABLE tickets.tickets ALTER COLUMN listing_id DROP NOT NULL"))
            conn.commit()
            logger.info("Migrated tickets.listing_id to be nullable")
    except Exception as e:
        logger.warning(f"Error migrating listing_id to nullable: {e}")
        # Migration might have already been applied, ignore


def _migrate_image_tables(engine):
    """Create image tables if they don't exist (SQLite only)."""
    import sqlalchemy
    with engine.connect() as conn:
        # Check if ticket_images table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_images'"
        ))
        if not result.fetchone():
            # Table doesn't exist, create_all will handle it
            return
        
        # Check if comment_images table exists
        result = conn.execute(sqlalchemy.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='comment_images'"
        ))
        if not result.fetchone():
            # Table doesn't exist, create_all will handle it
            return


def _migrate_tickets_recurring_table(engine):
    """Add recurring task columns to tickets table if they don't exist"""
    import sqlalchemy
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            # Check if tickets table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'"
            ))
            if not result.fetchone():
                return  # Table doesn't exist, create_all will handle it
            
            # Get existing columns
            result = conn.execute(sqlalchemy.text("PRAGMA table_info(tickets)"))
            existing_columns = {row[1] for row in result.fetchall()}
            
            # Add columns if missing (idempotent)
            columns_to_add = [
                ('is_recurring', 'INTEGER DEFAULT 0'),
                ('is_recurring_active', 'INTEGER DEFAULT 1'),
                ('frequency_value', 'INTEGER'),
                ('frequency_unit', 'TEXT'),
                ('initial_due_date', 'DATE'),
                ('recurring_admin_id', 'INTEGER'),
                ('reopen_days_before_due_date', 'INTEGER DEFAULT 10'),
            ]
            
            for col_name, col_type in columns_to_add:
                if col_name not in existing_columns:
                    try:
                        conn.execute(sqlalchemy.text(f"ALTER TABLE tickets ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        # Column might already exist, ignore
                        pass
        else:
            # PostgreSQL migration
            # Check if tickets table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'tickets' AND table_name = 'tickets')"
            ))
            if not result.scalar():
                return  # Table doesn't exist, create_all will handle it
            
            # Check and add columns
            columns_to_add = [
                ('is_recurring', 'BOOLEAN DEFAULT FALSE'),
                ('is_recurring_active', 'BOOLEAN DEFAULT TRUE'),
                ('frequency_value', 'INTEGER'),
                ('frequency_unit', 'VARCHAR'),
                ('initial_due_date', 'DATE'),
                ('recurring_admin_id', 'INTEGER'),
                ('reopen_days_before_due_date', 'INTEGER DEFAULT 10'),
            ]
            
            for col_name, col_type in columns_to_add:
                # Check if column exists
                result = conn.execute(sqlalchemy.text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_schema = 'tickets' 
                        AND table_name = 'tickets' 
                        AND column_name = :col_name
                    )
                """), {'col_name': col_name})
                if not result.scalar():
                    try:
                        conn.execute(sqlalchemy.text(f"ALTER TABLE tickets.tickets ADD COLUMN {col_name} {col_type}"))
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        # Column might already exist, ignore
            pass


def _migrate_activity_logs_table(engine):
    """Create activity_logs table if it doesn't exist."""
    import sqlalchemy
    database_url = os.getenv("DATABASE_URL")
    
    with engine.connect() as conn:
        if not database_url:
            # SQLite migration
            # Check if activity_logs table exists
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_logs'"
            ))
            if result.fetchone():
                return  # Table already exists
            
            # Table will be created by create_all, but we ensure it exists
            # create_all will handle it
            return
        else:
            # PostgreSQL migration
            # Check if activity_logs table exists
            result = conn.execute(sqlalchemy.text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'tickets' 
                    AND table_name = 'activity_logs'
                )
            """))
            if result.scalar():
                return  # Table already exists
            
            # Create table with JSONB for activity_metadata (metadata is reserved in SQLAlchemy)
            from sqlalchemy.dialects.postgresql import JSONB
            conn.execute(sqlalchemy.text("""
                CREATE TABLE tickets.activity_logs (
                    activity_id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users.users(user_id),
                    activity_type VARCHAR NOT NULL,
                    entity_type VARCHAR NOT NULL,
                    entity_id INTEGER,
                    action VARCHAR NOT NULL,
                    activity_metadata JSONB,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            
            # Create indexes
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_user_id ON tickets.activity_logs(user_id)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_activity_type ON tickets.activity_logs(activity_type)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_entity_id ON tickets.activity_logs(entity_id)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_action ON tickets.activity_logs(action)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_created_at ON tickets.activity_logs(created_at)"))
            conn.execute(sqlalchemy.text("CREATE INDEX idx_activity_logs_type_created ON tickets.activity_logs(activity_type, created_at)"))
            
            conn.commit()
            logger.info("Created activity_logs table with indexes")


def _safe_expunge(session, obj):
    """Safely expunge an object from the session if it's present."""
    if obj is None:
        return
    try:
        # Check if object is in session before expunging
        if obj in session:
            session.expunge(obj)
    except Exception:
        # If expunge fails (object not in session), just ignore
        pass




def create_ticket(listing_id: int = None, issue_title: str = None, title: str = None, description: str = None,
                  assigned_user_id: int = None, status: str = 'Open', priority: str = 'Low',
                  category: str = 'other', due_date: date = None, created_by: int = None,
                  is_recurring: bool = False, frequency_value: int = None, frequency_unit: str = None,
                  initial_due_date: date = None, recurring_admin_id: int = None,
                  reopen_days_before_due_date: int = None) -> Ticket:
    """Create a new ticket and inherit tags from the property (if listing_id is provided).
    
    For general tickets (listing_id=None), issue_title can be None and will default to title.
    """
    # For general tickets, use title as issue_title if not provided
    if listing_id is None and not issue_title and title:
        issue_title = title
    
    # Ensure issue_title is not None
    if not issue_title:
        raise ValueError("issue_title is required")
    
    from sqlalchemy.orm import joinedload
    import sys
    import os
    # Import main database models for tag inheritance
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, project_root)
    from database.models import get_session as get_main_session, ListingTag
    
    session = get_session()
    main_session = get_main_session(config.MAIN_DATABASE_PATH)
    
    try:
        ticket = Ticket(
            listing_id=listing_id,
            issue_title=issue_title,
            title=title,
            description=description,
            assigned_user_id=assigned_user_id,
            status=status,
            priority=priority,
            category=category,
            due_date=due_date,
            created_by=created_by,
            is_recurring=is_recurring,
            is_recurring_active=is_recurring if is_recurring else False,
            frequency_value=frequency_value,
            frequency_unit=frequency_unit,
            initial_due_date=initial_due_date,
            recurring_admin_id=recurring_admin_id,
            reopen_days_before_due_date=reopen_days_before_due_date if reopen_days_before_due_date is not None else 10
        )
        session.add(ticket)
        session.flush()  # Get ticket_id without committing
        ticket_id = ticket.ticket_id
        
        # Inherit tags from property (only if listing_id is provided)
        if listing_id is not None:
            listing_tags = main_session.query(ListingTag).filter(
                ListingTag.listing_id == listing_id
            ).all()
            
            for listing_tag in listing_tags:
                ticket_tag = TicketTag(
                    ticket_id=ticket_id,
                    tag_id=listing_tag.tag_id,
                    is_inherited=True
                )
                session.add(ticket_tag)
        
        session.commit()
        main_session.close()
        
        # Re-query with eager loading to get relationships
        ticket = session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator)
        ).filter(Ticket.ticket_id == ticket_id).first()
        
        if ticket:
            # Access relationships while session is open to populate them
            _ = ticket.assigned_user
            _ = ticket.creator
            
            # Expunge to detach from session but keep loaded relationships
            session.expunge(ticket)
            _safe_expunge(session, ticket.assigned_user)
            _safe_expunge(session, ticket.creator)
        
        return ticket
    except Exception as e:
        session.rollback()
        if 'main_session' in locals():
            main_session.rollback()
        raise e
    finally:
        session.close()
        if 'main_session' in locals():
            main_session.close()


def get_ticket(ticket_id: int) -> Optional[Ticket]:
    """Get a ticket by ID with relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        ticket = session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator),
            joinedload(Ticket.images)
        ).filter(Ticket.ticket_id == ticket_id).first()
        
        if ticket:
            # Access relationships while session is open to populate them
            _ = ticket.assigned_user
            _ = ticket.creator
            _ = ticket.images
            
            # Expunge to detach from session but keep loaded relationships
            session.expunge(ticket)
            _safe_expunge(session, ticket.assigned_user)
            _safe_expunge(session, ticket.creator)
            for img in ticket.images:
                _safe_expunge(session, img)
        
        return ticket
    finally:
        session.close()


def get_tickets(listing_id: int = None, assigned_user_id: int = None,
                status: str = None, priority: str = None, category: str = None, issue_title: str = None) -> List[Ticket]:
    """Get tickets with optional filters and relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    from sqlalchemy import func
    session = get_session()
    try:
        query = session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator)
        )
        
        if listing_id:
            query = query.filter(Ticket.listing_id == listing_id)
        if assigned_user_id:
            query = query.filter(Ticket.assigned_user_id == assigned_user_id)
        if status:
            query = query.filter(Ticket.status == status)
        if priority:
            query = query.filter(Ticket.priority == priority)
        if category:
            query = query.filter(Ticket.category == category)
        tickets = query.order_by(Ticket.created_at.desc()).all()
        
        # Filter by issue_title in Python if provided (more reliable for whitespace handling)
        if issue_title:
            issue_title_normalized = issue_title.strip().lower()
            # Filter tickets where issue_title meaningfully matches (case-insensitive, after trimming)
            filtered_tickets = []
            for t in tickets:
                if t.issue_title:
                    ticket_issue_normalized = t.issue_title.strip().lower()
                    # Case-insensitive exact match
                    if ticket_issue_normalized == issue_title_normalized:
                        filtered_tickets.append(t)
                    # Or if one is a significant substring of the other (at least 80% length match)
                    elif (len(ticket_issue_normalized) > 0 and len(issue_title_normalized) > 0):
                        shorter = min(ticket_issue_normalized, issue_title_normalized, key=len)
                        longer = max(ticket_issue_normalized, issue_title_normalized, key=len)
                        # Only match if shorter is at least 80% of longer and is contained
                        if len(shorter) >= len(longer) * 0.8 and shorter in longer:
                            filtered_tickets.append(t)
            tickets = filtered_tickets
        
        # Access relationships while session is open and expunge to detach
        for ticket in tickets:
            _ = ticket.assigned_user
            _ = ticket.creator
            session.expunge(ticket)
            _safe_expunge(session, ticket.assigned_user)
            _safe_expunge(session, ticket.creator)
        
        return tickets
    finally:
        session.close()


def update_ticket(ticket_id: int, **kwargs) -> Optional[Ticket]:
    """Update a ticket with relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        ticket = session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator),
            joinedload(Ticket.images).joinedload(TicketImage.uploader)
        ).filter(Ticket.ticket_id == ticket_id).first()
        
        if not ticket:
            return None
        
        for key, value in kwargs.items():
            if hasattr(ticket, key):
                # Allow setting None values explicitly
                setattr(ticket, key, value)
        
        ticket.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(ticket)
        
        # Re-query with eager loading to get fresh relationships
        ticket = session.query(Ticket).options(
            joinedload(Ticket.assigned_user),
            joinedload(Ticket.creator),
            joinedload(Ticket.images).joinedload(TicketImage.uploader)
        ).filter(Ticket.ticket_id == ticket_id).first()
        
        if ticket:
            # Access relationships while session is open to populate them
            _ = ticket.assigned_user
            _ = ticket.creator
            _ = ticket.images  # Access images while session is open
            # Access uploader for each image while session is open
            for img in ticket.images:
                _ = img.uploader
            
            # Expunge to detach from session but keep loaded relationships
            session.expunge(ticket)
            _safe_expunge(session, ticket.assigned_user)
            _safe_expunge(session, ticket.creator)
            for img in ticket.images:
                _safe_expunge(session, img.uploader)
                _safe_expunge(session, img)
        
        return ticket
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def delete_ticket(ticket_id: int) -> bool:
    """Delete a ticket."""
    session = get_session()
    try:
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if ticket:
            session.delete(ticket)
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def add_ticket_comment(ticket_id: int, user_id: int, comment_text: str) -> TicketComment:
    """Add a comment to a ticket with relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        comment = TicketComment(
            ticket_id=ticket_id,
            user_id=user_id,
            comment_text=comment_text
        )
        session.add(comment)
        
        # Update ticket's updated_at timestamp
        ticket = session.query(Ticket).filter(Ticket.ticket_id == ticket_id).first()
        if ticket:
            ticket.updated_at = datetime.utcnow()
        
        session.commit()
        comment_id = comment.comment_id
        
        # Re-query with eager loading to get relationships
        comment = session.query(TicketComment).options(
            joinedload(TicketComment.user),
            joinedload(TicketComment.images).joinedload(CommentImage.uploader)
        ).filter(TicketComment.comment_id == comment_id).first()
        
        if comment:
            # Access relationships while session is open to populate them
            _ = comment.user
            _ = comment.images  # Access images while session is open
            # Access uploader for each image while session is open
            for img in comment.images:
                _ = img.uploader
            
            # Expunge to detach from session but keep loaded relationships
            session.expunge(comment)
            _safe_expunge(session, comment.user)
            for img in comment.images:
                _safe_expunge(session, img.uploader)
                _safe_expunge(session, img)
        
        return comment
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def get_ticket_comments(ticket_id: int) -> List[TicketComment]:
    """Get all comments for a ticket with relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
        comments = session.query(TicketComment).options(
            joinedload(TicketComment.user),
            joinedload(TicketComment.images).joinedload(CommentImage.uploader)
        ).filter(
            TicketComment.ticket_id == ticket_id
        ).order_by(TicketComment.created_at.asc()).all()
        
        # Access relationships while session is open and expunge to detach
        for comment in comments:
            _ = comment.user
            _ = comment.images
            # Access uploader for each image while session is open
            for img in comment.images:
                _ = img.uploader
            session.expunge(comment)
            _safe_expunge(session, comment.user)
            for img in comment.images:
                _safe_expunge(session, img.uploader)
                _safe_expunge(session, img)
        
        return comments
    finally:
        session.close()


def delete_ticket_comment(comment_id: int) -> bool:
    """Delete a ticket comment."""
    from pathlib import Path
    from dashboard.config import TICKET_IMAGES_DIR
    import os
    
    session = get_session()
    try:
        # Load comment with images
        from sqlalchemy.orm import joinedload
        comment = session.query(TicketComment).options(
            joinedload(TicketComment.images)
        ).filter(TicketComment.comment_id == comment_id).first()
        
        if comment:
            # Delete associated image files
            for img in comment.images:
                if img.file_path:
                    file_path = Path(TICKET_IMAGES_DIR) / img.file_path
                    if file_path.exists():
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass
                if img.thumbnail_path:
                    thumb_path = Path(TICKET_IMAGES_DIR) / img.thumbnail_path
                    if thumb_path.exists():
                        try:
                            os.remove(thumb_path)
                        except Exception:
                            pass
            
            # Delete the comment (cascade will handle CommentImage records)
            session.delete(comment)
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()
