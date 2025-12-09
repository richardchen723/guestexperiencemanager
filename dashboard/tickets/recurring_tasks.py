#!/usr/bin/env python3
"""
Recurring tasks processor for tickets.
Handles automatic reopening of closed recurring tickets.
"""

import sys
import os
from datetime import datetime, date, timedelta
from typing import List, Optional
import logging

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import Ticket, get_session
from dashboard.tickets.models import update_ticket
from dashboard.auth.models import get_session as get_user_session, User
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


def get_admin_user() -> Optional[User]:
    """
    Find an admin user (admin or owner role).
    Returns the first admin found, or owner if no admin exists.
    """
    session = get_user_session()
    try:
        # Try to find an admin first
        admin = session.query(User).filter(User.role == 'admin').first()
        if admin:
            return admin
        
        # Fallback to owner
        owner = session.query(User).filter(User.role == 'owner').first()
        return owner
    finally:
        session.close()


def calculate_next_due_date(initial_due_date: date, frequency_value: int, 
                            frequency_unit: str, current_date: date = None) -> date:
    """
    Calculate the next due date based on initial due date and frequency.
    
    Args:
        initial_due_date: The original due date when recurring was set
        frequency_value: Number in frequency (e.g., 30 for "30 days")
        frequency_unit: Unit: "days" or "months"
        current_date: Current date (defaults to today)
    
    Returns:
        Next due date
    """
    if current_date is None:
        current_date = date.today()
    
    if frequency_unit == 'days':
        # Calculate how many intervals have passed
        days_since_initial = (current_date - initial_due_date).days
        intervals_passed = days_since_initial // frequency_value
        
        # Next due date is initial + (intervals_passed + 1) * frequency
        next_due_date = initial_due_date + timedelta(days=(intervals_passed + 1) * frequency_value)
        
    elif frequency_unit == 'months':
        # Calculate how many months have passed
        months_since_initial = (current_date.year - initial_due_date.year) * 12 + \
                               (current_date.month - initial_due_date.month)
        intervals_passed = months_since_initial // frequency_value
        
        # Next due date is initial + (intervals_passed + 1) * frequency months
        next_due_date = initial_due_date + relativedelta(months=(intervals_passed + 1) * frequency_value)
    else:
        raise ValueError(f"Invalid frequency_unit: {frequency_unit}. Must be 'days' or 'months'")
    
    return next_due_date


def reopen_recurring_ticket(ticket: Ticket) -> bool:
    """
    Reopen a recurring ticket with updated due date and assignment.
    
    Args:
        ticket: The ticket to reopen
    
    Returns:
        True if ticket was reopened, False otherwise
    """
    if not ticket.is_recurring or not ticket.is_recurring_active:
        return False
    
    if not ticket.initial_due_date:
        logger.warning(f"Ticket {ticket.ticket_id} is recurring but has no initial_due_date")
        return False
    
    if not ticket.frequency_value or not ticket.frequency_unit:
        logger.warning(f"Ticket {ticket.ticket_id} is recurring but has invalid frequency")
        return False
    
    try:
        # Calculate next due date
        next_due_date = calculate_next_due_date(
            ticket.initial_due_date,
            ticket.frequency_value,
            ticket.frequency_unit
        )
        
        # Get reopen days (default to 10 if not set)
        reopen_days = ticket.reopen_days_before_due_date if ticket.reopen_days_before_due_date is not None else 10
        
        # Check if we should reopen (current date is reopen_days before next due date)
        current_date = date.today()
        reopen_date = next_due_date - timedelta(days=reopen_days)
        
        if current_date >= reopen_date:
            # Reopen the ticket
            update_data = {
                'status': 'Open',
                'due_date': next_due_date,
                'updated_at': datetime.utcnow()
            }
            
            # Assign to recurring admin if set
            if ticket.recurring_admin_id:
                update_data['assigned_user_id'] = ticket.recurring_admin_id
            
            update_ticket(ticket.ticket_id, **update_data)
            logger.info(f"Reopened recurring ticket {ticket.ticket_id}. Next due date: {next_due_date}")
            return True
        
        return False
    except Exception as e:
        logger.error(f"Error reopening ticket {ticket.ticket_id}: {e}", exc_info=True)
        return False


def process_recurring_tasks() -> dict:
    """
    Process all recurring tasks and reopen closed tickets that are due.
    
    Returns:
        Dictionary with processing results:
        {
            'processed': int,  # Number of tickets processed
            'reopened': int,   # Number of tickets reopened
            'errors': int      # Number of errors
        }
    """
    session = get_session()
    results = {
        'processed': 0,
        'reopened': 0,
        'errors': 0
    }
    
    try:
        # Find all closed/resolved tickets that are recurring and active
        closed_statuses = ['Resolved', 'Closed']
        tickets = session.query(Ticket).filter(
            Ticket.is_recurring == True,
            Ticket.is_recurring_active == True,
            Ticket.status.in_(closed_statuses)
        ).all()
        
        logger.info(f"Found {len(tickets)} closed recurring tickets to check")
        
        for ticket in tickets:
            results['processed'] += 1
            try:
                if reopen_recurring_ticket(ticket):
                    results['reopened'] += 1
            except Exception as e:
                results['errors'] += 1
                logger.error(f"Error processing ticket {ticket.ticket_id}: {e}", exc_info=True)
        
        logger.info(f"Recurring tasks processing complete: {results['processed']} processed, "
                   f"{results['reopened']} reopened, {results['errors']} errors")
        
        return results
    finally:
        session.close()

