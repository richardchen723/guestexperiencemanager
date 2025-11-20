#!/usr/bin/env python3
"""
Ticket database models and utilities.
"""

import sys
import os
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Date
from sqlalchemy.orm import relationship
from pathlib import Path

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
    
    ticket_id = Column(Integer, primary_key=True, autoincrement=True)
    listing_id = Column(Integer, nullable=False, index=True)  # References main database listings
    issue_title = Column(String, nullable=False)  # The issue title this ticket addresses
    title = Column(String, nullable=False)
    description = Column(Text)
    assigned_user_id = Column(Integer, ForeignKey('users.user_id'), nullable=True, index=True)
    status = Column(String, nullable=False, default='Open', index=True)
    priority = Column(String, default='Low')
    category = Column(String, nullable=False, default='other', index=True)
    due_date = Column(Date, nullable=True)
    created_by = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    assigned_user = relationship('User', foreign_keys=[assigned_user_id])
    creator = relationship('User', foreign_keys=[created_by])
    comments = relationship('TicketComment', back_populates='ticket', cascade='all, delete-orphan', order_by='TicketComment.created_at')
    
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
        }
        
        if include_comments:
            result['comments'] = [comment.to_dict() for comment in self.comments]
        
        return result


class TicketComment(Base):
    """Comment model for ticket discussions."""
    __tablename__ = 'ticket_comments'
    
    comment_id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey('tickets.ticket_id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.user_id'), nullable=False)
    comment_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    ticket = relationship('Ticket', back_populates='comments')
    user = relationship('User', foreign_keys=[user_id])
    
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
        
        return result


def init_ticket_database():
    """Initialize the ticket tables in the user database."""
    # Tickets use the same database as users
    db_path_or_url = config.USERS_DATABASE_PATH
    # Only create directory for SQLite (file-based)
    if not (db_path_or_url.startswith('postgresql://') or db_path_or_url.startswith('postgres://')):
        db_dir = Path(db_path_or_url).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    engine = get_engine(db_path_or_url)
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        import time
        time.sleep(0.5)
        try:
            Base.metadata.create_all(engine)
        except Exception:
            raise e
    
    # Migrate tickets table if needed (add category column)
    _migrate_tickets_table(engine)
    
    return engine


def _migrate_tickets_table(engine):
    """Add category column to tickets table if it doesn't exist and set default for existing records"""
    import sqlalchemy
    # Detect database type from engine URL
    db_url = str(engine.url)
    is_postgresql = db_url.startswith('postgresql://') or db_url.startswith('postgres://')
    
    with engine.connect() as conn:
        # Check if tickets table exists
        if is_postgresql:
            result = conn.execute(sqlalchemy.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'tickets'"
            ))
        else:
            result = conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'"
            ))
        
        if not result.fetchone():
            return  # Table doesn't exist, create_all will handle it
        
        # Get existing columns
        if is_postgresql:
            result = conn.execute(sqlalchemy.text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'tickets'"
            ))
            existing_columns = {row[0] for row in result.fetchall()}
        else:
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




def create_ticket(listing_id: int, issue_title: str, title: str, description: str = None,
                  assigned_user_id: int = None, status: str = 'Open', priority: str = 'Low',
                  category: str = 'other', due_date: date = None, created_by: int = None) -> Ticket:
    """Create a new ticket."""
    from sqlalchemy.orm import joinedload
    session = get_session()
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
            created_by=created_by
        )
        session.add(ticket)
        session.commit()
        ticket_id = ticket.ticket_id
        
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
        raise e
    finally:
        session.close()


def get_ticket(ticket_id: int) -> Optional[Ticket]:
    """Get a ticket by ID with relationships eagerly loaded."""
    from sqlalchemy.orm import joinedload
    session = get_session()
    try:
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
            joinedload(Ticket.creator)
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
            joinedload(TicketComment.user)
        ).filter(TicketComment.comment_id == comment_id).first()
        
        if comment:
            # Access relationship while session is open to populate it
            _ = comment.user
            
            # Expunge to detach from session but keep loaded relationship
            session.expunge(comment)
            _safe_expunge(session, comment.user)
        
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
            joinedload(TicketComment.user)
        ).filter(
            TicketComment.ticket_id == ticket_id
        ).order_by(TicketComment.created_at.asc()).all()
        
        # Access relationships while session is open and expunge to detach
        for comment in comments:
            _ = comment.user
            session.expunge(comment)
            _safe_expunge(session, comment.user)
        
        return comments
    finally:
        session.close()
