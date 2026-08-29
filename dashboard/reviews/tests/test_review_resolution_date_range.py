from datetime import date
from unittest.mock import Mock, patch

from flask import Flask

from dashboard.reviews import routes
from dashboard.reviews.query import (
    get_review_resolutions,
    review_resolution_date_range,
)


class EmptyQuery:
    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class EmptySession:
    def query(self, *args, **kwargs):
        return EmptyQuery()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_default_resolution_range_remains_six_months():
    assert review_resolution_date_range(today=date(2026, 8, 29)) == (
        date(2026, 2, 28),
        date(2026, 8, 29),
        False,
    )


def test_custom_resolution_range_is_inclusive_and_complete():
    assert review_resolution_date_range(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
        today=date(2026, 8, 29),
    ) == (date(2026, 5, 1), date(2026, 5, 31), True)


def test_custom_resolution_range_requires_both_dates_and_order():
    try:
        review_resolution_date_range(start_date=date(2026, 5, 1))
        raise AssertionError('Expected an incomplete range to fail')
    except ValueError as error:
        assert str(error) == 'Choose both a From date and a To date'

    try:
        review_resolution_date_range(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 5, 31),
        )
        raise AssertionError('Expected a reversed range to fail')
    except ValueError as error:
        assert str(error) == 'To date cannot be earlier than From date'


@patch('dashboard.reviews.query._historical_guest_reviews', return_value=[])
@patch('dashboard.reviews.query.get_workflow_session', return_value=EmptySession())
@patch('dashboard.reviews.query.get_session', return_value=EmptySession())
def test_resolution_payload_scopes_all_results_to_custom_dates(
    _main_session,
    _workflow_session,
    historical_reviews,
):
    payload = get_review_resolutions(
        7,
        start_date=date(2026, 4, 3),
        end_date=date(2026, 4, 9),
    )

    historical_reviews.assert_called_once()
    assert historical_reviews.call_args.args[1:] == (date(2026, 4, 3), date(2026, 4, 9))
    assert payload['summary'] == {'total': 0, 'open': 0, 'resolved': 0}
    assert payload['lookback'] == {
        'months': 6,
        'start_date': '2026-04-03',
        'end_date': '2026-04-09',
        'is_custom': True,
    }
    assert all(rule['review_count'] == 0 for rule in payload['rules'])
    assert all(lane['reviews'] == [] for lane in payload['lanes'])


def test_resolution_api_forwards_valid_dates_and_rejects_reversed_dates():
    app = Flask(__name__)
    current_user = Mock(user_id=7)
    payload = {
        'stages': [],
        'stage_definitions': [],
        'lanes': [],
        'rules': [],
        'summary': {'total': 0, 'open': 0, 'resolved': 0},
        'lookback': {},
    }

    with (
        patch.object(routes, 'get_current_user', return_value=current_user),
        patch.object(routes, 'get_all_users', return_value=[]),
        patch.object(routes, 'get_review_resolutions', return_value=payload) as get_resolutions,
        app.test_request_context('/reviews/api/resolutions?start_date=2026-04-03&end_date=2026-04-09'),
    ):
        response, status = routes.api_review_resolutions.__wrapped__()
        assert status == 200
        assert response.get_json()['operators'] == []
        get_resolutions.assert_called_once_with(
            7,
            start_date=date(2026, 4, 3),
            end_date=date(2026, 4, 9),
        )

    with (
        patch.object(routes, 'get_current_user', return_value=current_user),
        patch.object(
            routes,
            'get_review_resolutions',
            side_effect=ValueError('To date cannot be earlier than From date'),
        ),
        app.test_request_context('/reviews/api/resolutions?start_date=2026-04-09&end_date=2026-04-03'),
    ):
        response, status = routes.api_review_resolutions.__wrapped__()
        assert status == 400
        assert response.get_json() == {'error': 'To date cannot be earlier than From date'}
