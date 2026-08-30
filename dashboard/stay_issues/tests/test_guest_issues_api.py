import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask, g

from dashboard.api import security
from dashboard.auth.api_keys import GUEST_ISSUES_READ_SCOPE
from dashboard.stay_issues import routes
from dashboard.stay_issues.api_service import (
    GuestIssueApiService,
    redact_sensitive_text,
)


class FakeIssueQuery:
    def __init__(self, issues):
        self.issues = list(issues)

    def filter(self, *_args):
        return self

    def all(self):
        return list(self.issues)

    def first(self):
        return self.issues[0] if self.issues else None


class FakeBrainSession:
    def __init__(self, issues):
        self.issues = issues

    def query(self, *_args):
        return FakeIssueQuery(self.issues)


def make_issue(issue_id, *, priority="Medium", status="need_attention", reported_at=None):
    reported_at = reported_at or datetime(2026, 8, issue_id, 12, 0)
    return SimpleNamespace(
        issue_id=issue_id,
        source_kind="stay",
        listing_id=101,
        reservation_id=201,
        review_id=None,
        source_date=date(2026, 8, issue_id),
        issue_category="cleanliness",
        summary="Jane Doe reported jane@example.com",
        details="Call +1 (555) 444-3333; api_key=hk_123456789abcdef",
        suggested_improvement="Follow up with Jane",
        severity="material",
        resolution_state="feedback",
        source_references=[
            {"source_type": "message", "source_id": 301, "label": "raw content"},
            {"source_type": "review", "source_id": 401, "source_part": "private_feedback"},
            {"source_type": "other", "source_id": "do-not-return"},
        ],
        workflow_status="resolved" if status == "resolved" else "open",
        operational_status=status,
        priority=priority,
        priority_updated_at=None,
        priority_updated_by_user_id=None,
        resolution_comment="Resolved with jane@example.com",
        resolution_method=None,
        resolved_at=None,
        resolved_by_user_id=None,
        linked_ticket_id=501,
        created_at=reported_at,
        updated_at=reported_at,
    )


def service_context():
    reservation = SimpleNamespace(
        guest_name="Jane Doe",
        guest_first_name="Jane",
        guest_last_name="Doe",
        guest_email="jane@example.com",
        guest_phone="+1 (555) 444-3333",
        guest_address="10 Private Lane",
        confirmation_code="ABCSECRET",
        channel_reservation_id="CHANNELSECRET",
    )
    assignee = SimpleNamespace(user_id=8, name="Operator", email="private@example.com")
    return {
        "listings": {101: SimpleNamespace(internal_listing_name="Aurora Haven", name="Aurora")},
        "portfolios": {101: "Enchanted Havens"},
        "reservations": {201: reservation},
        "conversation_by_message": {301: 601},
        "tickets": {501: SimpleNamespace(ticket_id=501, assigned_user_id=8, assigned_user=assignee)},
        "users": {8: assignee},
    }


class GuestIssueApiServiceTests(unittest.TestCase):
    def make_service(self, issues):
        service = object.__new__(GuestIssueApiService)
        service.brain_session = FakeBrainSession(issues)
        service.now = datetime(2026, 8, 30, 15, 0)
        service._load_context = lambda _issues: service_context()
        return service

    def test_serializer_redacts_guest_pii_and_credentials(self):
        service = self.make_service([])
        payload = service._serialize(make_issue(1), service_context())
        serialized = json.dumps(payload)

        self.assertNotIn("Jane Doe", serialized)
        self.assertNotIn("jane@example.com", serialized)
        self.assertNotIn("555", serialized)
        self.assertNotIn("hk_123456789abcdef", serialized)
        self.assertNotIn("private@example.com", serialized)
        self.assertNotIn("raw content", serialized)
        self.assertEqual(payload["references"]["conversation_ids"], [601])
        self.assertEqual(payload["assignee"], {"user_id": 8, "name": "Operator"})
        self.assertEqual(
            payload["references"]["source_references"],
            [
                {"source_type": "message", "source_id": 301},
                {"source_type": "review", "source_id": 401, "source_part": "private_feedback"},
            ],
        )

    def test_list_is_filtered_sorted_and_paginated_deterministically(self):
        issues = [
            make_issue(1, priority="Low", reported_at=datetime(2026, 8, 28, 12)),
            make_issue(2, priority="Critical", reported_at=datetime(2026, 8, 29, 12)),
            make_issue(3, priority="High", status="resolved", reported_at=datetime(2026, 8, 30, 12)),
        ]
        service = self.make_service(issues)
        payload = service.list_issues({
            "status": "need_attention",
            "sort": "priority",
            "order": "desc",
            "page": "1",
            "per_page": "1",
        })

        self.assertEqual([row["issue_id"] for row in payload["data"]], [2])
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual(payload["pagination"]["next_page"], 2)

    def test_incremental_and_recency_parameters_are_validated(self):
        service = self.make_service([])
        payload = service.list_issues({
            "updated_since": "2026-08-29T12:30:00Z",
            "sort": "updated_at",
            "order": "asc",
        })
        self.assertEqual(payload["meta"]["filters"]["updated_since"], "2026-08-29T12:30:00Z")

        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            service.list_issues({"recency": "7d", "reported_from": "2026-08-01"})

    def test_redactor_removes_bearer_tokens_and_named_secrets(self):
        value = "Bearer abcdefghijk password=hunter2 and test@example.com"
        redacted = redact_sensitive_text(value, maximum=500)
        self.assertNotIn("abcdefghijk", redacted)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("test@example.com", redacted)


class GuestIssueApiRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(routes.guest_issues_api_bp)
        self.client = self.app.test_client()

    def test_missing_api_key_is_rejected_with_machine_readable_error(self):
        with patch.object(security, "authenticate_request_api_key", return_value=None):
            response = self.client.get("/api/v1/guest-issues")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "authentication_required")

    def test_valid_key_without_scope_is_rejected(self):
        def deny(*_args, **_kwargs):
            g.api_key_scope_denied = GUEST_ISSUES_READ_SCOPE
            return None

        with patch.object(security, "authenticate_request_api_key", side_effect=deny):
            response = self.client.get("/api/v1/guest-issues")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"]["code"], "insufficient_scope")

    def test_authorized_list_request_has_rate_headers_and_is_audited(self):
        api_key = SimpleNamespace(api_key_id=4)
        access = {
            "allowed": True,
            "log_id": 10,
            "limit": 60,
            "remaining": 59,
            "reset_at": datetime(2026, 8, 30, 16, 0),
            "retry_after": 60,
        }
        service = MagicMock()
        service.list_issues.return_value = {
            "data": [{"issue_id": 1}],
            "pagination": {"total": 1},
            "meta": {},
        }
        with (
            patch.object(security, "authenticate_request_api_key", return_value=api_key),
            patch.object(security, "reserve_api_access", return_value=access),
            patch.object(security, "finalize_api_access") as finalize,
            patch.object(routes, "GuestIssueApiService", return_value=service),
        ):
            response = self.client.get("/api/v1/guest-issues?status=need_attention")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-RateLimit-Remaining"], "59")
        finalize.assert_called_once_with(10, status_code=200, response_count=1)
        service.close.assert_called_once_with()

    def test_rate_limited_request_returns_retry_metadata(self):
        api_key = SimpleNamespace(api_key_id=4)
        access = {
            "allowed": False,
            "log_id": 11,
            "limit": 1,
            "remaining": 0,
            "reset_at": datetime(2026, 8, 30, 16, 0),
            "retry_after": 42,
        }
        with (
            patch.object(security, "authenticate_request_api_key", return_value=api_key),
            patch.object(security, "reserve_api_access", return_value=access),
        ):
            response = self.client.get("/api/v1/guest-issues")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"]["code"], "rate_limit_exceeded")
        self.assertEqual(response.headers["Retry-After"], "42")
        self.assertEqual(response.headers["X-RateLimit-Remaining"], "0")


if __name__ == "__main__":
    unittest.main()
