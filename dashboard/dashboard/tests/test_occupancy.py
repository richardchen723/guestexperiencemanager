from datetime import date, datetime, timezone
from types import SimpleNamespace as Row
from unittest.mock import Mock

import pytest

from dashboard.dashboard.occupancy import build_occupancy, month_start


def listing(listing_id=1, status=None):
    return Row(listing_id=listing_id, status=status)


def booking(arrival='2026-10-01', departure='2026-10-03', **values):
    return Row(**dict(dict(listing_id=1, status='new', source=None, channel_name='airbnbOfficial',
                          cancellation_date=None, is_dates_unspecified=0,
                          arrival_date=date.fromisoformat(arrival) if arrival else None,
                          departure_date=date.fromisoformat(departure) if departure else None), **values))


def calculate(reservations=(), listings=None, today=date(2026, 10, 28), **options):
    return build_occupancy([listing()] if listings is None else listings, reservations,
                           today=today, **options)


def month(result, name='2026-10'):
    return next(row for row in result['months'] if row['month'] == name)


@pytest.mark.parametrize('today', [date(2026, 1, 31), date(2026, 10, 28), date(2024, 2, 29), date(2026, 12, 31)])
def test_twelve_contiguous_calendar_months_with_six_on_each_side(today):
    result = calculate(today=today)
    months = result['months']
    assert len(months) == len({m['month'] for m in months}) == 12
    assert months[0]['month'] == month_start(today, -6).strftime('%Y-%m')
    for previous, current in zip(months, months[1:]):
        assert month_start(date.fromisoformat(previous['month'] + '-01'), 1).strftime('%Y-%m') == current['month']
    assert [m['period'] for m in months] == ['historical'] * 6 + ['on_books'] * 6
    assert [m['is_current_month'] for m in months] == [False] * 6 + [True] + [False] * 5


def test_checkout_excluded_month_boundaries_and_duplicate_overlaps():
    result = calculate([
        booking('2026-09-29', '2026-10-02'),
        booking('2026-10-01', '2026-10-03'),
        booking('2026-10-01', '2026-10-03'),
        booking('2026-10-03', '2026-10-04'),
        booking('2027-03-31', '2027-04-02'),
    ])
    assert month(result, '2026-09')['booked_nights'] == 2
    assert month(result)['booked_nights'] == 3
    assert month(result)['occupancy_rate'] == 9.68
    assert month(result, '2027-03')['booked_nights'] == 1


@pytest.mark.parametrize('status', ['inquiry', 'inquiryPreapproved', 'INQUIRY_NOT_POSSIBLE', ' Cancelled ',
                                    'CANCELED', 'declined', 'expired', 'pending', 'ownerStay', 'blocked', '', None, 'unrecognized'])
def test_non_booking_statuses_do_not_inflate_occupancy(status):
    assert month(calculate([booking(status=status)]))['booked_nights'] == 0


@pytest.mark.parametrize('status', ['new', ' MODIFIED ', 'Confirmed'])
def test_confirmed_booking_statuses(status):
    assert month(calculate([booking(status=status)]))['booked_nights'] == 2


@pytest.mark.parametrize('values', [dict(channel_name='customIcal'), dict(source='iCal'),
                                    dict(cancellation_date=datetime(2026, 9, 1)), dict(is_dates_unspecified=1)])
def test_blocks_cancellations_and_unspecified_dates(values):
    assert month(calculate([booking(**values)]))['booked_nights'] == 0


@pytest.mark.parametrize('arrival,departure', [(None, '2026-10-02'), ('2026-10-01', None),
                                               ('2026-10-01', '2026-10-01'), ('2026-10-03', '2026-10-01')])
def test_invalid_date_intervals(arrival, departure):
    assert month(calculate([booking(arrival, departure)]))['booked_nights'] == 0


def test_inventory_scope_deduplicates_ids_and_excludes_inactive_and_out_of_scope_bookings():
    result = calculate([booking(), booking(listing_id=2), booking(listing_id=99)],
                       [listing(), listing(), listing(2, ' ARCHIVED '), listing(3, 'deleted'), listing(4, 'inactive')])
    assert month(result)['capacity_nights'] == 31
    assert month(result)['listing_count'] == 1
    assert month(result)['booked_nights'] == 2


def test_missing_inventory_and_source_failure_are_unknown_not_zero():
    assert month(calculate(listings=[]))['occupancy_rate'] is None
    assert month(calculate(source_available=False))['occupancy_rate'] is None
    assert month(calculate(source_available=False))['booked_nights'] is None
    assert month(calculate())['occupancy_rate'] == 0


def test_leap_year_capacity_and_full_month_never_exceeds_100():
    result = calculate([booking('2024-01-31', '2024-03-02')] * 2, today=date(2024, 3, 1))
    assert month(result, '2024-02')['capacity_nights'] == 29
    assert month(result, '2024-02')['occupancy_rate'] == 100


def test_service_queries_real_date_bounds_preserves_scope_and_exposes_syncs(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from database.models import Listing, Reservation, SyncLog
    from dashboard.dashboard import service
    engine = create_engine('sqlite://')
    with engine.connect() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    for model in (Listing, Reservation, SyncLog):
        model.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([Listing(listing_id=1), Listing(listing_id=2, status='deleted'),
                         Reservation(reservation_id=1, listing_id=1, status='new', arrival_date=date(2026,10,31), departure_date=date(2026,11,2)),
                         Reservation(reservation_id=2, listing_id=2, status='new', arrival_date=date(2026,10,1), departure_date=date(2026,11,1)),
                         SyncLog(sync_type='reservations', status='success', sync_mode='incremental', completed_at=datetime(2026,10,27)),
                         SyncLog(sync_type='reservations', status='error', completed_at=datetime(2026,10,28))])
        session.commit()
        class Clock:
            @staticmethod
            def now(tz): return datetime(2026,10,28,tzinfo=timezone.utc)
        monkeypatch.setattr(service, 'datetime', Clock)
        svc = service.DashboardService.__new__(service.DashboardService)
        svc.user_id = 1
        svc.main_session = session
        rows = svc._calculate_occupancy(months=6)
        assert len(rows) == 12
        assert rows[6]['booked_nights'] == 1
        assert rows[7]['booked_nights'] == 1
        assert rows[6]['capacity_nights'] == 31
        assert svc.occupancy_metadata['syncs']['reservations']['completed_at'] == '2026-10-27T00:00:00+00:00'
        assert 'listings' not in svc.occupancy_metadata['syncs']
        svc.ticket_session = None
        svc._get_my_tickets = Mock(return_value=[])
        svc._calculate_statistics = Mock(return_value={})
        payload = svc.get_dashboard_data(occupancy_months=6)
        assert len(payload['occupancy']) == 12
        assert payload['occupancy_metadata']['listing_count'] == 1


def test_service_read_failure_keeps_twelve_unknown_months():
    from dashboard.dashboard.service import DashboardService
    svc = DashboardService.__new__(DashboardService)
    svc.user_id = 1
    svc.main_session = Mock()
    svc.main_session.query.side_effect = RuntimeError('source unavailable')
    rows = svc._calculate_occupancy()
    assert len(rows) == 12
    assert all(row['occupancy_rate'] is None for row in rows)
    assert svc.occupancy_metadata['status'] == 'unavailable'
