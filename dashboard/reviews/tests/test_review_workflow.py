import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from dashboard.reviews.query import (
    REVIEW_WINDOW_DAYS,
    _apply_review_lifecycle,
    _create_resolution_ticket_for_review,
    _review_for_origin,
    add_review_resolution_note,
    default_bad_review_threshold,
    is_in_review_window,
    is_bad_review_rating,
    is_review_chase_risk_eligible,
    hostaway_url_for_reservation,
    normalize_review_rating,
    rate_guest_review_risk,
    should_offer_review_chase,
    update_review_resolution,
    review_resolution_window_start,
    review_window_start,
)
from dashboard.portfolio_mapping import portfolio_name_for_listing, portfolio_name_for_tags
from dashboard.tickets.models import (
    REVIEW_RESOLUTION_STAGE_DEFINITIONS,
    REVIEW_RESOLUTION_STAGES,
    ReviewQueueState,
    Ticket,
    TicketListing,
    normalize_review_resolution_stage,
)
from database.models import Conversation, Listing, Reservation, Review


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

    def test_review_chase_excludes_only_high_and_elevated_bad_review_risk(self):
        self.assertFalse(is_review_chase_risk_eligible({'key': 'bad_high'}))
        self.assertFalse(is_review_chase_risk_eligible({'key': 'bad_elevated'}))
        self.assertTrue(is_review_chase_risk_eligible({'key': 'mixed'}))
        self.assertTrue(is_review_chase_risk_eligible({'key': 'good_likely'}))
        self.assertTrue(is_review_chase_risk_eligible({'key': 'good_high'}))

    def test_review_chase_is_not_offered_after_guest_submission(self):
        self.assertTrue(should_offer_review_chase({'key': 'mixed'}, guest_reviewed=False))
        self.assertFalse(should_offer_review_chase({'key': 'mixed'}, guest_reviewed=True))


class ReviewHostawayLinkTests(unittest.TestCase):
    def test_conversation_link_opens_the_latest_hostaway_thread(self):
        reservation = Reservation(reservation_id=20, listing_id=10)
        reservation.conversations = [
            Conversation(conversation_id=30, last_message_at=datetime(2026, 8, 1)),
            Conversation(conversation_id=31, last_message_at=datetime(2026, 8, 3)),
        ]

        result = hostaway_url_for_reservation(reservation)

        self.assertEqual(result['destination'], 'conversation')
        self.assertEqual(result['url'], 'https://dashboard.hostaway.com/messages/inbox/31')

    def test_reservation_link_is_used_when_no_conversation_exists(self):
        reservation = Reservation(reservation_id=20, listing_id=10)
        reservation.conversations = []

        result = hostaway_url_for_reservation(reservation)

        self.assertEqual(result['destination'], 'reservation')
        self.assertEqual(result['url'], 'https://dashboard.hostaway.com/reservations/20')


class ReviewResolutionPolicyTests(unittest.TestCase):
    def test_resolution_workflow_uses_the_service_recovery_schema(self):
        self.assertEqual(REVIEW_RESOLUTION_STAGES, [
            'New',
            'Outreach Initiated',
            'Require Follow Up',
            'Responded – Agreed to Remove',
            'Responded – Declined',
            'No Response',
            'Resolved',
        ])
        self.assertEqual(
            [definition['step'] for definition in REVIEW_RESOLUTION_STAGE_DEFINITIONS],
            ['1', '2', '3', '4', '4', '4', '5'],
        )

    def test_retired_resolution_stages_are_mapped_without_losing_tickets(self):
        self.assertEqual(normalize_review_resolution_stage('Reviewing'), 'Outreach Initiated')
        self.assertEqual(normalize_review_resolution_stage('Action in progress'), 'Require Follow Up')
        self.assertEqual(normalize_review_resolution_stage('Guest follow-up'), 'Require Follow Up')
        self.assertEqual(normalize_review_resolution_stage(None), 'New')

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
        self.assertEqual(portfolio_name_for_tags(["Crockett's Run"]), 'Crockett’s Run')
        self.assertEqual(portfolio_name_for_tags(['Crocketts Run']), 'Crockett’s Run')
        self.assertEqual(portfolio_name_for_tags(['Middlefork Ridge']), 'Middlefork')
        self.assertEqual(portfolio_name_for_listing(558675, []), 'crestwood')


class ResolutionWorkflowSession:
    def __init__(self, ticket=None):
        self.ticket = ticket
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def query(self, *args):
        session = self

        class Query:
            def filter(self, *filters):
                return self

            def first(self):
                return session.ticket

        return Query()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ReviewResolutionEditingTests(unittest.TestCase):
    @patch('dashboard.reviews.query.get_review_resolution_detail')
    @patch('dashboard.reviews.query.get_workflow_session')
    def test_case_fields_can_be_edited_without_overwriting_the_guest_review(self, get_session_mock, detail_mock):
        ticket = Ticket(
            ticket_id=91,
            ticket_type='review_resolution',
            title='Original case title',
            description='Original guest review text',
            workflow_stage='New',
            status='Open',
            priority='Medium',
            created_by=1,
        )
        session = ResolutionWorkflowSession(ticket)
        get_session_mock.return_value = session
        detail_mock.return_value = {'ticket_id': 91, 'stage': 'Require Follow Up'}

        result = update_review_resolution(91, {
            'title': '  Call guest about removal  ',
            'stage': 'Require Follow Up',
            'priority': 'High',
            'due_date': '2026-08-20',
        })

        self.assertEqual(result['stage'], 'Require Follow Up')
        self.assertEqual(ticket.title, 'Call guest about removal')
        self.assertEqual(ticket.workflow_stage, 'Require Follow Up')
        self.assertEqual(ticket.status, 'In Progress')
        self.assertEqual(ticket.priority, 'High')
        self.assertEqual(ticket.due_date, date(2026, 8, 20))
        self.assertEqual(ticket.description, 'Original guest review text')
        self.assertTrue(session.committed)

    def test_case_edit_rejects_unknown_stages(self):
        with self.assertRaisesRegex(ValueError, 'Invalid review resolution stage'):
            update_review_resolution(91, {'stage': 'Waiting somewhere'})

    @patch('dashboard.reviews.query.add_ticket_comment')
    @patch('dashboard.reviews.query.get_workflow_session')
    def test_operator_note_is_appended_to_the_ticket_history(self, get_session_mock, add_comment_mock):
        get_session_mock.return_value = ResolutionWorkflowSession(ticket=(91,))
        comment = Mock()
        comment.to_dict.return_value = {
            'comment_id': 18,
            'ticket_id': 91,
            'comment_text': 'Guest returned our call.',
        }
        add_comment_mock.return_value = comment

        result = add_review_resolution_note(91, 7, '  Guest returned our call.  ')

        self.assertEqual(result['comment_id'], 18)
        add_comment_mock.assert_called_once_with(91, 7, 'Guest returned our call.')


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
        state = ReviewQueueState(
            reservation_id=20,
            listing_id=10,
            host_reviewed=True,
            host_reviewed_by=1,
        )

        outcome, state = _apply_review_lifecycle(session, reservation, review, None, state, 1)

        self.assertEqual(outcome, 'closed')
        self.assertIsNotNone(state.closed_at)
        self.assertFalse(any(isinstance(value, Ticket) for value in session.added))

    def test_lower_rating_two_sided_review_creates_resolution_ticket(self):
        session = FakeWorkflowSession()
        reservation = self.make_reservation()
        review = Review(review_id=31, listing_id=10, overall_rating=8, origin='Guest', review_text='Needs follow-up')
        state = ReviewQueueState(
            reservation_id=20,
            listing_id=10,
            host_reviewed=True,
            host_reviewed_by=1,
        )

        outcome, state = _apply_review_lifecycle(session, reservation, review, None, state, 1)

        tickets = [value for value in session.added if isinstance(value, Ticket)]
        self.assertEqual(outcome, 'resolution')
        self.assertEqual(state.resolution_ticket_id, 9001)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].ticket_type, 'review_resolution')
        self.assertTrue(any(isinstance(value, TicketListing) for value in session.added))

    def test_pending_host_review_does_not_count_and_reopens_stale_synced_state(self):
        session = FakeWorkflowSession()
        reservation = self.make_reservation()
        guest_review = Review(
            review_id=31,
            listing_id=10,
            overall_rating=10,
            origin='Guest',
            status='submitted',
        )
        pending_host_review = Review(
            review_id=32,
            listing_id=10,
            origin='Host',
            status='pending',
        )
        reservation.reviews = [guest_review, pending_host_review]
        state = ReviewQueueState(
            reservation_id=20,
            listing_id=10,
            host_reviewed=True,
            host_review_id=32,
            host_reviewed_at=datetime(2026, 8, 2),
            closed_at=datetime(2026, 8, 2),
        )

        host_review = _review_for_origin(reservation, 'Host')
        outcome, state = _apply_review_lifecycle(
            session,
            reservation,
            guest_review,
            host_review,
            state,
            1,
        )

        self.assertIsNone(host_review)
        self.assertIsNone(outcome)
        self.assertFalse(state.host_reviewed)
        self.assertIsNone(state.host_review_id)
        self.assertIsNone(state.host_reviewed_at)
        self.assertIsNone(state.closed_at)

    def test_pending_host_review_does_not_override_manual_confirmation(self):
        session = FakeWorkflowSession()
        reservation = self.make_reservation()
        guest_review = Review(
            review_id=33,
            listing_id=10,
            overall_rating=10,
            origin='Guest',
            status='submitted',
        )
        state = ReviewQueueState(
            reservation_id=20,
            listing_id=10,
            host_reviewed=True,
            host_reviewed_by=7,
        )

        outcome, state = _apply_review_lifecycle(
            session,
            reservation,
            guest_review,
            None,
            state,
            1,
        )

        self.assertEqual(outcome, 'closed')
        self.assertTrue(state.host_reviewed)
        self.assertEqual(state.host_reviewed_by, 7)

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
