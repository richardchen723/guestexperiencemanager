import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.reviews.query import (
    REVIEW_WINDOW_DAYS,
    _apply_review_lifecycle,
    _create_resolution_ticket_for_review,
    _review_for_origin,
    add_review_resolution_note,
    default_bad_review_threshold,
    effective_review_risk,
    get_review_queue,
    is_in_review_window,
    is_reservation_in_review_window,
    is_bad_review_rating,
    is_review_chase_risk_eligible,
    hostaway_url_for_reservation,
    normalize_review_rating,
    rate_guest_review_risk,
    require_reservation_in_review_window,
    reservation_review_window_status,
    review_channel_name,
    should_offer_review_chase,
    update_review_resolution,
    update_review_risk_override,
    review_resolution_window_start,
    review_window_start,
    serialize_review_conversation,
)
from dashboard.reviews.timezone import (
    listing_timezone,
    review_window_bounds,
    scheduled_checkout_at_utc,
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


class ExactReviewCheckoutTests(unittest.TestCase):
    @staticmethod
    def reservation(
        departure_date=date(2026, 8, 28),
        *,
        checkout_time=11,
        timezone_name=None,
        city='Atlanta',
        state='GA',
    ):
        listing = SimpleNamespace(
            check_out_time=checkout_time,
            timezone_name=timezone_name,
            city=city,
            state=state,
        )
        return SimpleNamespace(departure_date=departure_date, listing=listing)

    def test_review_window_stays_closed_until_exact_eastern_checkout(self):
        reservation = self.reservation()
        before_checkout = datetime(2026, 8, 28, 14, 59, 59, tzinfo=timezone.utc)
        at_checkout = datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)

        self.assertEqual(
            reservation_review_window_status(reservation, before_checkout),
            'pre_checkout',
        )
        self.assertFalse(is_reservation_in_review_window(reservation, before_checkout))
        with self.assertRaisesRegex(ValueError, 'has not checked out yet'):
            require_reservation_in_review_window(reservation, before_checkout)
        self.assertTrue(is_reservation_in_review_window(reservation, at_checkout))

    def test_property_location_controls_checkout_when_hostaway_timezone_is_missing(self):
        reservation = self.reservation(
            checkout_time=10,
            timezone_name=None,
            city='San Gabriel',
            state='CA',
        )

        self.assertEqual(
            listing_timezone(reservation.listing),
            ('America/Los_Angeles', 'state_fallback'),
        )
        self.assertEqual(
            scheduled_checkout_at_utc(reservation, reservation.listing),
            datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        )

    def test_unknown_property_location_uses_eastern_application_fallback(self):
        reservation = self.reservation(
            checkout_time=10,
            timezone_name=None,
            city=None,
            state=None,
        )

        self.assertEqual(
            listing_timezone(reservation.listing),
            ('America/New_York', 'application_fallback'),
        )
        self.assertEqual(
            scheduled_checkout_at_utc(reservation, reservation.listing),
            datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc),
        )

    def test_expiration_keeps_the_same_local_clock_across_daylight_saving(self):
        reservation = self.reservation(
            departure_date=date(2026, 10, 25),
            checkout_time=11,
            timezone_name='America/New_York',
        )
        checkout_at, expires_at = review_window_bounds(
            reservation,
            reservation.listing,
            window_days=REVIEW_WINDOW_DAYS,
        )

        self.assertEqual(checkout_at, datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(expires_at, datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc))
        self.assertTrue(is_reservation_in_review_window(
            reservation,
            expires_at - timedelta(seconds=1),
        ))
        self.assertFalse(is_reservation_in_review_window(reservation, expires_at))

    @patch('dashboard.reviews.query._serialize_queue_card')
    @patch('dashboard.reviews.query._apply_review_lifecycle')
    @patch('dashboard.reviews.query.get_workflow_session')
    @patch('dashboard.reviews.query.get_session')
    def test_queue_does_not_reconcile_or_render_precheckout_reservations(
        self,
        main_session_factory,
        workflow_session_factory,
        lifecycle_mock,
        serialize_mock,
    ):
        before_checkout = self.reservation()
        before_checkout.reservation_id = 1
        before_checkout.reviews = []
        checked_out = self.reservation(departure_date=date(2026, 8, 27))
        checked_out.reservation_id = 2
        checked_out.reviews = []

        main_session = Mock()
        query = Mock()
        main_session.query.return_value.join.return_value.filter.return_value = query
        query.options.return_value.distinct.return_value.all.return_value = [
            before_checkout,
            checked_out,
        ]
        main_session_factory.return_value = main_session

        workflow_session = Mock()
        workflow_query = Mock()
        workflow_session.query.return_value = workflow_query
        workflow_query.filter.return_value.all.side_effect = [[], []]
        workflow_session_factory.return_value = workflow_session
        lifecycle_mock.return_value = (None, None)
        serialize_mock.return_value = {
            'portfolio': 'Test',
            'risk': {'order': 2},
            'days_remaining': 13,
            'guest_name': 'Checked Out Guest',
            'departure_date': '2026-08-27',
            'host_reviewed': False,
            'guest_reviewed': False,
        }

        result = get_review_queue(
            current_user_id=7,
            reference_time=datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(result['summary']['total'], 1)
        self.assertIs(lifecycle_mock.call_args.args[1], checked_out)
        self.assertIs(serialize_mock.call_args.args[0], checked_out)


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

    def test_manual_severity_survives_later_ai_signal_changes(self):
        reservation = SimpleNamespace(conversations=[SimpleNamespace(messages=[SimpleNamespace(
            sender_type='guest',
            is_incoming=True,
            content_preview='Thank you, the home was amazing and perfect.',
        )])])
        state = SimpleNamespace(
            risk_override_key='bad_high',
            risk_overridden_by=None,
            risk_overridden_at=datetime(2026, 8, 29, 18, 30),
            ai_risk_key='mixed',
            ai_risk_confidence='low',
            ai_risk_good_review_likelihood=58,
            ai_risk_reasons=['No strong sentiment signals in recent guest messages'],
        )

        result = effective_review_risk(reservation, state)

        self.assertEqual(result['key'], 'bad_high')
        self.assertEqual(result['short_label'], 'Red flags')
        self.assertEqual(result['source'], 'manual')
        self.assertEqual(result['ai']['key'], 'mixed')
        self.assertEqual(result['ai']['confidence'], 'low')

    @patch('dashboard.reviews.query.get_user_by_id')
    @patch('dashboard.reviews.query.get_workflow_session')
    @patch('dashboard.reviews.query.get_session')
    def test_override_captures_ai_snapshot_and_operator(
        self,
        main_session_factory,
        workflow_session_factory,
        get_user_mock,
    ):
        listing = SimpleNamespace(
            listing_id=10,
            check_out_time=11,
            timezone_name='America/New_York',
            city='Atlanta',
            state='GA',
        )
        reservation = SimpleNamespace(
            reservation_id=44,
            listing_id=10,
            departure_date=date(2026, 8, 28),
            listing=listing,
            conversations=[],
        )
        main_session = Mock()
        main_session.query.return_value.filter.return_value.options.return_value.first.return_value = reservation
        main_session_factory.return_value = main_session
        workflow_session = Mock()
        workflow_session.query.return_value.filter.return_value.first.return_value = None
        workflow_session_factory.return_value = workflow_session
        get_user_mock.return_value = SimpleNamespace(name='Richard Chen', email='yunhang.chen@gmail.com')

        result = update_review_risk_override(
            44,
            7,
            risk_key='bad_high',
            reference_time=datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc),
        )

        state = workflow_session.add.call_args.args[0]
        self.assertEqual(state.risk_override_key, 'bad_high')
        self.assertEqual(state.risk_overridden_by, 7)
        self.assertEqual(state.ai_risk_key, 'mixed')
        self.assertEqual(state.ai_risk_confidence, 'low')
        self.assertEqual(result['risk']['source'], 'manual')
        self.assertEqual(result['risk']['override']['updated_by']['name'], 'Richard Chen')
        workflow_session.commit.assert_called_once_with()

    @patch('dashboard.reviews.query.get_workflow_session')
    @patch('dashboard.reviews.query.get_session')
    def test_restore_clears_override_and_uses_fresh_ai_result(
        self,
        main_session_factory,
        workflow_session_factory,
    ):
        listing = SimpleNamespace(
            listing_id=10,
            check_out_time=11,
            timezone_name='America/New_York',
            city='Atlanta',
            state='GA',
        )
        reservation = SimpleNamespace(
            reservation_id=44,
            listing_id=10,
            departure_date=date(2026, 8, 28),
            listing=listing,
            conversations=[],
        )
        state = ReviewQueueState(
            reservation_id=44,
            listing_id=10,
            risk_override_key='bad_high',
            risk_overridden_by=7,
            risk_overridden_at=datetime(2026, 8, 29, 18, 30),
            ai_risk_key='bad_elevated',
            ai_risk_confidence='medium',
        )
        main_session = Mock()
        main_session.query.return_value.filter.return_value.options.return_value.first.return_value = reservation
        main_session_factory.return_value = main_session
        workflow_session = Mock()
        workflow_session.query.return_value.filter.return_value.first.return_value = state
        workflow_session_factory.return_value = workflow_session

        result = update_review_risk_override(
            44,
            7,
            restore_ai=True,
            reference_time=datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc),
        )

        self.assertIsNone(state.risk_override_key)
        self.assertIsNone(state.risk_overridden_by)
        self.assertIsNone(state.risk_overridden_at)
        self.assertTrue(result['restored_ai'])
        self.assertEqual(result['risk']['source'], 'ai')
        self.assertEqual(result['risk']['key'], 'mixed')


class ReviewConversationContextTests(unittest.TestCase):
    def test_complete_thread_combines_conversations_in_chronological_order(self):
        early_guest_message = SimpleNamespace(
            message_id=1,
            sender_type='guest',
            sender_name='Ketan Konanur',
            is_incoming=1,
            message_type='incoming',
            content_preview='The full guest message remains available for the human reviewer.',
            has_attachment=0,
            created_at=datetime(2026, 8, 12, 14, 5),
        )
        team_reply = SimpleNamespace(
            message_id=2,
            sender_type='host',
            sender_name='Maya from Guest Services',
            is_incoming=0,
            message_type='outgoing',
            content_preview='Thank you for letting us know. The team is checking this now.',
            has_attachment=1,
            created_at=datetime(2026, 8, 12, 14, 9),
        )
        reservation = SimpleNamespace(
            guest_name='Ketan Konanur',
            guest=None,
            conversations=[
                SimpleNamespace(
                    conversation_id=91,
                    communication_type='Airbnb',
                    messages=[team_reply],
                ),
                SimpleNamespace(
                    conversation_id=90,
                    communication_type='Airbnb',
                    messages=[early_guest_message, team_reply],
                ),
            ],
        )

        result = serialize_review_conversation(reservation)

        self.assertEqual([message['message_id'] for message in result['messages']], [1, 2])
        self.assertEqual([message['direction'] for message in result['messages']], ['guest', 'team'])
        self.assertEqual(result['messages'][0]['content'], early_guest_message.content_preview)
        self.assertTrue(result['messages'][1]['has_attachment'])
        self.assertEqual(result['message_count'], 2)
        self.assertEqual(result['guest_message_count'], 1)
        self.assertEqual(result['team_message_count'], 1)
        self.assertEqual(result['conversation_count'], 2)
        self.assertEqual(result['communication_types'], ['Airbnb'])
        self.assertEqual(result['display_timezone'], 'America/New_York')


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

    def test_resolution_channel_prefers_the_review_source(self):
        review = Review(channel_name='airbnbOfficial')
        reservation = Reservation(channel_name='Homeaway', source='direct')

        self.assertEqual(review_channel_name(review, reservation), 'airbnbOfficial')

    def test_resolution_channel_falls_back_to_reservation_then_direct(self):
        self.assertEqual(
            review_channel_name(Review(), Reservation(channel_name='bookingcom')),
            'bookingcom',
        )
        self.assertEqual(review_channel_name(Review(), Reservation()), 'Direct')

    def test_new_hostaway_portfolio_tags_map_without_brain_runtime(self):
        self.assertEqual(portfolio_name_for_tags(['San Gabriel Units']), 'LA St Gabe')
        self.assertEqual(portfolio_name_for_tags(['Crestwood']), 'crestwood')
        self.assertEqual(portfolio_name_for_tags(["Crockett's Run"]), 'Crockett’s Run')
        self.assertEqual(portfolio_name_for_tags(['Crocketts Run']), 'Crockett’s Run')
        self.assertEqual(portfolio_name_for_tags(['Middlefork Ridge']), 'Middlefork')
        self.assertEqual(portfolio_name_for_tags(['Smoky Cabins']), 'Smoky Cabins')
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
