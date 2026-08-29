from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_dashboard_requests_enough_tickets_to_match_the_active_count():
    script = (PROJECT_ROOT / "dashboard/static/js/dashboard-page.js").read_text()
    routes = (PROJECT_ROOT / "dashboard/dashboard/routes.py").read_text()
    service = (PROJECT_ROOT / "dashboard/dashboard/service.py").read_text()

    assert "ticketLimit: 50" in script
    assert "request.args.get('ticket_limit', 50)" in routes
    assert "ticket_limit: int = 50" in service
    assert "def _get_my_tickets(self, limit: int = 50)" in service


def test_dashboard_ticket_cards_are_compact_and_open_a_detail_dialog():
    template = (PROJECT_ROOT / "dashboard/templates/dashboard/dashboard.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/dashboard-page.js").read_text()

    for element_id in (
        "dashboardTicketsCaption",
        "myTicketsList",
        "dashboardTicketDetail",
        "dashboardTicketDetailTitle",
        "dashboardTicketDetailBody",
        "dashboardTicketDetailLink",
    ):
        assert f'id="{element_id}"' in template

    card_source = script[script.index("function createTicketCard"):script.index("function handleDashboardTicketClick")]
    for required_field in (
        "ticket.ticket_id",
        "ticket.title",
        "ticket.status",
        "ticket.priority",
        "ticket.assigned_user_name",
        "ticket.created_at",
        "ticket.due_date",
    ):
        assert required_field in card_source
    assert "ticket.description" not in card_source
    assert "ticket.listing" not in card_source
    assert 'data-action="open-ticket-detail"' in card_source
    assert "openDashboardTicketDetail(ticket, trigger)" in script
    assert 'data-action="close-ticket-detail"' in template


def test_dashboard_ticket_dialog_supports_basic_updates():
    template = (PROJECT_ROOT / "dashboard/templates/dashboard/dashboard.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/dashboard-page.js").read_text()
    styles = (PROJECT_ROOT / "dashboard/static/css/dashboard-overview.css").read_text()

    for element_id in (
        "dashboardTicketDetailSave",
        "dashboardTicketDetailStatus",
        "dashboardToast",
    ):
        assert f'id="{element_id}"' in template

    assert "dashboardTicketQuickEditForm" in script
    for field_name in ("status", "priority", "assigned_user_id", "due_date"):
        assert field_name in script
    assert "fetch('/tickets/api/users')" in script
    assert "method: 'PUT'" in script
    assert "refreshDashboardAfterTicketUpdate" in script
    assert '#dashboardTicketQuickEditForm .dashboard-ticket-detail__field select' in styles
    assert 'height: 40px; min-height: 40px; max-height: 40px' in styles
    assert '-webkit-appearance: none; appearance: none' in styles
