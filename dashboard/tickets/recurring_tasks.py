#!/usr/bin/env python3
"""
Recurring tasks processor for tickets.
Handles automatic reopening of closed recurring tickets.
"""

import sys
import os
from datetime import datetime, date, timedelta
from typing import List, Optional, Any, Tuple
import logging

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from dashboard.tickets.models import Ticket, get_session
from dashboard.tickets.models import update_ticket
from dashboard.auth.models import get_session as get_user_session, User
from dateutil.relativedelta import relativedelta
from dashboard.tickets.recurrence_utils import parse_weekdays, parse_annual_dates, get_next_valid_month_day

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


def calculate_next_due_date(
    initial_due_date: date,
    recurrence_type: str = 'frequency',
    current_date: date = None,
    # Frequency-based params (existing)
    frequency_value: int = None,
    frequency_unit: str = None,
    # Weekly params
    weekdays: List[int] = None,
    recurrence_weekdays: Any = None,  # Allow passing as string/list
    # Monthly params
    month_day: int = None,
    recurrence_month_day: int = None,
    # Quarterly params
    quarter_month: int = None,
    recurrence_quarter_month: int = None,
    quarter_day: int = None,
    recurrence_quarter_day: int = None,
    # Annual params
    annual_dates: List[Tuple[int, int]] = None,
    recurrence_annual_dates: Any = None,  # Allow passing as string/list
) -> date:
    """
    Calculate the next due date based on recurrence type and configuration.
    
    Args:
        initial_due_date: The original due date when recurring was set
        recurrence_type: Type of recurrence ('frequency', 'weekly', 'monthly', 'quarterly', 'annual')
        current_date: Current date (defaults to today)
        frequency_value: Number in frequency (e.g., 30 for "30 days")
        frequency_unit: Unit: "days" or "months"
        weekdays: List of weekday integers (0=Monday, 6=Sunday) for weekly recurrence
        recurrence_weekdays: Weekdays as string/list (alternative to weekdays param)
        month_day: Day of month (1-31) for monthly recurrence
        recurrence_month_day: Day of month (alternative to month_day param)
        quarter_month: Which month of quarter (1, 2, or 3) for quarterly recurrence
        recurrence_quarter_month: Quarter month (alternative to quarter_month param)
        quarter_day: Day of month (1-31) for quarterly recurrence
        recurrence_quarter_day: Quarter day (alternative to quarter_day param)
        annual_dates: List of (month, day) tuples for annual recurrence
        recurrence_annual_dates: Annual dates as string/list (alternative to annual_dates param)
    
    Returns:
        Next due date
    """
    if current_date is None:
        current_date = date.today()
    
    # Normalize parameter names (support both direct params and recurrence_* params)
    if recurrence_type == 'frequency':
        if not frequency_value or not frequency_unit:
            raise ValueError("frequency_value and frequency_unit are required for frequency recurrence")
        
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
    
    elif recurrence_type == 'weekly':
        # Parse weekdays
        if weekdays is None:
            weekdays = parse_weekdays(recurrence_weekdays) if recurrence_weekdays else []
        
        if not weekdays:
            raise ValueError("At least one weekday must be specified for weekly recurrence")
        
        # Find next occurrence of any selected weekday
        # Start from current_date (or initial_due_date if it's in the future)
        search_start = max(current_date, initial_due_date)
        
        # If current day is in selection, start from tomorrow to get NEXT occurrence
        current_weekday = search_start.weekday()
        start_offset = 1 if current_weekday in weekdays else 0
        
        # Check up to 8 days ahead (to cover a full week + 1 day)
        for days_ahead in range(start_offset, 8):
            check_date = search_start + timedelta(days=days_ahead)
            weekday = check_date.weekday()  # 0=Monday, 6=Sunday
            if weekday in weekdays:
                return check_date
        
        # Should never reach here, but fallback
        return search_start + timedelta(days=7)
    
    elif recurrence_type == 'monthly':
        # Use month_day or recurrence_month_day
        target_day = month_day if month_day is not None else recurrence_month_day
        if target_day is None:
            raise ValueError("recurrence_month_day is required for monthly recurrence")
        
        # Start from current_date (or initial_due_date if it's in the future)
        search_start = max(current_date, initial_due_date)
        
        # Start checking from current month
        year = search_start.year
        month = search_start.month
        
        # Check up to 2 months ahead to find next valid occurrence
        for _ in range(2):
            valid_day = get_next_valid_month_day(year, month, target_day)
            candidate = date(year, month, valid_day)
            
            if candidate >= search_start:
                return candidate
            
            # Move to next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
        
        # Fallback (should never reach here)
        valid_day = get_next_valid_month_day(year, month, target_day)
        return date(year, month, valid_day)
    
    elif recurrence_type == 'quarterly':
        # Use quarter params or recurrence_quarter params
        target_quarter_month = quarter_month if quarter_month is not None else recurrence_quarter_month
        target_quarter_day = quarter_day if quarter_day is not None else recurrence_quarter_day
        
        if target_quarter_month is None or target_quarter_day is None:
            raise ValueError("recurrence_quarter_month and recurrence_quarter_day are required for quarterly recurrence")
        
        if target_quarter_month < 1 or target_quarter_month > 3:
            raise ValueError("recurrence_quarter_month must be 1, 2, or 3")
        
        # Start from current_date (or initial_due_date if it's in the future)
        search_start = max(current_date, initial_due_date)
        
        # Determine which quarter we're in (Q1: Jan-Mar, Q2: Apr-Jun, Q3: Jul-Sep, Q4: Oct-Dec)
        current_quarter = (search_start.month - 1) // 3 + 1
        current_year = search_start.year
        
        # Calculate the actual month in the quarter (1st, 2nd, or 3rd month)
        quarter_start_month = (current_quarter - 1) * 3 + 1
        target_month = quarter_start_month + (target_quarter_month - 1)
        
        # Check if we're past the target month in current quarter
        if search_start.month > target_month or (search_start.month == target_month and search_start.day > target_quarter_day):
            # Move to next quarter
            if current_quarter == 4:
                current_year += 1
                current_quarter = 1
            else:
                current_quarter += 1
            quarter_start_month = (current_quarter - 1) * 3 + 1
            target_month = quarter_start_month + (target_quarter_month - 1)
        
        # Get valid day for the target month
        valid_day = get_next_valid_month_day(current_year, target_month, target_quarter_day)
        candidate = date(current_year, target_month, valid_day)
        
        # If candidate is before search_start, move to next quarter
        if candidate < search_start:
            if current_quarter == 4:
                current_year += 1
                current_quarter = 1
            else:
                current_quarter += 1
            quarter_start_month = (current_quarter - 1) * 3 + 1
            target_month = quarter_start_month + (target_quarter_month - 1)
            valid_day = get_next_valid_month_day(current_year, target_month, target_quarter_day)
            candidate = date(current_year, target_month, valid_day)
        
        return candidate
    
    elif recurrence_type == 'annual':
        # Parse annual dates
        if annual_dates is None:
            annual_dates = parse_annual_dates(recurrence_annual_dates) if recurrence_annual_dates else []
        
        if not annual_dates:
            raise ValueError("At least one annual date must be specified for annual recurrence")
        
        # Start from current_date (or initial_due_date if it's in the future)
        search_start = max(current_date, initial_due_date)
        current_year = search_start.year
        
        # Find next occurrence of any annual date
        candidates = []
        
        # Check dates in current year
        for month, day in annual_dates:
            valid_day = get_next_valid_month_day(current_year, month, day)
            candidate = date(current_year, month, valid_day)
            if candidate >= search_start:
                candidates.append(candidate)
        
        # Check dates in next year if none found in current year
        if not candidates:
            for month, day in annual_dates:
                valid_day = get_next_valid_month_day(current_year + 1, month, day)
                candidate = date(current_year + 1, month, valid_day)
                candidates.append(candidate)
        
        # Return earliest candidate
        return min(candidates) if candidates else search_start
    
    else:
        raise ValueError(f"Invalid recurrence_type: {recurrence_type}. Must be one of: 'frequency', 'weekly', 'monthly', 'quarterly', 'annual'")


def get_next_occurrence_date(ticket: Ticket, current_date: date = None) -> Optional[date]:
    """
    Calculate the next occurrence date for a recurring ticket.
    
    Args:
        ticket: The ticket object
        current_date: Current date (defaults to today)
    
    Returns:
        Next occurrence date, or None if ticket is not recurring or missing required data
    """
    if not ticket.is_recurring:
        return None
    
    # Use initial_due_date if available, otherwise fall back to current due_date
    base_due_date = ticket.initial_due_date
    if not base_due_date:
        base_due_date = ticket.due_date
        if not base_due_date:
            return None
    
    if current_date is None:
        current_date = date.today()
    
    recurrence_type = ticket.recurrence_type or 'frequency'
    
    try:
        next_due_date = calculate_next_due_date(
            base_due_date,
            recurrence_type=recurrence_type,
            current_date=current_date,
            frequency_value=ticket.frequency_value,
            frequency_unit=ticket.frequency_unit,
            recurrence_weekdays=ticket.recurrence_weekdays,
            recurrence_month_day=ticket.recurrence_month_day,
            recurrence_quarter_month=ticket.recurrence_quarter_month,
            recurrence_quarter_day=ticket.recurrence_quarter_day,
            recurrence_annual_dates=ticket.recurrence_annual_dates,
        )
        return next_due_date
    except Exception as e:
        logger.error(f"Error calculating next occurrence for ticket {ticket.ticket_id}: {e}", exc_info=True)
        return None


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
    
    # Determine recurrence type (default to 'frequency' for backward compatibility)
    recurrence_type = ticket.recurrence_type if ticket.recurrence_type else 'frequency'
    
    # Validate recurrence configuration based on type
    if recurrence_type == 'frequency':
        if not ticket.frequency_value or not ticket.frequency_unit:
            logger.warning(f"Ticket {ticket.ticket_id} is recurring but has invalid frequency")
            return False
    elif recurrence_type == 'weekly':
        if not ticket.recurrence_weekdays:
            logger.warning(f"Ticket {ticket.ticket_id} is weekly recurring but has no weekdays specified")
            return False
    elif recurrence_type == 'monthly':
        if ticket.recurrence_month_day is None:
            logger.warning(f"Ticket {ticket.ticket_id} is monthly recurring but has no month_day specified")
            return False
    elif recurrence_type == 'quarterly':
        if ticket.recurrence_quarter_month is None or ticket.recurrence_quarter_day is None:
            logger.warning(f"Ticket {ticket.ticket_id} is quarterly recurring but has incomplete configuration")
            return False
    elif recurrence_type == 'annual':
        if not ticket.recurrence_annual_dates:
            logger.warning(f"Ticket {ticket.ticket_id} is annual recurring but has no annual_dates specified")
            return False
    else:
        logger.warning(f"Ticket {ticket.ticket_id} has invalid recurrence_type: {recurrence_type}")
        return False
    
    try:
        # Calculate next due date based on recurrence type
        next_due_date = calculate_next_due_date(
            ticket.initial_due_date,
            recurrence_type=recurrence_type,
            frequency_value=ticket.frequency_value,
            frequency_unit=ticket.frequency_unit,
            recurrence_weekdays=ticket.recurrence_weekdays,
            recurrence_month_day=ticket.recurrence_month_day,
            recurrence_quarter_month=ticket.recurrence_quarter_month,
            recurrence_quarter_day=ticket.recurrence_quarter_day,
            recurrence_annual_dates=ticket.recurrence_annual_dates,
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

