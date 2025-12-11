#!/usr/bin/env python3
"""
User database models and utilities.
"""

import sys
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean, text
import sqlalchemy
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from pathlib import Path

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

import dashboard.config as config

Base = declarative_base()


class User(Base):
    """User model for authentication and authorization."""
    __tablename__ = 'users'
    __table_args__ = (
        {'schema': 'users'} if os.getenv("DATABASE_URL") else {},
    )
    
    user_id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    picture_url = Column(String)
    role = Column(String, nullable=False, default='user')  # 'owner', 'admin', 'user'
    is_approved = Column(Boolean, default=False, nullable=False)
    google_id = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at = Column(DateTime)
    # Self-referential foreign key - adjust schema prefix for PostgreSQL
    _users_schema = 'users.' if os.getenv("DATABASE_URL") else ''
    approved_by = Column(Integer, ForeignKey(f'{_users_schema}users.user_id'), nullable=True)
    last_login = Column(DateTime)
    whatsapp_number = Column(String, nullable=True)  # E.164 format (e.g., +14155552671)
    whatsapp_notifications_enabled = Column(Boolean, default=True, nullable=False)
    
    # Relationship to approver
    approver = relationship('User', remote_side=[user_id], foreign_keys=[approved_by])
    
    def __repr__(self):
        return f"<User(email='{self.email}', role='{self.role}', approved={self.is_approved})>"
    
    def is_admin(self):
        """Check if user has admin privileges (admin or owner)."""
        return self.role in ('admin', 'owner')
    
    def is_owner(self):
        """Check if user is the owner."""
        return self.role == 'owner'


def get_engine(db_path: str):
    """
    Create SQLAlchemy engine for user database.
    PostgreSQL is required - no SQLite fallback.
    
    Args:
        db_path: Ignored for PostgreSQL (kept for interface compatibility)
    """
    import os
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is required. "
            "PostgreSQL is required for this application. "
            "Example: postgresql://user@localhost:5432/hostaway_dev"
        )
    
    # PostgreSQL connection - use 'users' schema
    # Modify connection string to include schema search path
    if '?' in database_url:
        database_url += "&options=-csearch_path%3Dusers,public"
    else:
        database_url += "?options=-csearch_path%3Dusers,public"
    
    engine = create_engine(
        database_url,
        echo=False,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 15,  # Increased from 10 to handle slower connections
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }
    )
    return engine


def init_user_database():
    """
    Initialize the user database and create tables.
    Supports PostgreSQL (creates schema) and SQLite.
    """
    import os
    
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
            conn.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS users"))
    
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
    return engine


def get_session():
    """Get a database session for user operations."""
    engine = get_engine(config.USERS_DATABASE_PATH)
    Session = sessionmaker(bind=engine)
    session = Session()
    return session


def get_user_by_email(email: str):
    """Get user by email address."""
    session = get_session()
    try:
        return session.query(User).filter(User.email == email).first()
    finally:
        session.close()


def get_user_by_google_id(google_id: str):
    """Get user by Google ID."""
    session = get_session()
    try:
        return session.query(User).filter(User.google_id == google_id).first()
    finally:
        session.close()


def get_user_by_id(user_id: int):
    """Get user by user ID."""
    session = get_session()
    try:
        return session.query(User).filter(User.user_id == user_id).first()
    finally:
        session.close()


def create_user(email: str, name: str = None, picture_url: str = None, 
                google_id: str = None, role: str = 'user', is_approved: bool = False):
    """Create a new user."""
    session = get_session()
    try:
        user = User(
            email=email,
            name=name,
            picture_url=picture_url,
            google_id=google_id,
            role=role,
            is_approved=is_approved
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def approve_user(user_id: int, approved_by_user_id: int):
    """Approve a user account."""
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            # Owner accounts are always approved, cannot be modified
            if user.role == 'owner':
                return user
            user.is_approved = True
            user.approved_at = datetime.utcnow()
            user.approved_by = approved_by_user_id
            session.commit()
            return user
        return None
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def revoke_user(user_id: int):
    """Revoke user access (unapprove)."""
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            # Owner accounts cannot be revoked
            if user.role == 'owner':
                raise ValueError("Cannot revoke owner account")
            user.is_approved = False
            user.approved_at = None
            user.approved_by = None
            session.commit()
            return user
        return None
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def update_user_role(user_id: int, new_role: str):
    """Update user role."""
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            # Owner role cannot be changed
            if user.role == 'owner':
                raise ValueError("Cannot change owner role")
            # Only owner email can have owner role (enforced at OAuth level)
            if new_role == 'owner':
                raise ValueError("Cannot assign owner role")
            user.role = new_role
            session.commit()
            return user
        return None
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def update_last_login(user_id: int):
    """Update user's last login timestamp."""
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user:
            user.last_login = datetime.utcnow()
            session.commit()
            return user
        return None
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def get_all_users():
    """Get all users."""
    session = get_session()
    try:
        return session.query(User).order_by(User.created_at.desc()).all()
    finally:
        session.close()


def delete_user(user_id: int):
    """Delete a user (cannot delete owner)."""
    session = get_session()
    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user and user.role != 'owner':
            session.delete(user)
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def _migrate_user_whatsapp_fields(engine):
    """Add WhatsApp-related columns to users table if they don't exist (idempotent)."""
    import sqlalchemy
    import logging
    logger = logging.getLogger(__name__)
    database_url = os.getenv("DATABASE_URL")
    
    try:
        with engine.connect() as conn:
            if not database_url:
                # SQLite migration
                # Check if users table exists
                result = conn.execute(sqlalchemy.text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
                ))
                if not result.fetchone():
                    return  # Table doesn't exist, create_all will handle it
                
                # Get existing columns
                result = conn.execute(sqlalchemy.text("PRAGMA table_info(users)"))
                existing_columns = {row[1] for row in result.fetchall()}
                
                # Add whatsapp_number column if missing
                if 'whatsapp_number' not in existing_columns:
                    try:
                        conn.execute(sqlalchemy.text("ALTER TABLE users ADD COLUMN whatsapp_number TEXT"))
                        conn.commit()
                        logger.info("Added whatsapp_number column to users table")
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f"Error adding whatsapp_number column: {e}")
                
                # Add whatsapp_notifications_enabled column if missing
                if 'whatsapp_notifications_enabled' not in existing_columns:
                    try:
                        conn.execute(sqlalchemy.text("ALTER TABLE users ADD COLUMN whatsapp_notifications_enabled INTEGER DEFAULT 1"))
                        # Update existing records to have notifications enabled by default
                        conn.execute(sqlalchemy.text("UPDATE users SET whatsapp_notifications_enabled = 1 WHERE whatsapp_notifications_enabled IS NULL"))
                        conn.commit()
                        logger.info("Added whatsapp_notifications_enabled column to users table")
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f"Error adding whatsapp_notifications_enabled column: {e}")
            else:
                # PostgreSQL migration
                # Check if users table exists
                result = conn.execute(sqlalchemy.text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'users' AND table_name = 'users')"
                ))
                if not result.scalar():
                    return  # Table doesn't exist, create_all will handle it
                
                # Check if whatsapp_number column exists
                result = conn.execute(sqlalchemy.text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'users' 
                    AND table_name = 'users' 
                    AND column_name = 'whatsapp_number'
                """))
                if not result.fetchone():
                    try:
                        conn.execute(sqlalchemy.text("ALTER TABLE users.users ADD COLUMN whatsapp_number VARCHAR"))
                        conn.commit()
                        logger.info("Added whatsapp_number column to users.users table")
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f"Error adding whatsapp_number column: {e}")
                
                # Check if whatsapp_notifications_enabled column exists
                result = conn.execute(sqlalchemy.text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'users' 
                    AND table_name = 'users' 
                    AND column_name = 'whatsapp_notifications_enabled'
                """))
                if not result.fetchone():
                    try:
                        conn.execute(sqlalchemy.text("ALTER TABLE users.users ADD COLUMN whatsapp_notifications_enabled BOOLEAN DEFAULT TRUE NOT NULL"))
                        # Update existing records to have notifications enabled by default
                        conn.execute(sqlalchemy.text("UPDATE users.users SET whatsapp_notifications_enabled = TRUE WHERE whatsapp_notifications_enabled IS NULL"))
                        conn.commit()
                        logger.info("Added whatsapp_notifications_enabled column to users.users table")
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f"Error adding whatsapp_notifications_enabled column: {e}")
    except Exception as e:
        logger.warning(f"Error in _migrate_user_whatsapp_fields: {e}")
        # Migration might have already been applied, ignore
