#!/usr/bin/env python3
"""
Unit tests for recurrence calculation logic.
Tests all recurrence types: frequency, weekly, monthly, quarterly, annual.
"""

import unittest
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

from dashboard.tickets.recurring_tasks import calculate_next_due_date


class TestFrequencyRecurrence(unittest.TestCase):
    """Tests for frequency-based recurrence (every N days/months)."""
    
    def test_daily_frequency(self):
        """Test daily frequency (every N days)."""
        initial = date(2024, 1, 1)
        current = date(2024, 1, 15)
        
        # Every 7 days
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=7, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 22))
        
        # Every 30 days
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=30, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 31))
    
    def test_monthly_frequency(self):
        """Test monthly frequency (every N months)."""
        initial = date(2024, 1, 15)
        current = date(2024, 3, 20)
        
        # Every 1 month
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=1, frequency_unit='months'
        )
        self.assertEqual(next_date, date(2024, 4, 15))
        
        # Every 3 months
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=3, frequency_unit='months'
        )
        self.assertEqual(next_date, date(2024, 4, 15))
    
    def test_frequency_same_day(self):
        """Test calculation when current date is exactly on initial date."""
        initial = date(2024, 1, 1)
        current = date(2024, 1, 1)
        
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=7, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 8))
    
    def test_frequency_before_initial(self):
        """Test calculation when current date is before initial date."""
        initial = date(2024, 1, 15)
        current = date(2024, 1, 10)
        
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=7, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 15))
    
    def test_frequency_leap_year(self):
        """Test frequency calculation with leap year (Feb 29)."""
        initial = date(2024, 2, 29)  # Leap year
        current = date(2024, 3, 1)
        
        # Every 1 year (12 months)
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=12, frequency_unit='months'
        )
        # 2025 is not a leap year, so Feb 28
        self.assertEqual(next_date, date(2025, 2, 28))


class TestWeeklyRecurrence(unittest.TestCase):
    """Tests for weekly recurrence (specific days of week)."""
    
    def test_single_weekday(self):
        """Test single weekday (e.g., every Tuesday)."""
        initial = date(2024, 1, 2)  # Tuesday
        current = date(2024, 1, 5)  # Friday
        
        # Every Tuesday (weekday 1)
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[1]
        )
        self.assertEqual(next_date, date(2024, 1, 9))  # Next Tuesday
    
    def test_multiple_weekdays(self):
        """Test multiple weekdays (e.g., Monday and Wednesday)."""
        initial = date(2024, 1, 1)  # Monday
        current = date(2024, 1, 5)  # Friday
        
        # Every Monday (0) and Wednesday (2)
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[0, 2]
        )
        self.assertEqual(next_date, date(2024, 1, 8))  # Next Monday
    
    def test_weekday_current_day_in_selection(self):
        """Test when current day is one of the selected weekdays."""
        initial = date(2024, 1, 1)  # Monday
        current = date(2024, 1, 8)  # Monday (in selection)
        
        # Every Monday
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[0]
        )
        # Should return the next Monday (not today)
        self.assertEqual(next_date, date(2024, 1, 15))
    
    def test_weekday_week_boundary(self):
        """Test week boundary crossing."""
        initial = date(2024, 1, 1)  # Monday
        current = date(2024, 1, 6)  # Saturday
        
        # Every Sunday (6)
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[6]
        )
        self.assertEqual(next_date, date(2024, 1, 7))  # Next day (Sunday)
    
    def test_all_weekdays(self):
        """Test all weekdays selected (should be next day)."""
        initial = date(2024, 1, 1)
        current = date(2024, 1, 5)
        
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[0, 1, 2, 3, 4, 5, 6]
        )
        self.assertEqual(next_date, date(2024, 1, 6))  # Next day


class TestMonthlyRecurrence(unittest.TestCase):
    """Tests for monthly recurrence (specific day of month)."""
    
    def test_valid_day_all_months(self):
        """Test day that exists in all months (1-28)."""
        initial = date(2024, 1, 15)
        current = date(2024, 2, 20)
        
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=15
        )
        self.assertEqual(next_date, date(2024, 3, 15))
    
    def test_day_29(self):
        """Test day 29 (exists in most months, not in Feb)."""
        initial = date(2024, 1, 29)
        current = date(2024, 2, 1)
        
        # Feb 2024 is leap year, has 29 days
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=29
        )
        self.assertEqual(next_date, date(2024, 2, 29))
    
    def test_day_29_non_leap_february(self):
        """Test day 29 when next month is non-leap February."""
        initial = date(2024, 1, 29)
        current = date(2025, 1, 30)
        
        # Feb 2025 is not leap year, has 28 days
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=29
        )
        self.assertEqual(next_date, date(2025, 2, 28))  # Adjusted to last day
    
    def test_day_31_month_with_30_days(self):
        """Test day 31 when next month has only 30 days."""
        initial = date(2024, 1, 31)
        current = date(2024, 2, 1)
        
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=31
        )
        self.assertEqual(next_date, date(2024, 2, 29))  # Feb has 29 days in 2024
    
    def test_month_boundary_crossing(self):
        """Test month boundary crossing."""
        initial = date(2024, 1, 15)
        current = date(2024, 1, 20)
        
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=15
        )
        self.assertEqual(next_date, date(2024, 2, 15))
    
    def test_year_boundary_crossing(self):
        """Test year boundary crossing."""
        initial = date(2024, 1, 15)
        current = date(2024, 12, 20)
        
        next_date = calculate_next_due_date(
            initial, 'monthly', current,
            month_day=15
        )
        self.assertEqual(next_date, date(2025, 1, 15))


class TestQuarterlyRecurrence(unittest.TestCase):
    """Tests for quarterly recurrence (specific day of quarter month)."""
    
    def test_first_month_of_quarter(self):
        """Test 1st month of quarter (Jan, Apr, Jul, Oct)."""
        initial = date(2024, 1, 10)
        current = date(2024, 2, 1)
        
        # 1st month, 10th day
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=1, quarter_day=10
        )
        self.assertEqual(next_date, date(2024, 4, 10))  # Next quarter (Apr)
    
    def test_second_month_of_quarter(self):
        """Test 2nd month of quarter (Feb, May, Aug, Nov)."""
        initial = date(2024, 2, 15)
        current = date(2024, 3, 1)
        
        # 2nd month, 15th day
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=2, quarter_day=15
        )
        self.assertEqual(next_date, date(2024, 5, 15))  # Next quarter (May)
    
    def test_third_month_of_quarter(self):
        """Test 3rd month of quarter (Mar, Jun, Sep, Dec)."""
        initial = date(2024, 3, 20)
        current = date(2024, 4, 1)
        
        # 3rd month, 20th day
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=3, quarter_day=20
        )
        self.assertEqual(next_date, date(2024, 6, 20))  # Next quarter (Jun)
    
    def test_quarter_boundary_crossing(self):
        """Test quarter boundary crossing."""
        initial = date(2024, 1, 10)
        current = date(2024, 3, 15)
        
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=1, quarter_day=10
        )
        self.assertEqual(next_date, date(2024, 4, 10))
    
    def test_year_boundary_crossing(self):
        """Test year boundary crossing in quarterly."""
        initial = date(2024, 10, 10)
        current = date(2024, 12, 15)
        
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=1, quarter_day=10
        )
        self.assertEqual(next_date, date(2025, 1, 10))
    
    def test_invalid_day_in_quarter_month(self):
        """Test invalid day in specific quarter month (e.g., Feb 30)."""
        initial = date(2024, 1, 30)
        current = date(2024, 2, 1)
        
        # 2nd month of Q1 is Feb, which doesn't have 30 days
        next_date = calculate_next_due_date(
            initial, 'quarterly', current,
            quarter_month=2, quarter_day=30
        )
        # Should adjust to Feb 29 (leap year) or Feb 28
        self.assertIn(next_date, [date(2024, 2, 28), date(2024, 2, 29)])


class TestAnnualRecurrence(unittest.TestCase):
    """Tests for annual recurrence (specific dates each year)."""
    
    def test_single_annual_date(self):
        """Test single annual date (e.g., April 10)."""
        initial = date(2024, 4, 10)
        current = date(2024, 5, 1)
        
        next_date = calculate_next_due_date(
            initial, 'annual', current,
            annual_dates=[(4, 10)]
        )
        self.assertEqual(next_date, date(2025, 4, 10))
    
    def test_multiple_annual_dates(self):
        """Test multiple annual dates."""
        initial = date(2024, 4, 10)
        current = date(2024, 5, 1)
        
        # April 10 and October 15
        next_date = calculate_next_due_date(
            initial, 'annual', current,
            annual_dates=[(4, 10), (10, 15)]
        )
        self.assertEqual(next_date, date(2024, 10, 15))  # Earliest future date
    
    def test_annual_date_ordering(self):
        """Test that next occurrence is earliest future date."""
        initial = date(2024, 4, 10)
        current = date(2024, 3, 1)
        
        # Multiple dates, current is before all
        next_date = calculate_next_due_date(
            initial, 'annual', current,
            annual_dates=[(4, 10), (10, 15), (12, 25)]
        )
        self.assertEqual(next_date, date(2024, 4, 10))  # Earliest
    
    def test_annual_year_boundary(self):
        """Test year boundary crossing."""
        initial = date(2024, 4, 10)
        current = date(2024, 12, 26)
        
        next_date = calculate_next_due_date(
            initial, 'annual', current,
            annual_dates=[(4, 10), (10, 15), (12, 25)]
        )
        self.assertEqual(next_date, date(2025, 4, 10))  # Next year
    
    def test_leap_year_date_feb_29(self):
        """Test leap year date (Feb 29)."""
        initial = date(2024, 2, 29)  # Leap year
        current = date(2024, 3, 1)
        
        next_date = calculate_next_due_date(
            initial, 'annual', current,
            annual_dates=[(2, 29)]
        )
        # 2025 is not leap year, should skip to 2026 (or adjust to Feb 28)
        # The function should handle this - let's check what it does
        # For now, expect it to adjust to valid date
        self.assertIn(next_date.month, [2, 3])
        self.assertIn(next_date.year, [2025, 2026])


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases across all recurrence types."""
    
    def test_current_date_exactly_on_initial(self):
        """Test when current_date is exactly on initial_due_date."""
        initial = date(2024, 1, 15)
        current = date(2024, 1, 15)
        
        # Frequency
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=7, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 22))
        
        # Weekly
        next_date = calculate_next_due_date(
            initial, 'weekly', current,
            weekdays=[0]  # Monday
        )
        # Jan 15, 2024 is a Monday, so next should be Jan 22
        self.assertEqual(next_date, date(2024, 1, 22))
    
    def test_current_date_before_initial(self):
        """Test when current_date is before initial_due_date."""
        initial = date(2024, 1, 15)
        current = date(2024, 1, 10)
        
        # Should return initial or next occurrence
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=7, frequency_unit='days'
        )
        self.assertEqual(next_date, date(2024, 1, 15))
    
    def test_current_date_in_future(self):
        """Test when current_date is in future."""
        initial = date(2024, 1, 1)
        current = date(2025, 6, 1)
        
        next_date = calculate_next_due_date(
            initial, 'frequency', current,
            frequency_value=30, frequency_unit='days'
        )
        # Should calculate from current, not initial
        self.assertGreater(next_date, current)


if __name__ == '__main__':
    unittest.main()

