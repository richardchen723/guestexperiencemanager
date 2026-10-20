#!/usr/bin/env python3
"""Intake-only Gmail collector. Originals and recovery state remain on the server.

No connector/session credentials, email mutations, model calls or pricing writes.
"""
from __future__ import annotations

import argparse
import base64
import email.policy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
import stat
import sys
import time
from datetime import datetime, timezone
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.inquiries.parser import parse_email

MAILBOX = "larry@enchantedhavens.com"
SOURCE = "enchanted-havens-email"
ORIGIN = "https://yourcottoncandy.com"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
EXCLUDED_REFERENCES = {"EH-2902A0CA"}
EXCLUDED_THREADS = {"1a1518b5492db6bd"}
SKIP_LABELS = {"SENT", "DRAFT", "SPAM", "TRASH"}
TEST_MARKER_PREFIX = "INTERNAL-CRM-TEST-"


class WorkerError(Exception):
    """Only fixed, non-sensitive codes are safe for logs and the ledger."""
    def __init__(self, code, *, transient=False, retry_after=0):
        self.code, self.transient, self.retry_after = code, transient, retry_after
        super().__init__(code)


def private_file(path):
    p = Path(path)
    info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in (0, os.getuid()):
        raise WorkerError("unsafe_secret_file_permissions")
    return p.read_text()


def credential_path(environment_key, name):
    # Ubuntu's systemd exports CREDENTIALS_DIRECTORY even when %d is unsupported
    # in Environment=. No shell expansion or credential values are required.
    return os.environ.get(environment_key) or str(Path(os.environ["CREDENTIALS_DIRECTORY"]) / name)


def retry_seconds(response, now):
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, min(seconds, 86400))


def request_json(http, method, url, *, clock=time.time, **kwargs):
    try:
        response = http.request(method, url, timeout=(10, 45), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise WorkerError("transport_failure", transient=True) from None
    if response.status_code != 200:
        transient = response.status_code in (408, 429, 500, 502, 503, 504)
        raise WorkerError(f"http_{response.status_code}", transient=transient,
                          retry_after=retry_seconds(response, clock()))
    try:
        value = response.json()
    except (ValueError, TypeError):
        raise WorkerError("invalid_json_response", transient=True) from None
    if not isinstance(value, dict):
        raise WorkerError("invalid_json_response", transient=True)
    return value


class Gmail:
    def __init__(self, credential_path, *, http=None, clock=time.time):
        self.http, self.clock = http or requests.Session(), clock
        try:
            self.credential = json.loads(private_file(credential_path))
        except (ValueError, OSError):
            raise WorkerError("gmail_credential_unavailable") from None
        c = self.credential
        if c.get("mailbox") != MAILBOX or c.get("scopes") != [GMAIL_SCOPE] or not all(
            isinstance(c.get(k), str) and c[k] for k in ("client_id", "client_secret", "refresh_token")
        ):
            raise WorkerError("gmail_credential_binding_or_scope_invalid")
        self.access_token, self.expires = None, 0

    def refresh(self):
        c = self.credential
        data = request_json(self.http, "POST", "https://oauth2.googleapis.com/token", clock=self.clock,
                            data={k: c[k] for k in ("client_id", "client_secret", "refresh_token")} |
                            {"grant_type": "refresh_token"})
        # Never request additional scopes on refresh, nor persist access tokens.
        scopes = set(data.get("scope", GMAIL_SCOPE).split())
        if scopes != {GMAIL_SCOPE} or not isinstance(data.get("access_token"), str):
            raise WorkerError("gmail_refreshed_scope_invalid")
        self.access_token = data["access_token"]
        self.expires = self.clock() + int(data.get("expires_in", 3600)) - 60

    def get(self, path, params=None):
        if not self.access_token or self.clock() >= self.expires:
            self.refresh()
        for attempt in range(2):
            try:
                return request_json(self.http, "GET", "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
                                    params=params, headers={"Authorization": "Bearer " + self.access_token}, clock=self.clock)
            except WorkerError as exc:
                if exc.code != "http_401" or attempt:
                    raise
                self.refresh()

    def profile(self):
        return self.get("profile")

    def matching(self, query):
        token = None
        seen = set()
        while True:
            data = self.get("messages", {"q": query, "maxResults": 500, **({"pageToken": token} if token else {})})
            for message in data.get("messages", []):
                yield message
            token = data.get("nextPageToken")
            if not token:
                return
            if token in seen:
                raise WorkerError("gmail_pagination_loop", transient=True)
            seen.add(token)

    def message(self, message_id):
        return self.get("messages/" + quote(message_id, safe=""), {"format": "raw"})

    def thread(self, thread_id):
        return self.get("threads/" + quote(thread_id, safe=""), {"format": "minimal"}).get("messages", [])


class CRM:
    def __init__(self, key_path, *, http=None):
        self.key, self.http = private_file(key_path).strip(), http or requests.Session()
        if not self.key:
            raise WorkerError("crm_key_unavailable")

    def call(self, path, payload=None):
        return request_json(self.http, "GET" if payload is None else "POST", ORIGIN + "/inquiries/api/" + path,
                            headers={"X-API-Key": self.key}, **({"json": payload} if payload is not None else {}))

    def context(self):
        return self.call("worker-context")

    def intake(self, payload):
        return self.call("intake", payload)


def normalized(raw):
    """Retain exact RFC822 bytes separately; do not strip or rewrite text bodies."""
    try:
        original = base64.urlsafe_b64decode(raw["raw"] + "=" * (-len(raw["raw"]) % 4))
        if not original:
            raise ValueError()
        msg = BytesParser(policy=email.policy.default).parsebytes(original)
        text, html = [], []
        for part in msg.walk():
            if part.is_multipart() or part.get_filename():
                continue
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            content = part.get_payload(decode=True)
            if content is not None:
                value = content.decode(part.get_content_charset() or "utf-8", errors="strict")
                (text if part.get_content_type() == "text/plain" else html).append(value)
        body = "\n".join(text or html)
        if not body or len(body) > 250000:
            raise ValueError()
        payload = {"source": SOURCE, "mailbox": MAILBOX, "message_id": raw["id"], "thread_id": raw["threadId"],
                   "internet_message_id": str(msg.get("Message-ID", "")) or None,
                   "in_reply_to": str(msg.get("In-Reply-To", "")) or None,
                   "sender": str(msg.get("From", "")), "subject": str(msg.get("Subject", "")), "body": body,
                   "received_at": datetime.fromtimestamp(int(raw["internalDate"]) / 1000, timezone.utc).isoformat()}
        if not all(payload[k] for k in ("sender", "subject", "message_id", "thread_id")):
            raise ValueError()
        payload["extraction"] = parse_email(payload["subject"], body)
        if not text:
            payload["extraction"] = {"reference": payload["extraction"]["reference"], "fields": {},
                                     "review_reasons": ["Original HTML retained; review source before extracting fields."]}
        return payload
    except (KeyError, TypeError, ValueError, LookupError, OverflowError):
        raise WorkerError("original_requires_review") from None


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, mode=0o700)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise WorkerError("unsafe_state_directory")
        if self.path.exists():
            private_file(self.path) if not self.path.stat().st_size else self._check_state()
        else:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS threads(thread_id TEXT PRIMARY KEY,initial_id TEXT NOT NULL,reference TEXT);
            CREATE TABLE IF NOT EXISTS backlog(message_id TEXT PRIMARY KEY,thread_id TEXT,observed_at REAL);
            CREATE TABLE IF NOT EXISTS messages(
                message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL,
                original_json TEXT NOT NULL, original_hash TEXT NOT NULL, payload_json TEXT,
                received_at REAL NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, error_code TEXT, crm_record_id INTEGER,
                lead_id INTEGER, replayed INTEGER, processed_at REAL);
        """)

    def _check_state(self):
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise WorkerError("unsafe_state_file")

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, json.dumps(value)))

    def initialize(self, now):
        if self.meta("activation_at") is None:
            with self.db:
                for key, value in {"activation_at": now, "mailbox": MAILBOX, "source": SOURCE}.items():
                    self.db.execute("INSERT INTO metadata VALUES(?,?)", (key, json.dumps(value)))
        if self.meta("mailbox") != MAILBOX or self.meta("source") != SOURCE:
            raise WorkerError("ledger_mailbox_binding_invalid")

    def has(self, message_id):
        return self.db.execute("SELECT 1 FROM messages WHERE message_id=?", (message_id,)).fetchone() is not None

    def store(self, raw, role, *, payload=None, error=None):
        original = json.dumps({k: raw.get(k) for k in ("id", "threadId", "internalDate", "raw")}, sort_keys=True)
        digest = hashlib.sha256(original.encode()).hexdigest()
        row = self.db.execute("SELECT original_hash FROM messages WHERE message_id=?", (raw["id"],)).fetchone()
        if row:
            if row[0] != digest:
                raise WorkerError("original_id_content_conflict")
            return
        with self.db:
            self.db.execute("""INSERT INTO messages(message_id,thread_id,role,original_json,original_hash,
                            payload_json,received_at,status,error_code) VALUES(?,?,?,?,?,?,?,?,?)""",
                            (raw["id"], raw["threadId"], role, original, digest,
                             json.dumps(payload, sort_keys=True) if payload else None,
                             int(raw["internalDate"]) / 1000, "blocked" if error else "pending", error))
            if role == "initial":
                self.db.execute("INSERT OR IGNORE INTO threads VALUES(?,?,?)", (
                    raw["threadId"], raw["id"], (payload or {}).get("extraction", {}).get("reference")))

    def status(self):
        return {"activation_at": self.meta("activation_at"), "last_collection_success": self.meta("last_collection_success"),
                "last_processing_success": self.meta("last_processing_success"), "interval_seconds": self.meta("interval_seconds"),
                "collection_error": self.meta("collection_error"), "collection_retry_at": self.meta("collection_retry_at"),
                "backlog_candidates": self.db.execute("SELECT count(*) FROM backlog").fetchone()[0],
                "messages": dict(self.db.execute("SELECT status,count(*) FROM messages GROUP BY status")),
                "unresolved": [dict(r) for r in self.db.execute("SELECT message_id,error_code,attempts,next_attempt FROM messages WHERE status IN ('blocked','retry')")]}


class Worker:
    def __init__(self, ledger, gmail, crm, *, clock=time.time, jitter=lambda: random.uniform(0, 1), test_run_path=None):
        self.ledger, self.gmail, self.crm, self.clock, self.jitter = ledger, gmail, crm, clock, jitter
        self.test_run_path = Path(test_run_path) if test_run_path else ledger.path.parent / "test-runs.json"

    def classify_test(self, payload, raw):
        """A website marker alone cannot select test classification."""
        content = payload["subject"] + "\n" + payload["body"]
        if TEST_MARKER_PREFIX not in content:
            return False
        try:
            registrations = json.loads(private_file(self.test_run_path))
            matches = [v for v in registrations if isinstance(v, dict) and isinstance(v.get("marker"), str)
                       and re.fullmatch(TEST_MARKER_PREFIX + r"[A-F0-9]{24}", v["marker"])
                       and v["marker"] in content]
            if len(matches) != 1:
                raise ValueError()
            match = matches[0]
            received = int(raw["internalDate"]) / 1000
            if not (match["created_at"] <= received <= match["expires_at"] and self.clock() <= match["expires_at"]):
                raise ValueError()
            if payload["extraction"].get("fields", {}).get("email", "").lower() != match["email"].lower():
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError, WorkerError):
            raise WorkerError("internal_test_registration_review_required") from None
        return True

    def policy(self):
        context = self.crm.context()
        matches = [s for s in context.get("sources", []) if s.get("id") == SOURCE and s.get("mailbox") == MAILBOX]
        if len(matches) != 1 or any(not isinstance(matches[0].get(k), list) or not matches[0][k]
                                   or any(not isinstance(v, str) or not v for v in matches[0][k])
                                   for k in ("senders", "subject_prefixes")):
            raise WorkerError("crm_source_configuration_invalid")
        if self.gmail.profile().get("emailAddress", "").lower() != MAILBOX:
            raise WorkerError("gmail_profile_mailbox_mismatch")
        return matches[0]

    def queries(self, policy, *, since=None, before=None):
        def literal(value):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        for sender in policy["senders"]:
            for prefix in policy["subject_prefixes"]:
                yield " ".join(["from:" + literal(sender), "subject:" + literal(prefix), "-in:sent -in:drafts -in:spam -in:trash",
                                *["-subject:" + literal(ref) for ref in EXCLUDED_REFERENCES],
                                *(["after:" + str(int(since))] if since is not None else []),
                                *(["before:" + str(int(before) + 1)] if before is not None else [])])

    def capture(self, raw, role, policy):
        if raw.get("threadId") in EXCLUDED_THREADS or set(raw.get("labelIds", [])) & SKIP_LABELS:
            return
        if int(raw["internalDate"]) / 1000 < self.ledger.meta("activation_at"):
            return
        try:
            payload = normalized(raw)
        except WorkerError as exc:
            self.ledger.store(raw, role, error=exc.code)
            return
        if any(ref in (payload["subject"] + "\n" + payload["body"]).upper() for ref in EXCLUDED_REFERENCES):
            return
        if role == "initial" and not (
            parseaddr(payload["sender"])[1].lower() in [s.lower() for s in policy["senders"]]
            and any(payload["subject"].lower().startswith(s.lower()) for s in policy["subject_prefixes"])
        ):
            return
        if role == "initial":
            try:
                if self.classify_test(payload, raw):
                    payload["internal_test"] = True
            except WorkerError as exc:
                self.ledger.store(raw, role, error=exc.code)
                return
        if role == "reply" and not payload["extraction"].get("reference"):
            thread = self.ledger.db.execute("SELECT reference FROM threads WHERE thread_id=?", (raw["threadId"],)).fetchone()
            payload["extraction"]["reference"] = thread[0] if thread else None
        self.ledger.store(raw, role, payload=payload)

    def collect(self, policy):
        ledger, started = self.ledger, self.clock()
        ledger.initialize(started)
        if not ledger.meta("backlog_recorded", False):
            for query in self.queries(policy, before=ledger.meta("activation_at")):
                for msg in self.gmail.matching(query):
                    if msg.get("threadId") not in EXCLUDED_THREADS:
                        with ledger.db:
                            ledger.db.execute("INSERT OR IGNORE INTO backlog VALUES(?,?,?)", (msg["id"], msg.get("threadId"), started))
            ledger.set_meta("backlog_recorded", True)
        # Daily full reconciliation catches late-indexed mail; the usual scan overlaps five minutes.
        full = started - ledger.meta("last_full_scan", 0) >= 86400
        since = ledger.meta("activation_at") if full else max(ledger.meta("activation_at"), ledger.meta("last_scan", started) - 300)
        for query in self.queries(policy, since=since - 1):
            for msg in self.gmail.matching(query):
                if not ledger.has(msg["id"]) and msg.get("threadId") not in EXCLUDED_THREADS:
                    self.capture(self.gmail.message(msg["id"]), "initial", policy)
        # Full thread IDs survive subject changes and outages. Only unseen inbound originals are fetched.
        for row in ledger.db.execute("SELECT thread_id FROM threads").fetchall():
            for msg in self.gmail.thread(row[0]):
                if not ledger.has(msg["id"]) and not set(msg.get("labelIds", [])) & SKIP_LABELS:
                    self.capture(self.gmail.message(msg["id"]), "reply", policy)
        ledger.set_meta("last_scan", started)
        if full:
            ledger.set_meta("last_full_scan", started)
        ledger.set_meta("last_collection_success", self.clock())

    def backoff(self, attempts, minimum=0):
        return max(minimum, min(3600, 5 * 2 ** min(max(0, attempts - 1), 10)) * (1 + self.jitter() / 4))

    def process(self):
        ledger, now = self.ledger, self.clock()
        rows = ledger.db.execute("""SELECT * FROM messages WHERE status IN ('pending','retry') AND next_attempt<=?
                                   ORDER BY CASE role WHEN 'initial' THEN 0 ELSE 1 END,received_at,message_id""", (now,)).fetchall()
        for row in rows:
            if row["role"] == "reply":
                ready = ledger.db.execute("""SELECT m.lead_id FROM threads t JOIN messages m ON m.message_id=t.initial_id
                                             WHERE t.thread_id=? AND m.status='processed'""", (row["thread_id"],)).fetchone()
                if not ready or not ready[0]:
                    continue
            attempts = row["attempts"] + 1
            with ledger.db:
                ledger.db.execute("UPDATE messages SET attempts=? WHERE message_id=?", (attempts, row["message_id"]))
            try:
                result = self.crm.intake(json.loads(row["payload_json"]))
                status = result.get("processing_status")
                if status == "failed":
                    raise WorkerError("crm_processing_failed")
                if status == "ignored":
                    raise WorkerError("crm_ignored_requires_review")
                if status != "processed" or type(result.get("message_id")) is not int or type(result.get("lead_id")) is not int or result["message_id"] <= 0 or result["lead_id"] <= 0 or type(result.get("replayed")) is not bool:
                    raise WorkerError("crm_result_contract_invalid")
                with ledger.db:
                    ledger.db.execute("""UPDATE messages SET status='processed',error_code=NULL,next_attempt=0,
                                         crm_record_id=?,lead_id=?,replayed=?,processed_at=? WHERE message_id=?""",
                                      (result["message_id"], result["lead_id"], result["replayed"], self.clock(), row["message_id"]))
                ledger.set_meta("last_processing_success", self.clock())
            except WorkerError as exc:
                with ledger.db:
                    ledger.db.execute("UPDATE messages SET status=?,error_code=?,next_attempt=? WHERE message_id=?", (
                        "retry" if exc.transient else "blocked", exc.code,
                        self.clock() + self.backoff(attempts, exc.retry_after) if exc.transient else 0, row["message_id"]))

    def cycle(self):
        ledger, now = self.ledger, self.clock()
        if now < ledger.meta("collection_retry_at", 0):
            return
        try:
            policy = self.policy()  # Read live context before choosing candidates or property IDs.
            self.collect(policy)
            ledger.set_meta("collection_error", None)
            ledger.set_meta("collection_failures", 0)
            ledger.set_meta("collection_retry_at", 0)
            self.process()
        except WorkerError as exc:
            attempts = ledger.meta("collection_failures", 0) + 1
            ledger.set_meta("collection_failures", attempts)
            ledger.set_meta("collection_error", exc.code)
            ledger.set_meta("collection_retry_at", self.clock() + self.backoff(attempts, exc.retry_after) if exc.transient else self.clock() + 3600)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=os.getenv("INQUIRY_WORKER_STATE", "/var/lib/hostaway-inquiry-intake/state.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--interval", type=int, default=60)
    run.add_argument("--once", action="store_true")
    sub.add_parser("status")
    retry = sub.add_parser("retry")
    retry.add_argument("--message-id", required=True)
    retry.add_argument("--extraction-file")
    args = parser.parse_args()
    if args.command == "run" and args.interval < 60:
        parser.error("Collection intervals shorter than 60 seconds are unsupported.")
    try:
        # Acquire secrets before creating an activation ledger. Missing consent never activates intake.
        if args.command == "run":
            gmail = Gmail(credential_path("INQUIRY_GMAIL_CREDENTIAL_FILE", "gmail.json"))
            crm = CRM(credential_path("INQUIRY_CRM_API_KEY_FILE", "crm.key"))
        elif not Path(args.state).is_file():
            raise WorkerError("worker_not_initialized")
        ledger = Ledger(args.state)
        if args.command == "status":
            print(json.dumps(ledger.status(), indent=2));return
        lock_fd = os.open(str(ledger.path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerError("worker_already_running") from None
        if args.command == "retry":
            row = ledger.db.execute("SELECT * FROM messages WHERE message_id=? AND status='blocked'", (args.message_id,)).fetchone()
            if not row or not row["payload_json"]:
                raise WorkerError("blocked_message_requires_original_review")
            payload = json.loads(row["payload_json"])
            if args.extraction_file:
                extraction = json.loads(private_file(args.extraction_file))
                if not isinstance(extraction, dict) or not isinstance(extraction.get("fields"), dict):
                    raise WorkerError("invalid_corrected_extraction")
                payload["extraction"] = extraction
            with ledger.db:
                ledger.db.execute("UPDATE messages SET status='pending',error_code=NULL,next_attempt=0,payload_json=? WHERE message_id=?",
                                  (json.dumps(payload, sort_keys=True), args.message_id))
            print(json.dumps({"message_id": args.message_id, "status": "pending"}));return
        ledger.set_meta("interval_seconds", args.interval)
        worker = Worker(ledger, gmail, crm)
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            started = time.monotonic()
            worker.cycle()
            print(json.dumps(ledger.status()), flush=True)
            if args.once:
                break
            while not stopping and time.monotonic() - started < args.interval:
                time.sleep(max(0.01, min(1, args.interval - (time.monotonic() - started))))
    except (WorkerError, OSError, KeyError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error_code": exc.code if isinstance(exc, WorkerError) else "worker_configuration_or_state_error"}), file=sys.stderr)
        raise SystemExit(78) from None


if __name__ == "__main__":
    main()
