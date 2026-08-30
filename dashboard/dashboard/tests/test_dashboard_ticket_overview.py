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
    base_template = (PROJECT_ROOT / "dashboard/templates/base.html").read_text()
    template = (PROJECT_ROOT / "dashboard/templates/dashboard/dashboard.html").read_text()
    tickets_template = (PROJECT_ROOT / "dashboard/templates/tickets/list.html").read_text()
    script = (PROJECT_ROOT / "dashboard/static/js/dashboard-page.js").read_text()
    shared_script = (PROJECT_ROOT / "dashboard/static/js/ticket-cards.js").read_text()
    shared_styles = (PROJECT_ROOT / "dashboard/static/css/ticket-cards.css").read_text()

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
    assert "window.TicketCards.create" in card_source
    assert "window.TicketCards.create" in tickets_template
    assert "ticket-cards.js" in base_template
    assert "ticket-cards.css" in base_template
    assert 'class="dashboard-tickets-list ticket-summary-grid"' in template
    assert 'class="tickets-container ticket-summary-grid"' in tickets_template
    for required_field in (
        "ticket.ticket_id",
        "ticket.title",
        "ticket.status",
        "ticket.priority",
        "ticket.assigned_user_name",
        "ticket.created_at",
        "ticket.due_date",
        "ticket.tags",
    ):
        assert required_field in shared_script
    assert "propertyName(ticket" in shared_script
    assert "ticket?.listings" in shared_script
    assert "ticket.description" not in shared_script
    assert ".ticket-summary-card__property" in shared_styles
    assert ".ticket-summary-card__tags" in shared_styles
    assert "dataAction: 'open-ticket-detail'" in card_source
    assert "openDashboardTicketDetail(ticket, trigger)" in script
    assert 'data-action="close-ticket-detail"' in template


def test_dashboard_ticket_data_includes_all_properties_and_tags():
    service = (PROJECT_ROOT / "dashboard/dashboard/service.py").read_text()

    assert "TicketListing.ticket_id.in_(ticket_ids)" in service
    assert "ticket_dict['listings']" in service
    assert "ticket_dict['listing'] = ticket_dict['listings'][0]" in service
    assert "ticket_dict['tags']" in service


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
