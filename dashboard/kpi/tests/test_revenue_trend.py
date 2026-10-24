"""Independent arithmetic for the fixed-cohort, calendar-anniversary table."""
from datetime import date, datetime, timedelta, timezone

import pytest

from dashboard.kpi.estimates import estimate_revenue
from dashboard.kpi.operations import local_window, months_before
from dashboard.kpi.revenue_trend import SCHEMA, weekly_comparison
from dashboard.kpi.tests.test_estimates import W, booking, data


def observed_days(member, period, start, end, amount):
    return {(member, period, start + timedelta(days=i)): amount for i in range((end - start).days)}


def test_both_columns_and_chart_use_the_goal_common_cohort_not_all_listings():
    d = data()
    d['listings'] += [{**d['listings'][0], 'id': 2, 'name': 'New'}, {**d['listings'][0], 'id': 3, 'name': 'Prior only'}]
    d['reservations'] = [booking(), booking(2, 2025, total_price=150), booking(3, listing_id=2, total_price=10050), booking(4, 2025, listing_id=3, total_price=10050)]
    r = estimate_revenue(d, {lid: W[1] for lid in (1, 2, 3)})
    assert r['denominator'] == 1 and r['trend_schema'] == SCHEMA
    assert sum(x['booking_value_usd'] or 0 for x in r['trend']) == 200
    assert sum(x['prior_booking_value_usd'] or 0 for x in r['trend']) == 100
    assert all(x['cohort_size'] == 1 and x['value'] == x['booking_value_usd'] for x in r['trend'])
    assert 'Same 1 common listing' in r['trend_scope'] and 'refunds' in r['trend_scope']


def test_boundary_stays_clip_both_anniversary_spans_to_equal_partial_weeks():
    d = data()
    d['reservations'] = [booking(arrival=date(2026, 7, 22), departure=date(2026, 7, 24)), booking(2, 2025, arrival=date(2025, 7, 22), departure=date(2025, 7, 24), total_price=150), booking(3, arrival=date(2026, 10, 22), departure=date(2026, 10, 24)), booking(4, 2025, arrival=date(2025, 10, 22), departure=date(2025, 10, 24), total_price=150)]
    rows = estimate_revenue(d, W)['trend']
    first, last = rows[0], rows[-1]
    assert first['date'] == '2026-07-20' and last['date'] == '2026-10-19'
    assert first['current_spans'] == ['2026-07-23 → 2026-07-27 (exclusive)']
    assert first['prior_spans'] == ['2025-07-23 → 2025-07-27 (exclusive)']
    assert last['prior_spans'] == ['2025-10-19 → 2025-10-23 (exclusive)']
    for row in (first, last):
        assert row['partial_week'] and row['current_days_per_listing'] == row['prior_days_per_listing'] == [4]
        assert row['booking_value_usd'] == 100 and row['prior_booking_value_usd'] == 50
        assert row['yoy_change_pct'] == 100


@pytest.mark.parametrize('current,prior,expected,reason', [(100, None, None, 'Prior observations unavailable'), (100, 0, None, 'Prior booking value is zero'), (None, 100, None, 'Current observations unavailable'), (0, 100, -100, None), (125, 100, 25, None), (75, 100, -25, None)])
def test_unknown_zero_and_signed_changes_are_distinct(current, prior, expected, reason):
    daily = {}
    if current is not None: daily[(1, 'current', date(2026, 8, 1))] = current
    if prior is not None: daily[(1, 'prior', date(2025, 8, 1))] = prior
    row = next(x for x in weekly_comparison(daily, W, {1}) if x['date'] == '2026-07-27')
    assert row['booking_value_usd'] == current and row['prior_booking_value_usd'] == prior
    assert row['yoy_change_pct'] == expected and row['yoy_unavailable_reason'] == reason


@pytest.mark.parametrize('year,week,current_days,prior_days', [(2024, '2024-02-26', 7, 6), (2025, '2025-02-24', 7, 8)])
def test_leap_spans_partition_goal_windows_without_normalizing_unequal_lengths(year, week, current_days, prior_days):
    w = local_window(datetime(year, 3, 10, 12, tzinfo=timezone.utc), 'America/New_York')
    prior_start, prior_end = months_before(w['start'], 12), months_before(w['end'], 12)
    daily = observed_days(1, 'current', w['start'], w['end'], 10) | observed_days(1, 'prior', prior_start, prior_end, 10)
    rows = weekly_comparison(daily, {1: w}, {1})
    row = next(x for x in rows if x['date'] == week)
    assert row['booking_value_usd'] == current_days * 10 and row['prior_booking_value_usd'] == prior_days * 10
    assert row['current_days_per_listing'] == [current_days] and row['prior_days_per_listing'] == [prior_days]
    assert row['yoy_change_pct'] is None and row['yoy_unavailable_reason'] == 'Different calendar day counts'
    assert sum(x['booking_value_usd'] for x in rows) == (w['end'] - w['start']).days * 10
    assert sum(x['prior_booking_value_usd'] for x in rows) == (prior_end - prior_start).days * 10


def test_leap_day_window_boundary_clamps_the_same_way_as_goal():
    w = local_window(datetime(2024, 5, 29, 12, tzinfo=timezone.utc), 'America/New_York')
    row = weekly_comparison({}, {1: w}, {1})[0]
    assert w['start'] == date(2024, 2, 29)
    assert row['prior_spans'] == ['2023-02-28 → 2023-03-04 (exclusive)']
    assert row['current_days_per_listing'] == row['prior_days_per_listing'] == [4]
    assert row['partial_week']


def test_different_property_local_edges_retain_the_same_roster_and_clip_each_property():
    a = W[1]; b = {**a, 'start': a['start'] + timedelta(days=1), 'end': a['end'] + timedelta(days=1)}
    daily = {(1, 'current', date(2026, 7, 23)): 10, (1, 'prior', date(2025, 7, 23)): 5, (2, 'current', date(2026, 7, 24)): 20, (2, 'prior', date(2025, 7, 24)): 10}
    row = weekly_comparison(daily, {1: a, 2: b}, {1, 2})[0]
    assert row['cohort_size'] == row['contributing_listings'] == 2
    assert row['current_days_per_listing'] == row['prior_days_per_listing'] == [3, 4]
    assert row['booking_value_usd'] == 30 and row['prior_booking_value_usd'] == 15
    assert row['yoy_change_pct'] == 100 and row['partial_week']


def test_missing_common_cohort_has_no_fabricated_rows():
    assert weekly_comparison({}, W, set()) == []
    assert estimate_revenue(data(), W)['trend'] == []


def test_different_properties_cannot_supply_each_others_comparison_coverage():
    daily={(1,'current',date(2026,8,1)):100,(2,'prior',date(2025,8,1)):200}
    rows=weekly_comparison(daily,{1:W[1],2:W[1]},{1,2})
    row=next(x for x in rows if x['date']=='2026-07-27')
    assert row['booking_value_usd'] is row['prior_booking_value_usd'] is row['yoy_change_pct'] is None
    assert row['coverage']['current']=={'observed_listings':1,'expected_listings':2,'missing_listing_ids':[2]}
    assert row['coverage']['prior']['missing_listing_ids']==[1]
    # Explicit observed zeros establish coverage, unlike absent entries.
    daily.update({(2,'current',date(2026,8,1)):0,(1,'prior',date(2025,8,1)):0})
    row=next(x for x in weekly_comparison(daily,{1:W[1],2:W[1]},{1,2}) if x['date']=='2026-07-27')
    assert row['booking_value_usd']==100 and row['prior_booking_value_usd']==200 and row['yoy_change_pct']==-50
