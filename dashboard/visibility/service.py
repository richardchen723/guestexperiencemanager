"""Bounded report contract; immutable corrections and idempotent publishing."""
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit, parse_qsl

from sqlalchemy import select, func, text
from .models import Report

SITE = "enchantedhavens.com"
PROVIDERS = {"chatgpt", "gemini", "claude", "muse"}
MAX_BYTES = 2_000_000


class ReportError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def string(value, label, limit=2000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ReportError(f"{label}: provide text of at most {limit} characters.")
    return value.strip()


def integer(value, label, maximum=100000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ReportError(f"{label}: provide a nonnegative integer.")
    return value


def choice(value, values, label):
    if not isinstance(value, str) or value not in values:
        raise ReportError(f"{label}: unsupported value.")
    return value


def items(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ReportError(f"{label}: provide at most {maximum} entries.")
    if any(not isinstance(x, dict) for x in value):
        raise ReportError(f"{label}: entries must be objects.")
    return value


def day(value, label):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ReportError(f"{label}: use YYYY-MM-DD.")


def timestamp(value, label):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not parsed.tzinfo:
            raise ValueError()
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ReportError(f"{label}: include an ISO timestamp with timezone.")


def url(value):
    string(value, "Evidence URL", 4000)
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in value):
            raise ValueError()
        if any(re.search(r"token|secret|password|signature|api.?key|credential|authorization", k, re.I) for k, _ in parse_qsl(parsed.query)):
            raise ValueError()
    except ValueError:
        raise ReportError("Evidence links must be HTTPS and must not contain credentials or signed tokens.")
    return value


def ratio(value, label):
    if not isinstance(value, dict) or set(value) != {"n", "d"}:
        raise ReportError(f"{label}: include numerator n and denominator d.")
    d = integer(value["d"], label)
    n = value["n"]
    if d == 0:
        if n is not None:
            raise ReportError(f"{label}: an unmeasured result must be null / 0.")
    elif integer(n, label) > d:
        raise ReportError(f"{label}: numerator exceeds denominator.")


def validate(data):
    if not isinstance(data, dict):
        raise ReportError("Provide one report object.")
    required = {"schema_version", "site", "week_start", "kind", "status", "title", "summary", "observed_start", "observed_end", "protocol", "comparison", "providers", "search_metrics", "insights", "actions", "changes", "limitations", "samples", "sources"}
    if set(data) != required or data.get("schema_version") != 1 or data.get("site") != SITE:
        raise ReportError("Unsupported report schema or site; use the documented contract.")
    week = day(data["week_start"], "Week start")
    if week.weekday() != 0:
        raise ReportError("Week start must be a Monday.")
    start = timestamp(data["observed_start"], "Observation start")
    end = timestamp(data["observed_end"], "Observation end")
    if end < start or end > datetime.now(timezone.utc):
        raise ReportError("Observation dates must be ordered and cannot be in the future.")
    choice(data["kind"], {"baseline", "weekly"}, "Report kind")
    choice(data["status"], {"complete", "partial", "blocked"}, "Status")
    string(data["title"], "Title", 160)
    string(data["summary"], "Summary", 1000)
    string(data["protocol"], "Protocol and measurement context", 3000)
    comparison = data["comparison"]
    if not isinstance(comparison, dict) or set(comparison) != {"basis", "note"}:
        raise ReportError("Comparison needs basis and note.")
    choice(comparison["basis"], {"baseline", "matched", "incomparable"}, "Comparison")
    string(comparison["note"], "Comparison note", 3000)
    providers = items(data["providers"], "Providers", 4)
    for p in providers:
        choice(p.get("id"), PROVIDERS, "Provider ID")
    if {x.get("id") for x in providers} != PROVIDERS or len(providers) != 4:
        raise ReportError("Retain all four planned providers, including unavailable ones.")
    for p in providers:
        choice(p.get("citation_basis"), {"complete_only", "legacy", "unavailable"}, "Citation basis")
        for field in ("label", "model", "mode", "cohort", "notes"):
            string(p.get(field), f"Provider {field}", 3000)
        day(p.get("observed_date"), "Provider observation date")
        for field in ("planned", "scored", "blocked", "errors", "excluded", "incorrect_facts", "unverified_facts", "retired_errors"):
            integer(p.get(field), field, 10000)
        if p["planned"] != p["scored"] + p["blocked"] + p["errors"] + p["excluded"]:
            raise ReportError("Provider coverage must account for every planned sample.")
        for field in ("mentions", "top_three", "citations", "facts"):
            ratio(p.get(field), field)
            if field != "facts" and p[field]["d"] > p["scored"]:
                raise ReportError("Response metric denominator exceeds scored coverage.")
        if p["retired_errors"] > p["scored"] or p["incorrect_facts"] + p["unverified_facts"] > p["facts"]["d"]:
            raise ReportError("Quality counts exceed their measured denominators.")
        if (p["facts"]["n"] or 0) + p["incorrect_facts"] + p["unverified_facts"] != p["facts"]["d"]:
            raise ReportError("Supported, incorrect and unverified facts must reconcile.")
    if data["status"] == "complete" and any(p["scored"] != p["planned"] for p in providers):
        raise ReportError("A partially measured run cannot be labeled complete.")
    if data["status"] == "blocked" and any(p["scored"] for p in providers):
        raise ReportError("Use partial when any planned answers were scored.")
    for metric in items(data["search_metrics"], "Search / business metrics", 40):
        for field in ("system", "metric", "unit", "period", "segment", "note"):
            string(metric.get(field), field, 3000)
        value = metric.get("value")
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ReportError("Metric value must be a finite nonnegative number, or null if unavailable.")
        url(metric.get("source"))
    for insight in items(data["insights"], "Insights", 20):
        choice(insight.get("type"), {"observation", "hypothesis"}, "Insight type")
        for field in ("title", "evidence", "implication"):
            string(insight.get(field), field, 3000)
    seen_actions = set()
    for action in items(data["actions"], "Actions", 30):
        key = string(action.get("id"), "Action ID", 100)
        if key in seen_actions:
            raise ReportError("Action IDs must be unique.")
        seen_actions.add(key)
        choice(action.get("priority"), {"P1", "P2", "P3"}, "Priority")
        choice(action.get("status"), {"proposed", "ready", "in_progress", "waiting", "implemented", "verified", "rejected"}, "Action status")
        choice(action.get("confidence"), {"high", "medium", "low"}, "Confidence")
        for field in ("title", "why", "next_step", "success", "owner", "effort", "review_on", "evidence", "follow_up"):
            string(action.get(field), field, 3000)
    for field in ("changes", "limitations"):
        if not isinstance(data[field], list) or len(data[field]) > 30:
            raise ReportError(f"{field}: at most 30 notes.")
        for value in data[field]:
            string(value, field, 3000)
    for source in items(data["sources"], "Sources", 30):
        string(source.get("label"), "Source label", 200)
        url(source.get("url"))
    seen = set()
    for sample in items(data["samples"], "Samples", 200):
        choice(sample.get("provider"), PROVIDERS, "Sample provider")
        key = (sample["provider"], string(sample.get("prompt_id"), "Prompt ID", 100), integer(sample.get("trial"), "Trial", 10))
        if key in seen:
            raise ReportError("Duplicate sample.")
        seen.add(key)
        choice(sample.get("status"), {"completed", "blocked", "error", "excluded"}, "Sample status")
        for field, limit in (("prompt", 6000), ("answer", 60000), ("notes", 6000), ("model", 300), ("mode", 300), ("started_at", 80), ("completed_at", 80), ("search_evidence", 1500)):
            string(sample.get(field), field, limit, empty=True)
        if sample["status"] == "completed" and not sample["answer"].strip():
            raise ReportError("A scored sample requires its captured answer.")
        if sample["status"] == "completed":
            if timestamp(sample["completed_at"], "Sample completed") < timestamp(sample["started_at"], "Sample started"):
                raise ReportError("Sample timestamps are reversed.")
        if sample.get("citation_complete") not in (True, False, None):
            raise ReportError("Citation capture must be true, false, or null for legacy evidence.")
        links = sample.get("citations")
        if not isinstance(links, list) or len(links) > 100:
            raise ReportError("At most 100 citations per sample.")
        for link in links:
            url(link)
    for p in providers:
        samples = [s for s in data["samples"] if s["provider"] == p["id"]]
        for status, field in (("completed", "scored"), ("blocked", "blocked"), ("error", "errors"), ("excluded", "excluded")):
            if sum(s["status"] == status for s in samples) != p[field]:
                raise ReportError(f"{p['label']}: sample evidence must reconcile with {field}.")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > MAX_BYTES:
        raise ReportError("Keep each report below 2 MB.", 413)
    return json.loads(encoded), encoded


def query():
    return select(Report).where(Report.site == SITE)


def save(db, data, command_key, expected_revision, actor):
    payload, encoded = validate(data)
    integer(expected_revision, "Expected revision")
    string(command_key, "Idempotency key", 180)
    string(actor, "Actor", 160)
    fingerprint = hashlib.sha256((actor + "\n" + encoded).encode()).hexdigest()
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(84020261029)"))
    replay = db.scalar(query().where(Report.command_key == command_key))
    if replay:
        if replay.fingerprint != fingerprint:
            raise ReportError("This command key was already used for different content.", 409)
        return {"id": replay.id, "revision": replay.revision, "replayed": True}
    week = day(payload["week_start"], "Week start")
    current = db.scalar(query().where(Report.week_start == week).order_by(Report.revision.desc()).limit(1))
    if (current.revision if current else 0) != expected_revision:
        raise ReportError("A newer report exists. Read it before publishing a correction.", 409)
    summary = {k: payload[k] for k in ("site", "week_start", "kind", "status", "title", "summary", "comparison", "providers", "actions")}
    record = Report(site=SITE, week_start=week, revision=expected_revision + 1, command_key=command_key, fingerprint=fingerprint, actor=actor, payload=payload, summary=summary)
    db.add(record)
    db.flush()
    return {"id": record.id, "revision": record.revision, "replayed": False}


def serialize(record):
    return {"id": record.id, "week_start": record.week_start.isoformat(), "revision": record.revision, "created_at": record.created_at.isoformat(), "actor": record.actor, **record.payload}


def latest(db, page=1):
    ids = select(func.max(Report.id)).where(Report.site == SITE).group_by(Report.week_start)
    count = db.scalar(select(func.count()).select_from(ids.subquery()))
    rows = db.execute(select(Report.id, Report.revision, Report.created_at, Report.summary).where(Report.site == SITE, Report.id.in_(ids)).order_by(Report.week_start.desc()).offset((page - 1) * 20).limit(20))
    return [{**r.summary, "id": r.id, "revision": r.revision, "created_at": r.created_at.isoformat()} for r in rows], count


def rate(value):
    if not value or not value["d"] or value["n"] is None:
        return "Not measured"
    return f"{value['n']}/{value['d']} · {value['n'] / value['d'] * 100:.1f}%"
