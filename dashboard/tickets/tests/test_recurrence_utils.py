#!/usr/bin/env python3
"""
Unit tests for recurrence utility functions.
Tests validation, parsing, and formatting functions.
"""

import unittest
import calendar

import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

from dashboard.tickets.recurrence_utils import (
    validate_recurrence_config,
    parse_weekdays,
    parse_annual_dates,
    get_next_valid_month_day,
    format_recurrence_description
)


class TestValidateRecurrenceConfig(unittest.TestCase):
    """Tests for validate_recurrence_config()."""
    
    def test_valid_frequency_config(self):
        """Test valid frequency configuration."""
        is_valid, error = validate_recurrence_config('frequency', {
            'frequency_value': 30,
            'frequency_unit': 'days'
        })
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_valid_weekly_config(self):
        """Test valid weekly configuration."""
        is_valid, error = validate_recurrence_config('weekly', {
            'recurrence_weekdays': [0, 2, 4]  # Mon, Wed, Fri
        })
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_valid_monthly_config(self):
        """Test valid monthly configuration."""
        is_valid, error = validate_recurrence_config('monthly', {
            'recurrence_month_day': 15
        })
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_valid_quarterly_config(self):
        """Test valid quarterly configuration."""
        is_valid, error = validate_recurrence_config('quarterly', {
            'recurrence_quarter_month': 1,
            'recurrence_quarter_day': 10
        })
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_valid_annual_config(self):
        """Test valid annual configuration."""
        is_valid, error = validate_recurrence_config('annual', {
            'recurrence_annual_dates': [(4, 10), (10, 15)]
        })
        self.assertTrue(is_valid)
        self.assertIsNone(error)
    
    def test_invalid_recurrence_type(self):
        """Test invalid recurrence_type."""
        is_valid, error = validate_recurrence_config('invalid', {})
        self.assertFalse(is_valid)
        self.assertIn('Invalid recurrence_type', error)
    
    def test_missing_frequency_fields(self):
        """Test missing frequency fields."""
        is_valid, error = validate_recurrence_config('frequency', {
            'frequency_value': 30
            # Missing frequency_unit
        })
        self.assertFalse(is_valid)
        self.assertIn('frequency_unit', error)
    
    def test_invalid_weekday_values(self):
        """Test invalid weekday values (out of range)."""
        is_valid, error = validate_recurrence_config('weekly', {
            'recurrence_weekdays': [0, 7, 8]  # 7 and 8 are invalid
        })
        self.assertFalse(is_valid)
        self.assertIn('between 0', error)
    
    def test_invalid_month_day(self):
        """Test invalid month_day (out of range)."""
        is_valid, error = validate_recurrence_config('monthly', {
            'recurrence_month_day': 32  # Invalid
        })
        self.assertFalse(is_valid)
        self.assertIn('between 1 and 31', error)
    
    def test_invalid_quarter_month(self):
        """Test invalid quarter_month."""
        is_valid, error = validate_recurrence_config('quarterly', {
            'recurrence_quarter_month': 4,  # Invalid (must be 1-3)
            'recurrence_quarter_day': 10
        })
        self.assertFalse(is_valid)
        self.assertIn('1, 2, or 3', error)
    
    def test_invalid_annual_date_format(self):
        """Test invalid annual date format."""
        is_valid, error = validate_recurrence_config('annual', {
            'recurrence_annual_dates': 'invalid-format'
        })
        self.assertFalse(is_valid)


class TestParseWeekdays(unittest.TestCase):
    """Tests for parse_weekdays()."""
    
    def test_parse_comma_separated_string(self):
        """Test parsing comma-separated string."""
        result = parse_weekdays("0,2,4")
        self.assertEqual(result, [0, 2, 4])
    
    def test_parse_json_array_string(self):
        """Test parsing JSON array string."""
        result = parse_weekdays("[0,2,4]")
        self.assertEqual(result, [0, 2, 4])
    
    def test_parse_list_of_integers(self):
        """Test parsing list of integers."""
        result = parse_weekdays([0, 2, 4])
        self.assertEqual(result, [0, 2, 4])
    
    def test_handle_empty_input(self):
        """Test handling empty input."""
        result = parse_weekdays(None)
        self.assertEqual(result, [])
        
        result = parse_weekdays("")
        self.assertEqual(result, [])
    
    def test_remove_duplicates(self):
        """Test that duplicates are removed."""
        result = parse_weekdays("0,2,0,4,2")
        self.assertEqual(result, [0, 2, 4])
    
    def test_filter_out_of_range(self):
        """Test that out of range values are filtered."""
        result = parse_weekdays("0,2,7,8,4")
        self.assertEqual(result, [0, 2, 4])  # 7 and 8 filtered out
    
    def test_sort_result(self):
        """Test that result is sorted."""
        result = parse_weekdays("4,0,2")
        self.assertEqual(result, [0, 2, 4])


class TestParseAnnualDates(unittest.TestCase):
    """Tests for parse_annual_dates()."""
    
    def test_parse_comma_separated_mm_dd(self):
        """Test parsing comma-separated MM-DD format."""
        result = parse_annual_dates("04-10,10-15")
        self.assertEqual(result, [(4, 10), (10, 15)])
    
    def test_parse_json_array_of_strings(self):
        """Test parsing JSON array of strings."""
        result = parse_annual_dates('["04-10", "10-15"]')
        self.assertEqual(result, [(4, 10), (10, 15)])
    
    def test_parse_list_of_tuples(self):
        """Test parsing list of tuples."""
        result = parse_annual_dates([(4, 10), (10, 15)])
        self.assertEqual(result, [(4, 10), (10, 15)])
    
    def test_handle_empty_input(self):
        """Test handling empty input."""
        result = parse_annual_dates(None)
        self.assertEqual(result, [])
        
        result = parse_annual_dates("")
        self.assertEqual(result, [])
    
    def test_remove_duplicates(self):
        """Test that duplicates are removed."""
        result = parse_annual_dates("04-10,10-15,04-10")
        self.assertEqual(result, [(4, 10), (10, 15)])
    
    def test_sort_result(self):
        """Test that result is sorted."""
        result = parse_annual_dates("10-15,04-10,12-25")
        self.assertEqual(result, [(4, 10), (10, 15), (12, 25)])


class TestGetNextValidMonthDay(unittest.TestCase):
    """Tests for get_next_valid_month_day()."""
    
    def test_valid_day_in_month(self):
        """Test valid day returns as-is."""
        result = get_next_valid_month_day(2024, 1, 15)
        self.assertEqual(result, 15)
    
    def test_day_31_in_month_with_30_days(self):
        """Test day 31 in month with 30 days."""
        result = get_next_valid_month_day(2024, 4, 31)  # April has 30 days
        self.assertEqual(result, 30)
    
    def test_day_30_in_february(self):
        """Test day 30 in February."""
        result = get_next_valid_month_day(2024, 2, 30)  # Feb has 29 days in 2024
        self.assertEqual(result, 29)
    
    def test_day_29_in_february_non_leap_year(self):
        """Test day 29 in February non-leap year."""
        result = get_next_valid_month_day(2025, 2, 29)  # 2025 is not leap year
        self.assertEqual(result, 28)
    
    def test_day_29_in_february_leap_year(self):
        """Test day 29 in February leap year."""
        result = get_next_valid_month_day(2024, 2, 29)  # 2024 is leap year
        self.assertEqual(result, 29)
    
    def test_all_months_edge_cases(self):
        """Test edge cases for all months."""
        # Months with 31 days
        self.assertEqual(get_next_valid_month_day(2024, 1, 31), 31)  # January
        self.assertEqual(get_next_valid_month_day(2024, 3, 31), 31)  # March
        self.assertEqual(get_next_valid_month_day(2024, 5, 31), 31)  # May
        
        # Months with 30 days
        self.assertEqual(get_next_valid_month_day(2024, 4, 31), 30)  # April
        self.assertEqual(get_next_valid_month_day(2024, 6, 31), 30)  # June


class TestFormatRecurrenceDescription(unittest.TestCase):
    """Tests for format_recurrence_description()."""
    
    def test_format_frequency_description(self):
        """Test formatting frequency description."""
        desc = format_recurrence_description('frequency', {
            'frequency_value': 30,
            'frequency_unit': 'days'
        })
        self.assertEqual(desc, "Every 30 days")
        
        desc = format_recurrence_description('frequency', {
            'frequency_value': 1,
            'frequency_unit': 'days'
        })
        self.assertEqual(desc, "Every day")
    
    def test_format_weekly_single_day(self):
        """Test formatting weekly description (single day)."""
        desc = format_recurrence_description('weekly', {
            'recurrence_weekdays': [1]  # Tuesday
        })
        self.assertEqual(desc, "Every Tuesday")
    
    def test_format_weekly_multiple_days(self):
        """Test formatting weekly description (multiple days)."""
        desc = format_recurrence_description('weekly', {
            'recurrence_weekdays': [0, 2, 4]  # Mon, Wed, Fri
        })
        self.assertIn("Monday", desc)
        self.assertIn("Wednesday", desc)
        self.assertIn("Friday", desc)
    
    def test_format_monthly_description(self):
        """Test formatting monthly description."""
        desc = format_recurrence_description('monthly', {
            'recurrence_month_day': 15
        })
        self.assertEqual(desc, "Every 15th of the month")
        
        desc = format_recurrence_description('monthly', {
            'recurrence_month_day': 1
        })
        self.assertEqual(desc, "Every 1st of the month")
    
    def test_format_quarterly_description(self):
        """Test formatting quarterly description."""
        desc = format_recurrence_description('quarterly', {
            'recurrence_quarter_month': 1,
            'recurrence_quarter_day': 10
        })
        self.assertIn("1st month", desc)
        self.assertIn("10th", desc)
    
    def test_format_annual_single_date(self):
        """Test formatting annual description (single date)."""
        desc = format_recurrence_description('annual', {
            'recurrence_annual_dates': [(4, 10)]
        })
        self.assertEqual(desc, "Every April 10th")
    
    def test_format_annual_multiple_dates(self):
        """Test formatting annual description (multiple dates)."""
        desc = format_recurrence_description('annual', {
            'recurrence_annual_dates': [(4, 10), (10, 15)]
        })
        self.assertIn("April 10th", desc)
        self.assertIn("October 15th", desc)


if __name__ == '__main__':
    unittest.main()

