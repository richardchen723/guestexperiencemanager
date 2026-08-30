import unittest
from pathlib import Path

from flask import Flask

from dashboard.tickets import routes


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class TicketFilterNavigationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(routes.tickets_bp)

    def test_return_target_accepts_only_the_local_ticket_list(self):
        filtered_list = "/tickets/?status=In+Progress&priority=High&search=heater"

        with self.app.test_request_context('/'):
            self.assertEqual(routes._ticket_list_return_url(filtered_list), filtered_list)
            self.assertEqual(routes._ticket_list_return_url('https://example.com/tickets/'), '/tickets/')
            self.assertEqual(routes._ticket_list_return_url('//example.com/tickets/'), '/tickets/')
            self.assertEqual(routes._ticket_list_return_url('/dashboard/'), '/tickets/')

    def test_list_serializes_every_filter_and_builds_return_aware_links(self):
        template = (PROJECT_ROOT / 'dashboard/templates/tickets/list.html').read_text()

        for query_key in (
            'listing_ids',
            'assigned_user_id',
            'status',
            'priority',
            'category',
            'past_due',
            'recurring',
            'due_days',
            'search',
            'tags',
            'tag_logic',
        ):
            self.assertIn(f"'{query_key}'", template)

        self.assertIn('function buildTicketFilterParams()', template)
        self.assertIn('function syncTicketFiltersToUrl(params)', template)
        self.assertIn('function buildTicketDetailUrl(ticketId)', template)
        self.assertIn("new URLSearchParams({ return_to: returnTo })", template)
        self.assertIn('action: { href: buildTicketDetailUrl(ticket.ticket_id) }', template)
        self.assertIn('href="${buildTicketDetailUrl(ticket.ticket_id)}"', template)

    def test_detail_back_link_uses_the_validated_return_target(self):
        detail_template = (PROJECT_ROOT / 'dashboard/templates/tickets/detail.html').read_text()

        self.assertIn('href="{{ tickets_return_url }}"', detail_template)
        self.assertIn('const ticketsReturnUrl = {{ tickets_return_url|tojson }};', detail_template)

    def test_tag_filter_can_restore_selection_without_firing_a_change(self):
        tag_script = (PROJECT_ROOT / 'dashboard/static/js/tags.js').read_text()

        self.assertIn('this.ready = this.loadTags();', tag_script)
        self.assertIn('setSelectedTags(tagIds, logic = this.logic, notify = false)', tag_script)
        self.assertIn('if (notify)', tag_script)


if __name__ == '__main__':
    unittest.main()
