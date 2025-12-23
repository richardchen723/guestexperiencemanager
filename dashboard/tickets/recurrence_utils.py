#!/usr/bin/env python3
"""
Utility functions for recurring task recurrence types.
Handles validation, parsing, and formatting of recurrence configurations.
"""

import json
import re
from typing import List, Optional, Tuple, Dict, Any
from datetime import date
import calendar


def validate_recurrence_config(recurrence_type: str, config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate a recurrence configuration.
    
    Args:
        recurrence_type: Type of recurrence ('frequency', 'weekly', 'monthly', 'quarterly', 'annual')
        config: Configuration dictionary with recurrence parameters
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    valid_types = ['frequency', 'weekly', 'monthly', 'quarterly', 'annual']
    
    if recurrence_type not in valid_types:
        return False, f"Invalid recurrence_type: {recurrence_type}. Must be one of {valid_types}"
    
    if recurrence_type == 'frequency':
        if 'frequency_value' not in config or config['frequency_value'] is None:
            return False, "frequency_value is required for frequency recurrence"
        if 'frequency_unit' not in config or config['frequency_unit'] is None:
            return False, "frequency_unit is required for frequency recurrence"
        if config['frequency_value'] <= 0:
            return False, "frequency_value must be greater than 0"
        if config['frequency_unit'] not in ['days', 'months']:
            return False, "frequency_unit must be 'days' or 'months'"
    
    elif recurrence_type == 'weekly':
        if 'recurrence_weekdays' not in config or not config['recurrence_weekdays']:
            return False, "recurrence_weekdays is required for weekly recurrence"
        # Check raw input for invalid values before parsing
        weekdays_input = config['recurrence_weekdays']
        if isinstance(weekdays_input, (list, tuple)):
            if any(not isinstance(w, int) or w < 0 or w > 6 for w in weekdays_input):
                return False, "Weekday values must be between 0 (Monday) and 6 (Sunday)"
        elif isinstance(weekdays_input, str):
            # Check if string contains invalid values
            try:
                # Try JSON first
                parsed = json.loads(weekdays_input)
                if isinstance(parsed, list):
                    if any(not isinstance(w, int) or w < 0 or w > 6 for w in parsed):
                        return False, "Weekday values must be between 0 (Monday) and 6 (Sunday)"
            except (json.JSONDecodeError, ValueError):
                # Try comma-separated
                parts = weekdays_input.split(',')
                for part in parts:
                    try:
                        w = int(part.strip())
                        if w < 0 or w > 6:
                            return False, "Weekday values must be between 0 (Monday) and 6 (Sunday)"
                    except ValueError:
                        return False, "Invalid weekday format"
        weekdays = parse_weekdays(config['recurrence_weekdays'])
        if not weekdays:
            return False, "At least one weekday must be specified"
    
    elif recurrence_type == 'monthly':
        if 'recurrence_month_day' not in config or config['recurrence_month_day'] is None:
            return False, "recurrence_month_day is required for monthly recurrence"
        month_day = config['recurrence_month_day']
        if not isinstance(month_day, int) or month_day < 1 or month_day > 31:
            return False, "recurrence_month_day must be between 1 and 31"
    
    elif recurrence_type == 'quarterly':
        if 'recurrence_quarter_month' not in config or config['recurrence_quarter_month'] is None:
            return False, "recurrence_quarter_month is required for quarterly recurrence"
        if 'recurrence_quarter_day' not in config or config['recurrence_quarter_day'] is None:
            return False, "recurrence_quarter_day is required for quarterly recurrence"
        quarter_month = config['recurrence_quarter_month']
        quarter_day = config['recurrence_quarter_day']
        if not isinstance(quarter_month, int) or quarter_month < 1 or quarter_month > 3:
            return False, "recurrence_quarter_month must be 1, 2, or 3"
        if not isinstance(quarter_day, int) or quarter_day < 1 or quarter_day > 31:
            return False, "recurrence_quarter_day must be between 1 and 31"
    
    elif recurrence_type == 'annual':
        if 'recurrence_annual_dates' not in config or not config['recurrence_annual_dates']:
            return False, "recurrence_annual_dates is required for annual recurrence"
        annual_dates = parse_annual_dates(config['recurrence_annual_dates'])
        if not annual_dates:
            return False, "At least one annual date must be specified"
        for month, day in annual_dates:
            if month < 1 or month > 12:
                return False, f"Invalid month in annual date: {month} (must be 1-12)"
            if day < 1 or day > 31:
                return False, f"Invalid day in annual date: {day} (must be 1-31)"
            # Check if date is valid (e.g., Feb 30 doesn't exist)
            try:
                # Use a leap year to check validity
                calendar.monthrange(2000, month)[1] >= day
            except (ValueError, calendar.IllegalMonthError):
                return False, f"Invalid date: month {month}, day {day}"
    
    return True, None


def parse_weekdays(weekdays_input: Any) -> List[int]:
    """
    Parse weekday input to a list of integers (0=Monday, 6=Sunday).
    
    Args:
        weekdays_input: Can be:
            - Comma-separated string: "0,2,4"
            - JSON array string: "[0,2,4]"
            - List of integers: [0, 2, 4]
    
    Returns:
        List of weekday integers, sorted and deduplicated
    """
    if weekdays_input is None:
        return []
    
    if isinstance(weekdays_input, list):
        weekdays = [int(w) for w in weekdays_input if w is not None]
    elif isinstance(weekdays_input, str):
        # Try JSON first
        try:
            parsed = json.loads(weekdays_input)
            if isinstance(parsed, list):
                weekdays = [int(w) for w in parsed]
            else:
                # Try comma-separated
                weekdays = [int(w.strip()) for w in weekdays_input.split(',') if w.strip()]
        except (json.JSONDecodeError, ValueError):
            # Try comma-separated
            weekdays = [int(w.strip()) for w in weekdays_input.split(',') if w.strip()]
    else:
        return []
    
    # Remove duplicates, sort, and filter valid range
    weekdays = sorted(set([w for w in weekdays if 0 <= w <= 6]))
    return weekdays


def parse_annual_dates(annual_dates_input: Any) -> List[Tuple[int, int]]:
    """
    Parse annual dates input to a list of (month, day) tuples.
    
    Args:
        annual_dates_input: Can be:
            - Comma-separated "MM-DD" format: "04-10,10-15"
            - JSON array of strings: ["04-10", "10-15"]
            - List of tuples: [(4, 10), (10, 15)]
    
    Returns:
        List of (month, day) tuples, sorted and deduplicated
    """
    if annual_dates_input is None:
        return []
    
    dates = []
    
    if isinstance(annual_dates_input, list):
        for item in annual_dates_input:
            if isinstance(item, tuple) and len(item) == 2:
                dates.append((int(item[0]), int(item[1])))
            elif isinstance(item, str):
                # Parse "MM-DD" format
                match = re.match(r'(\d{1,2})-(\d{1,2})', item)
                if match:
                    dates.append((int(match.group(1)), int(match.group(2))))
    elif isinstance(annual_dates_input, str):
        # Try JSON first
        try:
            parsed = json.loads(annual_dates_input)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, str):
                        match = re.match(r'(\d{1,2})-(\d{1,2})', item)
                        if match:
                            dates.append((int(match.group(1)), int(match.group(2))))
        except json.JSONDecodeError:
            # Try comma-separated "MM-DD" format
            for item in annual_dates_input.split(','):
                item = item.strip()
                match = re.match(r'(\d{1,2})-(\d{1,2})', item)
                if match:
                    dates.append((int(match.group(1)), int(match.group(2))))
    
    # Remove duplicates and sort
    dates = sorted(set(dates))
    return dates


def get_next_valid_month_day(year: int, month: int, day: int) -> int:
    """
    Get the valid day for a given month, handling edge cases like Feb 30th.
    
    Args:
        year: Year
        month: Month (1-12)
        day: Desired day (1-31)
    
    Returns:
        Valid day for the month (may be adjusted if day doesn't exist in month)
    """
    try:
        days_in_month = calendar.monthrange(year, month)[1]
        return min(day, days_in_month)
    except (ValueError, calendar.IllegalMonthError):
        # Invalid month, return 28 as safe default
        return 28


def format_recurrence_description(recurrence_type: str, config: Dict[str, Any]) -> str:
    """
    Format a human-readable description of the recurrence pattern.
    
    Args:
        recurrence_type: Type of recurrence
        config: Configuration dictionary
    
    Returns:
        Human-readable description string
    """
    if recurrence_type == 'frequency':
        value = config.get('frequency_value', 0)
        unit = config.get('frequency_unit', 'days')
        unit_name = 'day' if unit == 'days' else 'month'
        unit_name_plural = 'days' if unit == 'days' else 'months'
        if value == 1:
            return f"Every {unit_name}"
        return f"Every {value} {unit_name_plural}"
    
    elif recurrence_type == 'weekly':
        weekdays = parse_weekdays(config.get('recurrence_weekdays', []))
        if not weekdays:
            return "Weekly (no days selected)"
        
        weekday_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        selected_days = [weekday_names[w] for w in weekdays]
        
        if len(selected_days) == 1:
            return f"Every {selected_days[0]}"
        elif len(selected_days) == 2:
            return f"Every {selected_days[0]} and {selected_days[1]}"
        else:
            return f"Every {', '.join(selected_days[:-1])}, and {selected_days[-1]}"
    
    elif recurrence_type == 'monthly':
        day = config.get('recurrence_month_day', 0)
        if day == 0:
            return "Monthly (no day specified)"
        # Add ordinal suffix
        if 10 <= day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        return f"Every {day}{suffix} of the month"
    
    elif recurrence_type == 'quarterly':
        quarter_month = config.get('recurrence_quarter_month', 0)
        quarter_day = config.get('recurrence_quarter_day', 0)
        if quarter_month == 0 or quarter_day == 0:
            return "Quarterly (incomplete configuration)"
        
        month_names = ['1st', '2nd', '3rd']
        month_name = month_names[quarter_month - 1] if 1 <= quarter_month <= 3 else f"{quarter_month}th"
        
        # Add ordinal suffix for day
        if 10 <= quarter_day % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(quarter_day % 10, 'th')
        
        return f"Every {quarter_day}{suffix} of the {month_name} month of each quarter"
    
    elif recurrence_type == 'annual':
        annual_dates = parse_annual_dates(config.get('recurrence_annual_dates', []))
        if not annual_dates:
            return "Annual (no dates specified)"
        
        month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                      'July', 'August', 'September', 'October', 'November', 'December']
        
        formatted_dates = []
        for month, day in annual_dates:
            # Add ordinal suffix
            if 10 <= day % 100 <= 20:
                suffix = 'th'
            else:
                suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
            formatted_dates.append(f"{month_names[month - 1]} {day}{suffix}")
        
        if len(formatted_dates) == 1:
            return f"Every {formatted_dates[0]}"
        elif len(formatted_dates) == 2:
            return f"Every {formatted_dates[0]} and {formatted_dates[1]}"
        else:
            return f"Every {', '.join(formatted_dates[:-1])}, and {formatted_dates[-1]}"
    
    return "Unknown recurrence type"

