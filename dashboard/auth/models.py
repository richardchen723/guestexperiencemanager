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
    Supports PostgreSQL (via DATABASE_URL) with SQLite fallback.
    """
    import os
    
    # Check for PostgreSQL connection string
    database_url = os.getenv("DATABASE_URL")
    
    if database_url:
        # PostgreSQL connection - use 'users' schema
        # Modify connection string to include schema search path
        if '?' in database_url:
            database_url += "&options=-csearch_path%3Dusers,public"
        else:
            database_url += "?options=-csearch_path%3Dusers,public"
        
        # For serverless (Vercel), use NullPool to avoid connection exhaustion
        # For local development, use connection pooling
        is_vercel = os.getenv("VERCEL") == "1"
        
        if is_vercel:
            from sqlalchemy.pool import NullPool
            # NullPool doesn't support pool_size, max_overflow, or pool_timeout
            engine = create_engine(
                database_url,
                echo=False,
                poolclass=NullPool,
                pool_pre_ping=True,
                connect_args={
                    "connect_timeout": 15,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 5
                }
            )
        else:
            # Local development: use connection pooling
            engine = create_engine(
                database_url,
                echo=False,
                pool_size=5,
                max_overflow=2,
                pool_timeout=30,
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
        engine = create_engine(
            f'sqlite:///{db_path}',
            echo=False,
            connect_args={
                'check_same_thread': False,
                'timeout': 30.0
            },
            pool_pre_ping=True
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
