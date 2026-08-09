import unittest
from unittest.mock import patch

from flask import Flask

from dashboard.tickets import routes
from dashboard.tickets.routes import _parse_ticket_statuses


class RecordingQuery:
    def __init__(self):
        self.criteria = []

    def filter(self, *criteria):
        self.criteria.extend(criteria)
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return []


class RecordingSession:
    def __init__(self):
        self.query_recorder = RecordingQuery()
        self.closed = False

    def query(self, *_args):
        return self.query_recorder

    def close(self):
        self.closed = True


class TicketStatusFilterTests(unittest.TestCase):
    def test_preserves_resolved_and_closed_statuses(self):
        self.assertEqual(
            _parse_ticket_statuses('Resolved,Closed'),
            ['Resolved', 'Closed'],
        )

    def test_preserves_active_statuses_and_request_order(self):
        self.assertEqual(
            _parse_ticket_statuses('Blocked, Open,In Progress'),
            ['Blocked', 'Open', 'In Progress'],
        )

    def test_rejects_invalid_or_explicitly_empty_status_selection(self):
        self.assertEqual(_parse_ticket_statuses('__none__'), [])
        self.assertEqual(_parse_ticket_statuses(''), [])

    def test_distinguishes_no_filter_from_empty_filter(self):
        self.assertIsNone(_parse_ticket_statuses(None))

    def test_api_applies_resolved_status_filter(self):
        app = Flask(__name__)
        ticket_session = RecordingSession()
        main_session = RecordingSession()

        with app.test_request_context('/tickets/api/tickets?status=Resolved'):
            with (
                patch.object(routes, 'get_session', return_value=ticket_session),
                patch.object(routes, 'get_main_session', return_value=main_session),
            ):
                response = routes.api_list_tickets.__wrapped__()

        status_parameters = []
        for criterion in ticket_session.query_recorder.criteria:
            for value in criterion.compile().params.values():
                if isinstance(value, list):
                    status_parameters.append(value)

        self.assertIn(['Resolved'], status_parameters)
        self.assertEqual(response.get_json(), [])
        self.assertTrue(ticket_session.closed)
        self.assertTrue(main_session.closed)

    def test_api_returns_no_tickets_for_explicit_empty_selection(self):
        app = Flask(__name__)
        ticket_session = RecordingSession()
        main_session = RecordingSession()

        with app.test_request_context('/tickets/api/tickets?status=__none__'):
            with (
                patch.object(routes, 'get_session', return_value=ticket_session),
                patch.object(routes, 'get_main_session', return_value=main_session),
            ):
                response = routes.api_list_tickets.__wrapped__()

        self.assertEqual(response.get_json(), [])
        self.assertTrue(ticket_session.closed)
        self.assertTrue(main_session.closed)


if __name__ == '__main__':
    unittest.main()
