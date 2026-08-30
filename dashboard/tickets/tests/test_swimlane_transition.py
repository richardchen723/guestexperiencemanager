from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from dashboard.tickets import models, routes


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class FakeTicketSession:
    def __init__(self, ticket, fail_commit=False):
        self.ticket = ticket
        self.fail_commit = fail_commit
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self.ticket

    def add(self, value):
        value.comment_id = 73
        self.added.append(value)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError('commit failed')
        self.committed = True

    def refresh(self, _value):
        return None

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def make_ticket(status='Open'):
    return SimpleNamespace(
        ticket_id=701,
        title='Move this ticket',
        status=status,
        updated_at=datetime(2026, 8, 30, 1, 2, 3),
        created_by=1,
        assigned_user_id=1,
    )


def test_status_and_handoff_comment_commit_in_one_transaction():
    ticket = make_ticket()
    session = FakeTicketSession(ticket)
    with patch.object(models, 'get_session', return_value=session):
        payload = models.transition_ticket_status_with_comment(
            ticket_id=701,
            user_id=1,
            status='In Progress',
            comment_text='Starting implementation now.',
            expected_status='Open',
            assigned_user_id=2,
            expected_assigned_user_id=1,
        )

    assert session.committed
    assert not session.rolled_back
    assert session.closed
    assert ticket.status == 'In Progress'
    assert ticket.assigned_user_id == 2
    assert len(session.added) == 1
    assert session.added[0].comment_text == 'Starting implementation now.'
    assert payload['ticket']['status'] == 'In Progress'
    assert payload['comment']['comment_id'] == 73


def test_failed_transition_rolls_back_status_and_comment_transaction():
    session = FakeTicketSession(make_ticket(), fail_commit=True)
    with (
        patch.object(models, 'get_session', return_value=session),
        pytest.raises(RuntimeError, match='commit failed'),
    ):
        models.transition_ticket_status_with_comment(
            ticket_id=701,
            user_id=1,
            status='Blocked',
            comment_text='Waiting on access.',
            expected_status='Open',
        )

    assert session.rolled_back
    assert session.closed


def test_stale_board_transition_is_rejected_before_commit():
    session = FakeTicketSession(make_ticket(status='Assigned'))
    with (
        patch.object(models, 'get_session', return_value=session),
        pytest.raises(models.TicketTransitionConflict, match='already in Assigned'),
    ):
        models.transition_ticket_status_with_comment(
            ticket_id=701,
            user_id=1,
            status='Blocked',
            comment_text='Waiting on access.',
            expected_status='Open',
        )

    assert not session.committed
    assert session.rolled_back
    assert session.closed


def test_stale_board_assignment_is_rejected_before_commit():
    session = FakeTicketSession(make_ticket(status='Open'))
    with (
        patch.object(models, 'get_session', return_value=session),
        pytest.raises(models.TicketTransitionConflict, match='assignment changed'),
    ):
        models.transition_ticket_status_with_comment(
            ticket_id=701,
            user_id=1,
            status='In Progress',
            comment_text='Handing this over.',
            expected_status='Open',
            assigned_user_id=2,
            expected_assigned_user_id=7,
        )

    assert not session.committed
    assert session.rolled_back
    assert session.closed


def test_transition_api_requires_comment_and_calls_atomic_workflow():
    app = Flask(__name__)
    user = SimpleNamespace(
        user_id=1,
        name='Richard Chen',
        email='richard@example.com',
        is_admin=lambda: True,
    )
    transition_result = {
        'ticket': {'ticket_id': 701, 'status': 'In Progress', 'assigned_user_id': 2},
        'comment': {'comment_id': 73, 'comment_text': '@Lydia Liang Starting implementation now.'},
    }
    assignee = SimpleNamespace(
        user_id=2,
        name='Lydia Liang',
        email='lydia@example.com',
        is_approved=True,
    )

    with (
        app.test_request_context(
            '/tickets/api/tickets/701/transition',
            method='POST',
            json={
                'status': 'In Progress',
                'from_status': 'Open',
                'assigned_user_id': 2,
                'from_assigned_user_id': 1,
                'comment_text': '@Lydia Liang Starting implementation now.',
            },
        ),
        patch.object(routes, 'get_current_user', return_value=user),
        patch.object(routes, 'get_ticket', return_value=make_ticket()),
        patch.object(routes, 'get_user_by_id', return_value=assignee),
        patch.object(
            routes,
            'transition_ticket_status_with_comment',
            return_value=transition_result,
        ) as transition,
        patch.object(routes, 'sync_issue_from_ticket_status'),
        patch('dashboard.activities.logger.log_ticket_activity'),
        patch('dashboard.activities.logger.log_comment_activity'),
        patch('dashboard.notifications.helpers.send_assignment_notification') as assignment_notification,
        patch('dashboard.notifications.helpers.send_status_change_notification') as status_notification,
        patch('dashboard.notifications.helpers.send_mention_notification') as mention_notification,
        patch('dashboard.notifications.mention_parser.parse_mentions', return_value=[(2, '@Lydia Liang')]),
    ):
        response, status = routes.api_transition_ticket.__wrapped__(701)

    assert status == 200
    assert response.get_json()['message'] == 'Ticket moved to In Progress'
    transition.assert_called_once_with(
        ticket_id=701,
        user_id=1,
        status='In Progress',
        comment_text='@Lydia Liang Starting implementation now.',
        expected_status='Open',
        assigned_user_id=2,
        expected_assigned_user_id=1,
    )
    assignment_notification.assert_called_once_with(2, 701)
    status_notification.assert_called_once_with(2, 701, 'Open', 'In Progress', 'Richard Chen')
    mention_notification.assert_called_once_with(
        2,
        701,
        '@Lydia Liang Starting implementation now.',
        'Richard Chen',
    )

    with (
        app.test_request_context(
            '/tickets/api/tickets/701/transition',
            method='POST',
            json={'status': 'In Progress', 'from_status': 'Open', 'comment_text': '   '},
        ),
        patch.object(routes, 'get_current_user', return_value=user),
        patch.object(routes, 'get_ticket', return_value=make_ticket()),
    ):
        response, status = routes.api_transition_ticket.__wrapped__(701)

    assert status == 400
    assert 'handoff comment' in response.get_json()['error'].lower()


def test_swimlane_template_exposes_drag_and_keyboard_workflows():
    template = (PROJECT_ROOT / 'dashboard/templates/tickets/list.html').read_text()
    styles = (PROJECT_ROOT / 'dashboard/static/css/ticket-swimlane.css').read_text()

    assert "card.draggable = true" in template
    assert 'class="swimlane-card-move"' in template
    assert 'role="dialog"' in template
    assert 'Handoff comment <strong>Required</strong>' in template
    assert "new MentionHandler(handoffComment" in template
    assert 'Type <kbd>@</kbd> to tag a team member' in template
    assert 'aria-required="true"' in template
    assert 'id="swimLaneTransitionAssignee"' in template
    assert 'from_assigned_user_id' in template
    assert "/transition`" in template
    assert 'captureSwimLaneContext()' in template
    assert 'restoreSwimLaneContext()' in template
    assert '.swimlane-dropzone.is-drag-over' in styles
    assert '@media (prefers-reduced-motion: reduce)' in styles
