from pathlib import Path
from types import SimpleNamespace

from dashboard.tickets.routes import (
    _build_ticket_filter_counts,
    _parse_ticket_listing_selection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def ticket(ticket_id, *, listing_id, status, priority, assignee_id, category):
    return SimpleNamespace(
        ticket_id=ticket_id,
        listing_id=listing_id,
        status=status,
        priority=priority,
        assigned_user_id=assignee_id,
        category=category,
    )


def ticket_set():
    return [
        ticket(1, listing_id=10, status='Open', priority='Low', assignee_id=1, category='cleaning'),
        ticket(2, listing_id=10, status='Open', priority='High', assignee_id=2, category='technology'),
        ticket(3, listing_id=20, status='Closed', priority='High', assignee_id=2, category='technology'),
        ticket(4, listing_id=None, status='Open', priority='High', assignee_id=2, category='other'),
    ]


def test_listing_filter_parser_supports_general_legacy_and_multiple_values():
    assert _parse_ticket_listing_selection('10, general, 20,invalid') == {0, 10, 20}
    assert _parse_ticket_listing_selection(None, 12) == {12}
    assert _parse_ticket_listing_selection(None, None) == set()


def test_filter_counts_exclude_their_own_facet_but_honor_other_filters():
    counts = _build_ticket_filter_counts(
        ticket_set(),
        {4: [20]},
        selected_listing_ids={10},
        selected_statuses={'Open'},
        selected_assignee_id=2,
        selected_priorities={'High'},
    )

    assert counts['result_total'] == 1
    assert counts['properties'] == {
        'all': 2,
        'options': {'0': 1, '10': 1, '20': 1},
    }
    assert counts['statuses']['Open'] == 1
    assert counts['statuses']['Closed'] == 0
    assert counts['assignees']['all'] == 1
    assert counts['assignees']['2'] == 1
    assert counts['priorities']['High'] == 1
    assert counts['priorities']['Low'] == 0
    assert counts['categories']['all'] == 1
    assert counts['categories']['technology'] == 1


def test_filter_counts_react_to_cross_filter_changes():
    counts = _build_ticket_filter_counts(
        ticket_set(),
        {4: [20]},
        selected_listing_ids={10},
        selected_statuses={'Open'},
        selected_priorities=None,
    )

    assert counts['result_total'] == 2
    assert counts['assignees']['1'] == 1
    assert counts['assignees']['2'] == 1
    assert counts['priorities']['Low'] == 1
    assert counts['priorities']['High'] == 1
    assert counts['statuses']['Open'] == 2


def test_category_counts_ignore_category_but_honor_every_other_filter():
    counts = _build_ticket_filter_counts(
        ticket_set(),
        {4: [20]},
        selected_listing_ids={10},
        selected_statuses={'Open'},
        selected_category='technology',
    )

    assert counts['result_total'] == 1
    assert counts['categories']['all'] == 2
    assert counts['categories']['cleaning'] == 1
    assert counts['categories']['technology'] == 1
    assert counts['categories']['other'] == 0
    assert counts['assignees']['all'] == 1


def test_empty_status_selection_keeps_status_options_discoverable():
    counts = _build_ticket_filter_counts(
        ticket_set(),
        {4: [20]},
        selected_listing_ids={10},
        selected_statuses=set(),
    )

    assert counts['result_total'] == 0
    assert counts['properties']['all'] == 0
    assert counts['assignees']['all'] == 0
    assert counts['priorities']['High'] == 0
    assert counts['statuses']['Open'] == 2
    assert counts['categories']['all'] == 0


def test_ticket_list_template_renders_and_refreshes_all_filter_counts():
    template = (PROJECT_ROOT / 'dashboard/templates/tickets/list.html').read_text()
    styles = (PROJECT_ROOT / 'dashboard/static/css/product-shell.css').read_text()

    assert 'data-property-count' in template
    assert 'data-status-count="Open"' in template
    assert 'data-priority-count="Critical"' in template
    assert 'data-base-label="All Users"' in template
    assert 'data-base-label="All Categories"' in template
    assert '/tickets/api/tickets/filter-counts?' in template
    assert 'renderTicketFilterCounts(filterCounts)' in template
    assert '.filter-option-count' in styles
