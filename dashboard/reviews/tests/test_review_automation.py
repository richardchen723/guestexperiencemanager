import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.reviews.automation import (
    DEFAULT_CHASE_MESSAGE_TEMPLATE,
    DEFAULT_HOST_REVIEW_TEMPLATE,
    DryRunReviewAutomationGateway,
    get_review_automation_preview,
    HostReviewPublishingUnavailable,
    host_review_destination,
    perform_review_automation_action,
    render_review_template,
    validate_review_template,
)
from dashboard.tickets.models import REVIEW_ACTION_CHASE, REVIEW_ACTION_HOST
from sync.api_client import HostawayAPIClient
from database.models import Reservation


class ReviewTemplateTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            'guest_name': 'Taylor Morgan',
            'guest_first_name': 'Taylor',
            'property_name': 'Crestwood Cottage',
            'portfolio_name': 'Crestwood',
        }

    def test_chase_template_renders_property_and_guest(self):
        rendered = render_review_template(DEFAULT_CHASE_MESSAGE_TEMPLATE, self.values)

        self.assertIn('Hi Taylor', rendered)
        self.assertIn('Crestwood Cottage', rendered)
        self.assertIn('great review', rendered)
        self.assertIn('private message', rendered)

    def test_host_review_template_contains_operator_requirements(self):
        rendered = render_review_template(DEFAULT_HOST_REVIEW_TEMPLATE, self.values)

        self.assertIn('Taylor was a great guest', rendered)
        self.assertNotIn('Taylor Morgan', rendered)
        self.assertIn('respectful', rendered)
        self.assertIn('communicative', rendered)
        self.assertIn('welcome Taylor back', rendered)
        self.assertIn('recommend them to other hosts', rendered)

    def test_unknown_placeholders_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown placeholder'):
            validate_review_template('Thank you {guest_namme} for being a wonderful guest.')


class HostReviewDestinationTests(unittest.TestCase):
    def test_airbnb_confirmation_code_opens_reservation(self):
        destination = host_review_destination(Reservation(
            channel_name='airbnbOfficial',
            confirmation_code='HMABC12345',
        ))

        self.assertTrue(destination['supported'])
        self.assertTrue(destination['direct'])
        self.assertEqual(destination['platform'], 'Airbnb')
        self.assertTrue(destination['url'].endswith('/HMABC12345'))

    def test_airbnb_without_confirmation_code_uses_completed_stays(self):
        destination = host_review_destination(Reservation(channel_name='airbnbOfficial'))

        self.assertTrue(destination['supported'])
        self.assertFalse(destination['direct'])
        self.assertIn('/hosting/reservations/completed', destination['url'])

    def test_vrbo_uses_owner_reviews_queue(self):
        destination = host_review_destination(Reservation(channel_name='Homeaway'))

        self.assertTrue(destination['supported'])
        self.assertEqual(destination['platform'], 'Vrbo')
        self.assertIn('/owner/reviews', destination['url'])

    def test_booking_com_does_not_offer_host_review(self):
        destination = host_review_destination(Reservation(channel_name='bookingcom'))

        self.assertFalse(destination['supported'])
        self.assertIsNone(destination['url'])
        self.assertIn('does not support', destination['note'])


class DryRunAutomationTests(unittest.TestCase):
    def preview(self, action_type):
        return {
            'action_type': action_type,
            'reservation_id': 44,
            'listing_id': 55,
            'listing_name': 'Test Home',
            'portfolio': 'Test Portfolio',
            'guest_name': 'Test Guest',
            'channel_name': 'airbnbOfficial',
            'conversation_id': 66,
            'communication_type': 'channel',
            'content': 'A sufficiently long test message for the guest.',
            'mode': 'dry_run',
            'simulated': True,
            'execution_enabled': True,
            'live_host_review_supported': False,
            'assisted_host_review': action_type == REVIEW_ACTION_HOST,
            'review_destination': None,
            'capability_note': 'Nothing will be sent.',
        }
    @patch('dashboard.reviews.automation.get_review_automation_preview')
    def test_chase_simulation_never_instantiates_hostaway_client(self, preview_mock):
        preview_mock.return_value = self.preview(REVIEW_ACTION_CHASE)
        gateway = DryRunReviewAutomationGateway()

        with patch('dashboard.reviews.automation.HostawayAPIClient') as hostaway_client:
            result = perform_review_automation_action(
                44,
                REVIEW_ACTION_CHASE,
                'A sufficiently long edited message for the guest.',
                7,
                gateway=gateway,
            )

        self.assertEqual(result['status'], 'simulated')
        self.assertTrue(result['provider_reference'].startswith('dry-run-'))
        hostaway_client.assert_not_called()

    @patch('dashboard.reviews.automation.get_review_automation_preview')
    def test_assisted_host_review_never_posts_from_the_application(self, preview_mock):
        preview = self.preview(REVIEW_ACTION_HOST)
        preview.update({
            'execution_enabled': False,
            'capability_note': 'Copy and post this review manually.',
        })
        preview_mock.return_value = preview

        with self.assertRaises(HostReviewPublishingUnavailable):
            perform_review_automation_action(
                44,
                REVIEW_ACTION_HOST,
                'Test Guest was respectful and welcome back any time.',
                7,
            )

    @patch('dashboard.reviews.automation.get_review_automation_preview')
    def test_live_host_review_fails_closed_without_supported_api(self, preview_mock):
        preview = self.preview(REVIEW_ACTION_HOST)
        preview.update({
            'mode': 'live',
            'simulated': False,
            'execution_enabled': False,
            'capability_note': 'Live review publishing is locked.',
        })
        preview_mock.return_value = preview

        with self.assertRaises(HostReviewPublishingUnavailable):
            perform_review_automation_action(
                44,
                REVIEW_ACTION_HOST,
                'Test Guest was respectful and welcome back any time.',
                7,
            )


class ReviewAutomationCheckoutGuardTests(unittest.TestCase):
    @patch('dashboard.reviews.automation.get_workflow_session')
    @patch('dashboard.reviews.automation.get_session')
    def test_preview_cannot_bypass_precheckout_guard(
        self,
        main_session_factory,
        workflow_session_factory,
    ):
        listing = SimpleNamespace(
            check_out_time=11,
            timezone_name='America/New_York',
            city='Atlanta',
            state='GA',
        )
        reservation = SimpleNamespace(
            departure_date=date(2026, 8, 28),
            listing=listing,
        )
        main_session = Mock()
        main_session.query.return_value.filter.return_value.options.return_value.first.return_value = reservation
        main_session_factory.return_value = main_session
        workflow_session_factory.return_value = Mock()

        with self.assertRaisesRegex(ValueError, 'has not checked out yet'):
            get_review_automation_preview(
                44,
                REVIEW_ACTION_CHASE,
                7,
                reference_time=datetime(2026, 8, 28, 14, 59, tzinfo=timezone.utc),
            )


class LiveGuestMessageTests(unittest.TestCase):
    @patch('dashboard.reviews.automation.get_workflow_session')
    @patch('dashboard.reviews.automation.get_review_automation_preview')
    def test_live_chase_uses_gateway_once_and_records_sent_action(self, preview_mock, session_factory_mock):
        preview_mock.return_value = {
            'action_type': REVIEW_ACTION_CHASE,
            'reservation_id': 44,
            'listing_id': 55,
            'listing_name': 'Test Home',
            'portfolio': 'Test Portfolio',
            'guest_name': 'Test Guest',
            'channel_name': 'airbnbOfficial',
            'conversation_id': 66,
            'communication_type': 'channel',
            'content': 'A sufficiently long test message for the guest.',
            'mode': 'live',
            'simulated': False,
            'execution_enabled': True,
            'live_host_review_supported': False,
            'assisted_host_review': False,
            'review_destination': None,
            'capability_note': 'This message will be sent through Hostaway.',
        }
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = None

        def assign_action_id():
            session.add.call_args.args[0].action_id = 901

        session.flush.side_effect = assign_action_id
        session_factory_mock.return_value = session
        gateway = Mock()
        gateway.send_guest_message.return_value = {
            'provider_reference': 'message-777',
            'simulated': False,
        }

        result = perform_review_automation_action(
            44,
            REVIEW_ACTION_CHASE,
            'A sufficiently long edited message for the guest.',
            7,
            gateway=gateway,
        )

        gateway.send_guest_message.assert_called_once()
        action = session.add.call_args.args[0]
        self.assertEqual(action.status, 'sent')
        self.assertEqual(action.provider_reference, 'message-777')
        self.assertEqual(result['status'], 'sent')
        self.assertFalse(result['simulated'])
        session.commit.assert_called_once()
        session.close.assert_called_once()


class HostawayMessageClientTests(unittest.TestCase):
    def test_send_message_uses_documented_channel_payload_without_retry_wrapper(self):
        client = HostawayAPIClient.__new__(HostawayAPIClient)
        client._make_post_request = Mock(return_value={'result': {'id': 777}})

        result = client.send_conversation_message(1406, 'Hello guest', 'channel')

        self.assertEqual(result['result']['id'], 777)
        client._make_post_request.assert_called_once_with(
            'conversations/1406/messages',
            {'body': 'Hello guest', 'communicationType': 'channel'},
        )


if __name__ == '__main__':
    unittest.main()
