"""Offline protocol/restart tests: synthetic messages, no production or network."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(os.environ.get("INQUIRY_WORKER_PROJECT", ROOT))
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location("inquiry_intake_worker", ROOT / "scripts/inquiry_intake_worker.py")
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("Offline tests must never contact a real service")
    monkeypatch.setattr(w.requests.Session, "request", forbidden)


class Clock:
    now = 1100
    def __call__(self): return self.now


def raw(message_id="original-gmail-1", thread_id="thread-1", at=1001, *, initial=True, body=None, labels=None, reference="EH-OFFLINE1"):
    body = body if body is not None else (f"Reference: {reference}\r\nName: Offline Guest\r\nEmail: guest@example.invalid\r\nProperty: Fair Haven\r\n" if initial else "Guests: 4\r\n  unchanged whitespace  \r\n")
    original = (f"From: {'Enchanted Havens <yunhang.chen@gmail.com>' if initial else 'guest@example.invalid'}\r\n"
                f"Subject: {'New stay inquiry ' + reference if initial else 'Changed subject for our stay'}\r\n"
                f"Message-ID: <{message_id}@example.invalid>\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body).encode()
    return {"id": message_id, "threadId": thread_id, "internalDate": str(at * 1000), "labelIds": labels or ["INBOX"],
            "raw": base64.urlsafe_b64encode(original).decode().rstrip("=")}


class GmailFake:
    def __init__(self, messages=()):
        self.messages = {m["id"]: m for m in messages}
        self.queries, self.fetches = [], []
        self.fail_after_first = False
    def profile(self): return {"emailAddress": w.MAILBOX}
    def matching(self, query):
        import re
        self.queries.append(query)
        after = re.search(r"after:(\d+)", query)
        before = re.search(r"before:(\d+)", query)
        for message in list(self.messages.values()):
            payload = w.normalized(message)
            at = int(message["internalDate"]) / 1000
            if not payload["subject"].startswith("New stay inquiry EH-") or "EH-2902A0CA" in payload["subject"]:
                continue
            if after and at <= int(after[1]) or before and at >= int(before[1]):
                continue
            yield {"id": message["id"], "threadId": message["threadId"]}
            if self.fail_after_first and after:
                self.fail_after_first = False
                raise w.WorkerError("http_503", transient=True)
    def message(self, message_id):
        self.fetches.append(message_id)
        return copy.deepcopy(self.messages[message_id])
    def thread(self, thread_id):
        return [m for m in self.messages.values() if m["threadId"] == thread_id]


class CRMFake:
    def __init__(self):
        self.originals, self.calls = {}, []
        self.behavior = None
    def context(self):
        return {"sources": [{"id": w.SOURCE, "mailbox": w.MAILBOX, "senders": ["yunhang.chen@gmail.com"],
                             "subject_prefixes": ["New stay inquiry EH-"]}], "properties": [{"id": 101, "name": "Fair Haven"}]}
    def intake(self, payload):
        self.calls.append(copy.deepcopy(payload))
        if isinstance(self.behavior, w.WorkerError):
            raise self.behavior
        if isinstance(self.behavior, dict):
            return self.behavior
        message_id = payload["message_id"]
        replayed = message_id in self.originals
        if replayed:
            assert self.originals[message_id] == payload, "Crash retries must preserve the exact original payload"
        self.originals.setdefault(message_id, copy.deepcopy(payload))
        if self.behavior == "crash_after_accept":
            self.behavior = None
            raise KeyboardInterrupt()
        return {"message_id": list(self.originals).index(message_id) + 1, "lead_id": 1,
                "processing_status": "processed", "replayed": replayed}


@pytest.fixture
def system(tmp_path):
    directory = tmp_path / "private-state"
    directory.mkdir(mode=0o700)
    ledger = w.Ledger(directory / "state.sqlite3")
    ledger.initialize(1000)
    gmail, crm, clock = GmailFake(), CRMFake(), Clock()
    worker = w.Worker(ledger, gmail, crm, clock=clock, jitter=lambda: 0)
    yield worker, ledger, gmail, crm, clock
    ledger.db.close()


def test_registered_owner_test_is_labeled_before_intake_and_replay(system):
    worker, ledger, gmail, crm, clock = system
    marker = 'INTERNAL-CRM-TEST-' + 'A1' * 12
    worker.test_run_path.write_text(json.dumps([{'marker':marker,'email':'yunhang.chen@gmail.com','created_at':1000,'expires_at':1200}]))
    worker.test_run_path.chmod(0o600)
    gmail.messages = {'original-gmail-1':raw(body=f'Email: yunhang.chen@gmail.com\r\nNotes: {marker}\r\n')}
    crm.behavior = 'crash_after_accept'
    with pytest.raises(KeyboardInterrupt):
        worker.cycle()
    assert crm.calls[0]['internal_test'] is True
    worker.test_run_path.unlink()
    worker.cycle()
    assert crm.calls[-1] == crm.calls[0]
    assert ledger.db.execute('SELECT status FROM messages').fetchone()[0] == 'processed'


@pytest.mark.parametrize('registration', ['missing','expired','wrong-email'])
def test_unverified_test_marker_is_retained_for_review_not_imported(system, registration):
    worker, ledger, gmail, crm, clock = system
    marker = 'INTERNAL-CRM-TEST-' + 'B2' * 12
    if registration != 'missing':
        worker.test_run_path.write_text(json.dumps([{'marker':marker,'email':'other@example.invalid' if registration == 'wrong-email' else 'yunhang.chen@gmail.com',
                                                   'created_at':1000,'expires_at':1002 if registration == 'expired' else 1200}]))
        worker.test_run_path.chmod(0o600)
    gmail.messages = {'original-gmail-1':raw(body=f'Email: yunhang.chen@gmail.com\r\nNotes: {marker}\r\n')}
    worker.cycle()
    assert crm.calls == []
    row = ledger.db.execute('SELECT * FROM messages').fetchone()
    assert row['status'] == 'blocked' and row['error_code'] == 'internal_test_registration_review_required'
    assert json.loads(row['original_json']) == {k:v for k,v in gmail.messages['original-gmail-1'].items() if k != 'labelIds'}


def test_new_inquiry_changed_subject_reply_and_repeated_scan_make_one_lead(system):
    worker, ledger, gmail, crm, clock = system
    initial, reply = raw(), raw("original-gmail-2", at=1002, initial=False)
    gmail.messages = {m["id"]: m for m in (reply, initial)}
    worker.cycle()
    worker.cycle()
    assert set(crm.originals) == {initial["id"], reply["id"]}
    assert len(crm.calls) == 2
    rows = ledger.db.execute("SELECT * FROM messages ORDER BY received_at").fetchall()
    assert [r["lead_id"] for r in rows] == [1, 1]
    assert rows[1]["message_id"] != str(rows[1]["crm_record_id"])
    assert json.loads(rows[1]["payload_json"])["extraction"]["fields"] == {"guest_count": 4}
    assert json.loads(rows[1]["payload_json"])["extraction"]["reference"] == "EH-OFFLINE1"
    assert all("is:unread" not in q for q in gmail.queries)


def test_crash_after_remote_accept_reopens_and_exactly_replays(system):
    worker, ledger, gmail, crm, clock = system
    gmail.messages = {"original-gmail-1": raw()}
    crm.behavior = "crash_after_accept"
    with pytest.raises(KeyboardInterrupt): worker.cycle()
    assert ledger.db.execute("SELECT status FROM messages").fetchone()[0] == "pending"
    reopened = w.Ledger(ledger.path)
    w.Worker(reopened, gmail, crm, clock=clock, jitter=lambda: 0).cycle()
    row = reopened.db.execute("SELECT * FROM messages").fetchone()
    assert row["status"] == "processed" and row["replayed"] == 1 and row["message_id"] == "original-gmail-1"
    assert len(crm.originals) == 1 and len(crm.calls) == 2
    reopened.db.close()


def test_collection_page_failure_does_not_advance_checkpoint_or_lose_partial_queue(system):
    worker, ledger, gmail, crm, clock = system
    gmail.messages = {m["id"]: m for m in (raw(), raw("second", "thread-2", 1003))}
    gmail.fail_after_first = True
    worker.cycle()
    assert ledger.meta("last_scan") is None and not crm.calls
    assert ledger.status()["messages"] == {"pending": 1}
    clock.now += 6
    worker.cycle()
    assert len(crm.originals) == 2 and ledger.meta("last_scan") == clock.now


def test_old_mail_and_internal_test_and_sent_messages_never_import(system):
    worker, ledger, gmail, crm, clock = system
    messages = [raw("old", "old-thread", 999), raw("test", "test-thread", 1004, reference="EH-2902A0CA"),
                raw(), raw("sent", at=1006, initial=False, labels=["SENT"])]
    gmail.messages = {m["id"]: m for m in messages}
    worker.cycle()
    assert list(crm.originals) == ["original-gmail-1"]
    assert ledger.status()["backlog_candidates"] == 1
    assert ledger.meta("activation_at") == 1000
    assert "old" not in gmail.fetches


def test_outage_lookback_and_daily_reconciliation_are_durable(system):
    worker, ledger, gmail, crm, clock = system
    worker.cycle()
    clock.now += 7200
    gmail.messages = {"during-outage": raw("during-outage", at=4000)}
    worker.cycle()
    assert "during-outage" in crm.originals
    gmail.messages["late-indexed"] = raw("late-indexed", "thread-3", 1200)
    clock.now += 86401
    worker.cycle()
    assert "late-indexed" in crm.originals


def test_transport_retry_after_is_persisted_across_restart(system):
    worker, ledger, gmail, crm, clock = system
    gmail.messages = {"original-gmail-1": raw()}
    crm.behavior = w.WorkerError("http_429", transient=True, retry_after=90)
    worker.cycle()
    row = ledger.db.execute("SELECT * FROM messages").fetchone()
    assert row["status"] == "retry" and row["next_attempt"] == 1190
    reopened = w.Ledger(ledger.path)
    restarted = w.Worker(reopened, gmail, crm, clock=clock, jitter=lambda: 0)
    crm.behavior = None
    clock.now = 1189
    restarted.cycle()
    assert len(crm.calls) == 1
    clock.now = 1190
    restarted.cycle()
    assert len(crm.calls) == 2 and reopened.status()["messages"] == {"processed": 1}
    reopened.db.close()


@pytest.mark.parametrize("result,code", [({"processing_status": "failed", "message_id": 1}, "crm_processing_failed"),
                                          ({"processing_status": "ignored", "message_id": 1}, "crm_ignored_requires_review"),
                                          ({"status": "ok"}, "crm_result_contract_invalid")])
def test_http_success_is_not_processing_success_and_originals_stay_durable(system, result, code):
    worker, ledger, gmail, crm, clock = system
    original = raw()
    gmail.messages = {original["id"]: original}
    crm.behavior = result
    worker.cycle(); worker.cycle()
    row = ledger.db.execute("SELECT * FROM messages").fetchone()
    assert row["status"] == "blocked" and row["error_code"] == code and len(crm.calls) == 1
    assert json.loads(row["original_json"])["raw"] == original["raw"]


def test_initial_failure_holds_reply_until_operator_retry(system):
    worker, ledger, gmail, crm, clock = system
    gmail.messages = {m["id"]: m for m in (raw(), raw("reply", at=1005, initial=False))}
    crm.behavior = {"processing_status": "failed", "message_id": 1}
    worker.cycle()
    assert len(crm.calls) == 1 and ledger.status()["messages"] == {"blocked": 1, "pending": 1}
    with ledger.db: ledger.db.execute("UPDATE messages SET status='pending' WHERE role='initial'")
    crm.behavior = None
    worker.cycle()
    assert len(crm.originals) == 2


def test_immutable_original_conflict_never_overwrites(system):
    _, ledger, _, _, _ = system
    first = raw()
    ledger.store(first, "initial", payload=w.normalized(first))
    with pytest.raises(w.WorkerError, match="original_id_content_conflict"):
        second = raw(body="Different original")
        ledger.store(second, "initial", payload=w.normalized(second))
    assert json.loads(ledger.db.execute("SELECT original_json FROM messages").fetchone()[0])["raw"] == first["raw"]


def test_exact_plain_body_and_rfc822_survive(system):
    _, ledger, _, _, _ = system
    body = "\r\n  Name: Offline Guest  \r\n\r\n> quoted original  \r\n"
    message = raw(body=body)
    payload = w.normalized(message)
    ledger.store(message, "initial", payload=payload)
    assert payload["body"] == body
    assert json.loads(ledger.db.execute("SELECT original_json FROM messages").fetchone()[0])["raw"] == message["raw"]


def test_mailbox_mismatch_never_initializes_or_imports(system):
    worker, ledger, gmail, crm, clock = system
    gmail.profile = lambda: {"emailAddress": "other@example.invalid"}
    worker.cycle()
    assert ledger.meta("collection_error") == "gmail_profile_mailbox_mismatch" and not crm.calls


def test_private_files_and_readonly_credentials_enforced(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o755)
    with pytest.raises(w.WorkerError, match="unsafe_state_directory"): w.Ledger(directory / "db")
    secret = tmp_path / "credential.json"
    secret.write_text(json.dumps({"mailbox": w.MAILBOX, "scopes": [w.GMAIL_SCOPE, "https://mail.google.com/"],
                                  "client_id": "offline", "client_secret": "offline", "refresh_token": "offline"}))
    secret.chmod(0o600)
    with pytest.raises(w.WorkerError, match="gmail_credential_binding_or_scope_invalid"): w.Gmail(secret)
    secret.chmod(0o644)
    with pytest.raises(w.WorkerError, match="unsafe_secret_file_permissions"): w.private_file(secret)


def test_transport_never_redirects_or_leaks_body_on_errors():
    class Response:
        status_code = 429
        headers = {"Retry-After": "120"}
        text = "sensitive response must never be used"
    class HTTP:
        def request(self, method, url, **kw):
            assert kw["allow_redirects"] is False and kw["timeout"] == (10, 45)
            return Response()
    with pytest.raises(w.WorkerError) as exc:
        w.request_json(HTTP(), "GET", "https://example.invalid")
    assert str(exc.value) == "http_429" and exc.value.retry_after == 120


def test_gmail_pagination_completes_without_using_estimated_counts():
    gmail = object.__new__(w.Gmail)
    calls = []
    def get(path, params):
        calls.append(params)
        return {"messages": [{"id": "second"}]} if params.get("pageToken") else {"messages": [{"id": "first"}], "nextPageToken": "page2", "resultSizeEstimate": 1}
    gmail.get = get
    assert [m["id"] for m in gmail.matching("scoped")] == ["first", "second"]
    assert len(calls) == 2


def test_unsupported_body_is_retained_and_blocked_instead_of_truncated(system):
    worker, ledger, gmail, crm, _ = system
    message = raw(body="x" * 250001)
    gmail.messages = {message["id"]: message}
    # The Gmail fake normally parses candidates; supply only known API metadata here.
    gmail.matching = lambda query: iter([] if "before:" in query else [{"id": message["id"], "threadId": message["threadId"]}])
    worker.cycle()
    row = ledger.db.execute("SELECT * FROM messages").fetchone()
    assert row["status"] == "blocked" and row["error_code"] == "original_requires_review" and not crm.calls
    assert json.loads(row["original_json"])["raw"] == message["raw"]


def test_source_disabled_stops_collection_and_import(system):
    worker, ledger, gmail, crm, _ = system
    crm.context = lambda: {"sources": []}
    gmail.messages = {"original-gmail-1": raw()}
    worker.cycle()
    assert ledger.meta("collection_error") == "crm_source_configuration_invalid" and not gmail.fetches and not crm.calls


def test_systemd_credentials_directory_needs_no_specifier_or_shell(monkeypatch):
    monkeypatch.delenv("INQUIRY_GMAIL_CREDENTIAL_FILE", raising=False)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/run/credentials/offline.service")
    assert w.credential_path("INQUIRY_GMAIL_CREDENTIAL_FILE", "gmail.json") == "/run/credentials/offline.service/gmail.json"
