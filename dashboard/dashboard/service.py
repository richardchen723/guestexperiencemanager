#!/usr/bin/env python3
"""
Dashboard service for fetching and calculating dashboard data.
"""

import sys
import os
import logging
from datetime import date, timedelta, datetime, timezone
from typing import Dict, List, Optional
from sqlalchemy import func, case
from sqlalchemy.orm import joinedload

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import (
    Ticket, TicketListing, TicketTag, get_session, TICKET_STATUSES, TICKET_PRIORITIES, STANDARD_TICKET_TYPE
)
from database.models import Reservation, Listing, SyncLog, get_session as get_main_session
from dashboard.dashboard.occupancy import build_occupancy, month_start
import dashboard.config as config

logger = logging.getLogger(__name__)

# Priority order for sorting (higher index = higher priority)
PRIORITY_ORDER = {priority: idx for idx, priority in enumerate(TICKET_PRIORITIES)}
PRIORITY_ORDER_REVERSE = {idx: priority for priority, idx in PRIORITY_ORDER.items()}


class DashboardService:
    """Service for fetching and calculating dashboard data."""
    
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.ticket_session = get_session()
        self.main_session = get_main_session(config.MAIN_DATABASE_PATH)
    
    def get_dashboard_data(self, 
                          ticket_limit: int = 50,
                          occupancy_months: int = 12) -> Dict:
        """
        Get all dashboard data in a single call.
        
        Args:
            ticket_limit: Maximum number of tickets to return (default: 50, max: 50)
            occupancy_months: Legacy argument; the chart always returns 12 rolling months.
        
        Returns:
            {
                'tickets': [...],
                'statistics': {...},
                'occupancy': [...]
            }
        """
        try:
            # Validate limits
            ticket_limit = min(max(1, ticket_limit), 50)
            
            tickets = self._get_my_tickets(limit=ticket_limit)
            statistics = self._calculate_statistics()
            occupancy = self._calculate_occupancy()
            
            return {
                'tickets': tickets,
                'statistics': statistics,
                'occupancy': occupancy,
                'occupancy_metadata': self.occupancy_metadata
            }
        except Exception as e:
            logger.error(f"Error in get_dashboard_data for user {self.user_id}: {e}", exc_info=True)
            raise
        finally:
            self._close_sessions()
    
    def _get_my_tickets(self, limit: int = 50) -> List[Dict]:
        """Get top priority tickets assigned to the user."""
        try:
            # Get tickets assigned to user, excluding resolved/closed
            active_statuses = [s for s in TICKET_STATUSES if s not in ['Resolved', 'Closed']]
            
            tickets = self.ticket_session.query(Ticket).options(
                joinedload(Ticket.assigned_user),
                joinedload(Ticket.creator),
                joinedload(Ticket.tags)
            ).filter(
                Ticket.assigned_user_id == self.user_id,
                Ticket.ticket_type == STANDARD_TICKET_TYPE,
                Ticket.status.in_(active_statuses)
            ).all()
            
            # Sort by priority (Critical > High > Medium > Low), then due_date, then created_at
            def sort_key(ticket):
                priority_idx = PRIORITY_ORDER.get(ticket.priority, len(PRIORITY_ORDER))
                due_date = ticket.due_date or date.max
                created_at = ticket.created_at or datetime.min
                return (-priority_idx, due_date, -created_at.timestamp())
            
            tickets = sorted(tickets, key=sort_key)[:limit]
            
            ticket_ids = [t.ticket_id for t in tickets]
            ticket_listing_ids = {}
            if ticket_ids:
                ticket_listings = self.ticket_session.query(TicketListing).filter(
                    TicketListing.ticket_id.in_(ticket_ids)
                ).all()
                for association in ticket_listings:
                    ticket_listing_ids.setdefault(association.ticket_id, []).append(association.listing_id)

            # Load every linked property, including legacy tickets that only use listing_id.
            listing_ids = {
                listing_id
                for linked_ids in ticket_listing_ids.values()
                for listing_id in linked_ids
            }
            listing_ids.update(t.listing_id for t in tickets if t.listing_id)
            listing_map = {}
            if listing_ids:
                listings = self.main_session.query(Listing).filter(
                    Listing.listing_id.in_(listing_ids)
                ).all()
                listing_map = {l.listing_id: l for l in listings}
            
            # Get tags for tickets
            ticket_tags_map = {}
            if ticket_ids:
                from database.models import Tag
                ticket_tags = self.ticket_session.query(TicketTag).filter(
                    TicketTag.ticket_id.in_(ticket_ids)
                ).all()
                tag_ids = list(set([tt.tag_id for tt in ticket_tags]))
                if tag_ids:
                    tags = self.main_session.query(Tag).filter(Tag.tag_id.in_(tag_ids)).all()
                    tag_map = {t.tag_id: {'tag_id': t.tag_id, 'name': t.name, 'color': t.color} for t in tags}
                    
                    for tt in ticket_tags:
                        if tt.ticket_id not in ticket_tags_map:
                            ticket_tags_map[tt.ticket_id] = []
                        if tt.tag_id in tag_map:
                            ticket_tags_map[tt.ticket_id].append({
                                **tag_map[tt.tag_id],
                                'is_inherited': tt.is_inherited
                            })
            
            # Convert to dicts
            result = []
            for ticket in tickets:
                ticket_dict = ticket.to_dict(include_comments=False)
                linked_listing_ids = ticket_listing_ids.get(ticket.ticket_id, [])
                if not linked_listing_ids and ticket.listing_id:
                    linked_listing_ids = [ticket.listing_id]
                ticket_dict['listings'] = [
                    {
                        'listing_id': listing.listing_id,
                        'name': listing.name,
                        'internal_listing_name': listing.internal_listing_name,
                        'address': listing.address,
                        'city': listing.city
                    }
                    for listing_id in linked_listing_ids
                    if (listing := listing_map.get(listing_id)) is not None
                ]
                if ticket_dict['listings']:
                    ticket_dict['listing'] = ticket_dict['listings'][0]
                ticket_dict['tags'] = ticket_tags_map.get(ticket.ticket_id, [])
                result.append(ticket_dict)
            
            return result
        except Exception as e:
            logger.error(f"Error in _get_my_tickets for user {self.user_id}: {e}", exc_info=True)
            return []
    
    def _calculate_statistics(self) -> Dict:
        """Calculate ticket statistics for the user."""
        try:
            today = date.today()
            week_end = today + timedelta(days=7)
            month_start = today.replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            
            # Active statuses (not resolved/closed)
            active_statuses = [s for s in TICKET_STATUSES if s not in ['Resolved', 'Closed']]
            high_priority = ['Critical', 'High']
            
            # Base query for user's tickets
            base_query = self.ticket_session.query(Ticket).filter(
                Ticket.assigned_user_id == self.user_id,
                Ticket.ticket_type == STANDARD_TICKET_TYPE,
            )
            
            # Total assigned (active tickets)
            total_assigned = base_query.filter(
                Ticket.status.in_(active_statuses)
            ).count()
            
            # Overdue tickets
            overdue_count = base_query.filter(
                Ticket.status.in_(active_statuses),
                Ticket.due_date.isnot(None),
                Ticket.due_date < today
            ).count()
            
            # Due this week
            due_this_week = base_query.filter(
                Ticket.status.in_(active_statuses),
                Ticket.due_date.isnot(None),
                Ticket.due_date >= today,
                Ticket.due_date <= week_end
            ).count()
            
            # High priority tickets
            high_priority_count = base_query.filter(
                Ticket.status.in_(active_statuses),
                Ticket.priority.in_(high_priority)
            ).count()
            
            # Closed this week
            closed_this_week = base_query.filter(
                Ticket.status.in_(['Resolved', 'Closed']),
                Ticket.updated_at >= datetime.combine(today - timedelta(days=7), datetime.min.time())
            ).count()
            
            # Closed this month
            closed_this_month = base_query.filter(
                Ticket.status.in_(['Resolved', 'Closed']),
                Ticket.updated_at >= datetime.combine(month_start, datetime.min.time()),
                Ticket.updated_at <= datetime.combine(month_end, datetime.max.time())
            ).count()
            
            return {
                'total_assigned': total_assigned,
                'overdue_count': overdue_count,
                'due_this_week': due_this_week,
                'high_priority_count': high_priority_count,
                'closed_this_week': closed_this_week,
                'closed_this_month': closed_this_month
            }
        except Exception as e:
            logger.error(f"Error in _calculate_statistics for user {self.user_id}: {e}", exc_info=True)
            return {
                'total_assigned': 0,
                'overdue_count': 0,
                'due_this_week': 0,
                'high_priority_count': 0,
                'closed_this_week': 0,
                'closed_this_month': 0
            }
    
    def _calculate_occupancy(self, months: int = 12) -> List[Dict]:
        """Fixed six completed months + current month and next five (UTC anchor).

        ORM reads preserve the application's operational archive scope. Stay
        dates are already property-local dates and must not be timezone-shifted.
        The legacy months argument is retained for cached callers.
        """
        now = datetime.now(timezone.utc)
        today = now.date()
        try:
            listings = self.main_session.query(Listing).all()
            reservations = self.main_session.query(Reservation).filter(
                Reservation.arrival_date < month_start(today, 6),
                Reservation.departure_date > month_start(today, -6),
            ).all()
            result = build_occupancy(listings, reservations, today=today)
        except Exception:
            logger.exception("Could not read dashboard occupancy for user %s", self.user_id)
            self.main_session.rollback()
            result = build_occupancy([], [], today=today, source_available=False)
        result['metadata']['calculated_at'] = now.isoformat()
        # Sync completion is provenance, not proof of complete historical coverage.
        result['metadata']['syncs'] = {}
        for source in ('listings', 'reservations'):
            try:
                latest = self.main_session.query(SyncLog).filter(
                    SyncLog.sync_type == source,
                    SyncLog.status == 'success',
                    SyncLog.completed_at.isnot(None),
                ).order_by(SyncLog.completed_at.desc()).first()
                if latest:
                    stamp = latest.completed_at
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    result['metadata']['syncs'][source] = {
                        'completed_at': stamp.astimezone(timezone.utc).isoformat(),
                        'mode': latest.sync_mode,
                    }
            except Exception:
                logger.warning("Occupancy sync history unavailable: %s", source, exc_info=True)
                self.main_session.rollback()
        self.occupancy_metadata = result['metadata']
        return result['months']

    def _close_sessions(self):
        """Close database sessions."""
        try:
            if self.ticket_session:
                self.ticket_session.close()
        except Exception as e:
            logger.error(f"Error closing ticket session: {e}")
        
        try:
            if self.main_session:
                self.main_session.close()
        except Exception as e:
            logger.error(f"Error closing main session: {e}")
