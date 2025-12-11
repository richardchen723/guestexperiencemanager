#!/usr/bin/env python3
"""
Dashboard service for fetching and calculating dashboard data.
"""

import sys
import os
import logging
from datetime import date, timedelta, datetime
from typing import Dict, List, Optional
from calendar import monthrange
from sqlalchemy import func, case
from sqlalchemy.orm import joinedload

# Add parent directories to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import (
    Ticket, TicketTag, get_session, TICKET_STATUSES, TICKET_PRIORITIES
)
from database.models import Reservation, Listing, get_session as get_main_session
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
                          ticket_limit: int = 10,
                          occupancy_months: int = 6) -> Dict:
        """
        Get all dashboard data in a single call.
        
        Args:
            ticket_limit: Maximum number of tickets to return (default: 10, max: 50)
            occupancy_months: Number of months for occupancy calculation (default: 6, max: 12)
        
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
            occupancy_months = min(max(1, occupancy_months), 12)
            
            tickets = self._get_my_tickets(limit=ticket_limit)
            statistics = self._calculate_statistics()
            occupancy = self._calculate_occupancy(months=occupancy_months)
            
            return {
                'tickets': tickets,
                'statistics': statistics,
                'occupancy': occupancy
            }
        except Exception as e:
            logger.error(f"Error in get_dashboard_data for user {self.user_id}: {e}", exc_info=True)
            raise
        finally:
            self._close_sessions()
    
    def _get_my_tickets(self, limit: int = 10) -> List[Dict]:
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
                Ticket.status.in_(active_statuses)
            ).all()
            
            # Sort by priority (Critical > High > Medium > Low), then due_date, then created_at
            def sort_key(ticket):
                priority_idx = PRIORITY_ORDER.get(ticket.priority, len(PRIORITY_ORDER))
                due_date = ticket.due_date or date.max
                created_at = ticket.created_at or datetime.min
                return (-priority_idx, due_date, -created_at.timestamp())
            
            tickets = sorted(tickets, key=sort_key)[:limit]
            
            # Get listing info for tickets with listing_id
            listing_ids = [t.listing_id for t in tickets if t.listing_id]
            listing_map = {}
            if listing_ids:
                listings = self.main_session.query(Listing).filter(
                    Listing.listing_id.in_(listing_ids)
                ).all()
                listing_map = {l.listing_id: l for l in listings}
            
            # Get tags for tickets
            ticket_ids = [t.ticket_id for t in tickets]
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
                if ticket.listing_id and ticket.listing_id in listing_map:
                    listing = listing_map[ticket.listing_id]
                    ticket_dict['listing'] = {
                        'listing_id': listing.listing_id,
                        'name': listing.name,
                        'internal_listing_name': listing.internal_listing_name,
                        'address': listing.address,
                        'city': listing.city
                    }
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
                Ticket.assigned_user_id == self.user_id
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
    
    def _calculate_occupancy(self, months: int = 6) -> List[Dict]:
        """
        Calculate occupancy rate for the last N months.
        
        Occupancy is calculated as: (total occupied nights across all listings) / (total available nights)
        Uses sets to track occupied dates per listing to prevent double-counting overlapping reservations.
        """
        try:
            today = date.today()
            start_date = today.replace(day=1) - timedelta(days=32 * months)
            start_date = start_date.replace(day=1)  # First day of the month
            
            # Get all reservations in the date range
            # Exclude cancelled reservations (case-insensitive check)
            reservations = self.main_session.query(Reservation).filter(
                Reservation.arrival_date.isnot(None),
                Reservation.departure_date.isnot(None),
                Reservation.arrival_date <= (today + timedelta(days=32 * months)),
                Reservation.departure_date >= start_date
            ).all()
            
            # Filter out cancelled reservations in Python (more reliable for case-insensitive)
            cancelled_statuses = ['cancelled', 'canceled', 'Cancelled', 'Canceled']
            reservations = [r for r in reservations if r.status not in cancelled_statuses]
            
            # Get all listings
            listings = self.main_session.query(Listing).all()
            listing_ids = [l.listing_id for l in listings]
            
            final_result = []
            for i in range(months):
                month_date = today.replace(day=1) - timedelta(days=32 * (months - 1 - i))
                month_date = month_date.replace(day=1)
                
                # Get last day of month
                last_day = monthrange(month_date.year, month_date.month)[1]
                month_end = month_date.replace(day=last_day)
                
                # Total available nights in month (using days as the unit)
                # For occupancy calculation: each day represents one night available
                total_nights_per_listing = last_day
                
                # Track occupied dates per listing using sets
                # This prevents double-counting when multiple reservations overlap
                listing_occupied_dates = {}
                for listing_id in listing_ids:
                    listing_occupied_dates[listing_id] = set()
                
                # Mark each date as occupied for each listing
                for reservation in reservations:
                    # Check if reservation overlaps with this month
                    if reservation.arrival_date <= month_end and reservation.departure_date >= month_date:
                        listing_id = reservation.listing_id
                        if listing_id not in listing_occupied_dates:
                            continue
                        
                        # Calculate date range for this month
                        overlap_start = max(reservation.arrival_date, month_date)
                        overlap_end = min(reservation.departure_date, month_end)
                        
                        # Mark each date as occupied (using dates, not counting days)
                        # This ensures overlapping reservations don't double-count
                        current_date = overlap_start
                        while current_date <= overlap_end:
                            listing_occupied_dates[listing_id].add(current_date)
                            current_date += timedelta(days=1)
                
                # Calculate total occupied nights across all listings
                total_occupied_nights = 0
                for listing_id in listing_ids:
                    occupied_nights = len(listing_occupied_dates.get(listing_id, set()))
                    total_occupied_nights += occupied_nights
                
                # Calculate total available nights (all listings × nights in month)
                total_available_nights = len(listing_ids) * total_nights_per_listing if listing_ids else 0
                
                # Calculate overall occupancy rate
                if total_available_nights > 0:
                    occupancy_rate = (total_occupied_nights / total_available_nights) * 100
                else:
                    occupancy_rate = 0.0
                
                final_result.append({
                    'month': month_date.strftime('%Y-%m'),
                    'occupancy_rate': round(occupancy_rate, 2),
                    'listing_id': None,
                    'listing_name': 'All Properties'
                })
            
            return final_result
        except Exception as e:
            logger.error(f"Error in _calculate_occupancy for user {self.user_id}: {e}", exc_info=True)
            return []
    
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

