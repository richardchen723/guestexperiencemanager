import unittest
from datetime import date, timedelta

from dashboard.reviews.query import (
    REVIEW_WINDOW_DAYS,
    _apply_review_lifecycle,
    _create_resolution_ticket_for_review,
    default_bad_review_threshold,
    is_in_review_window,
    is_bad_review_rating,
    normalize_review_rating,
    rate_guest_review_risk,
    review_resolution_window_start,
    review_window_start,
)
from dashboard.portfolio_mapping import portfolio_name_for_listing, portfolio_name_for_tags
from dashboard.tickets.models import ReviewQueueState, Ticket, TicketListing
from database.models import Listing, Reservation, Review


class ReviewWindowTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 7)

    def test_window_contains_today_through_thirteen_days_ago(self):
        self.assertEqual(review_window_start(self.today), date(2026, 7, 25))
        self.assertTrue(is_in_review_window(self.today, self.today))
        self.assertTrue(is_in_review_window(self.today - timedelta(days=13), self.today))

    def test_window_excludes_fourteen_days_ago_and_future_departures(self):
        self.assertFalse(is_in_review_window(self.today - timedelta(days=REVIEW_WINDOW_DAYS), self.today))
        self.assertFalse(is_in_review_window(self.today + timedelta(days=1), self.today))


class ReviewRiskTests(unittest.TestCase):
    def test_strong_complaint_signals_rank_as_high_bad_review_risk(self):
        result = rate_guest_review_risk([
            'The unit was dirty and the air conditioner is not working.',
            'This is unacceptable and I need a refund.',
        ])
        self.assertEqual(result['key'], 'bad_high')
        self.assertLessEqual(result['good_review_likelihood'], 30)

    def test_strong_positive_signals_rank_as_high_good_review_confidence(self):
        result = rate_guest_review_risk([
            'Thank you, the home was amazing and perfect for our family.',
            'We loved the beautiful place and really enjoyed our stay.',
        ])
        self.assertEqual(result['key'], 'good_high')
        self.assertGreater(result['good_review_likelihood'], 82)

    def test_sparse_messages_are_marked_as_low_confidence(self):
        result = rate_guest_review_risk([])
        self.assertEqual(result['key'], 'mixed')
        self.assertEqual(result['confidence'], 'low')


class ReviewResolutionPolicyTests(unittest.TestCase):
    def test_six_month_pool_uses_calendar_months(self):
        self.assertEqual(review_resolution_window_start(date(2026, 8, 7)), date(2026, 2, 7))

    def test_portfolio_defaults_match_operating_standards(self):
        self.assertEqual(default_bad_review_threshold('Enchanted Havens'), 5.0)
        self.assertEqual(default_bad_review_threshold('Urban Stays'), 4.0)
        self.assertEqual(default_bad_review_threshold('crestwood'), 5.0)

    def test_bad_review_threshold_is_strictly_lower(self):
        self.assertTrue(is_bad_review_rating(4.9, 5.0))
        self.assertFalse(is_bad_review_rating(5.0, 5.0))
        self.assertTrue(is_bad_review_rating(3.9, 4.0))
        self.assertFalse(is_bad_review_rating(4.0, 4.0))

    def test_hostaway_ten_point_ratings_normalize_to_five_stars(self):
        self.assertEqual(normalize_review_rating(10), 5.0)
        self.assertEqual(normalize_review_rating(8), 4.0)
        self.assertEqual(normalize_review_rating(5), 2.5)

    def test_portfolio_rule_is_applied_after_rating_normalization(self):
        self.assertTrue(is_bad_review_rating(normalize_review_rating(7), 4.0))
        self.assertFalse(is_bad_review_rating(normalize_review_rating(8), 4.0))
        self.assertTrue(is_bad_review_rating(normalize_review_rating(9), 5.0))

    def test_native_five_star_rating_can_use_its_own_source_scale(self):
        self.assertEqual(normalize_review_rating(4, source_max=5), 4.0)

    def test_new_hostaway_portfolio_tags_map_without_brain_runtime(self):
        self.assertEqual(portfolio_name_for_tags(['San Gabriel Units']), 'LA St Gabe')
        self.assertEqual(portfolio_name_for_tags(['Crestwood']), 'crestwood')
        self.assertEqual(portfolio_name_for_tags(["Crockett's Run"]), 'Middlefork')
        self.assertEqual(portfolio_name_for_listing(558675, []), 'crestwood')


class FakeQuery:
    def filter(self, *args):
        return self

    def first(self):
        return None


class FakeWorkflowSession:
    def __init__(self):
        self.added = []

    def query(self, *args):
        return FakeQuery()

    def add(self, value):
        self.added.append(value)

    def flush(self):
        for value in self.added:
            if isinstance(value, Ticket) and value.ticket_id is None:
                value.ticket_id = 9001


class ReviewLifecycleTests(unittest.TestCase):
    def make_reservation(self):
        listing = Listing(listing_id=10, name='Test home', internal_listing_name='TEST-10')
        return Reservation(
            reservation_id=20,
            listing_id=10,
            guest_name='Test Guest',
            listing=listing,
        )

    def test_five_star_two_sided_review_closes_without_ticket(self):
        session = FakeWorkflowSession()
        reservation = self.make_reservation()
        review = Review(review_id=30, listing_id=10, overall_rating=10, origin='Guest')
        state = ReviewQueueState(reservation_id=20, listing_id=10, host_reviewed=True)

        outcome, state = _apply_review_lifecycle(session, reservation, review, None, state, 1)

        self.assertEqual(outcome, 'closed')
        self.assertIsNotNone(state.closed_at)
        self.assertFalse(any(isinstance(value, Ticket) for value in session.added))

    def test_lower_rating_two_sided_review_creates_resolution_ticket(self):
        session = FakeWorkflowSession()
        reservation = self.make_reservation()
        review = Review(review_id=31, listing_id=10, overall_rating=8, origin='Guest', review_text='Needs follow-up')
        state = ReviewQueueState(reservation_id=20, listing_id=10, host_reviewed=True)

        outcome, state = _apply_review_lifecycle(session, reservation, review, None, state, 1)

        tickets = [value for value in session.added if isinstance(value, Ticket)]
        self.assertEqual(outcome, 'resolution')
        self.assertEqual(state.resolution_ticket_id, 9001)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_type, 'review_resolution')
        self.assertTrue(any(isinstance(value, TicketListing) for value in session.added))

    def test_historical_review_creates_ticket_without_host_review(self):
        session = FakeWorkflowSession()
        listing = Listing(listing_id=10, name='Test home', internal_listing_name='TEST-10')
        review = Review(
            review_id=32,
            listing_id=10,
            reservation_id=20,
            overall_rating=6,
            origin='Guest',
            review_text='Needs service recovery',
        )

        ticket = _create_resolution_ticket_for_review(
            session,
            review,
            current_user_id=1,
            listing=listing,
            reservation=None,
        )

        self.assertEqual(ticket.ticket_id, 9001)
        self.assertEqual(ticket.source_review_id, 32)
        self.assertEqual(ticket.source_reservation_id, 20)
        self.assertEqual(ticket.workflow_stage, 'New')


if __name__ == '__main__':
    unittest.main()
