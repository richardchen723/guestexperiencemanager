"""Persisted operational scorecards. HTTP reads never load or aggregate sources.

Own metadata intentionally stays out of application startup migrations. Storage
is installed explicitly before enabling the separate background job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid

from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import declarative_base

from dashboard.kpi.operations import GOALS, as_utc, metric, months_before

logger = logging.getLogger(__name__)
CONTRACT_VERSION = "operational-kpi.v3-provisional-cohorts"
CALCULATION_REVISION = 'operational-kpi.2026-10-24-details.v1'
CADENCE = timedelta(hours=48)
LEASE = timedelta(hours=2)
STATE_KEY = "portfolio"
Base = declarative_base()
json_type = JSON().with_variant(JSONB(), "postgresql")


class OperationalSnapshot(Base):
    __tablename__ = "operational_kpi_snapshots"
    __table_args__ = (UniqueConstraint("contract_version", "scheduled_for_at", name="uq_operational_kpi_slot"), {"schema": "brain"})
    snapshot_id = Column(String, primary_key=True)
    contract_version = Column(String, nullable=False)
    scheduled_for_at = Column(DateTime(timezone=True), nullable=False)
    window_as_of = Column(DateTime(timezone=True), nullable=False)
    calculated_at = Column(DateTime(timezone=True), nullable=False)
    source_as_of = Column(json_type, nullable=False)
    payload = Column(json_type, nullable=False)


class OperationalSnapshotState(Base):
    __tablename__ = "operational_kpi_snapshot_state"
    __table_args__ = {"schema": "brain"}
    state_key = Column(String, primary_key=True)
    contract_version = Column(String, nullable=False)
    generation = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="idle")
    lease_token = Column(String)
    lease_expires_at = Column(DateTime(timezone=True))
    scheduled_for_at = Column(DateTime(timezone=True))
    last_attempt_at = Column(DateTime(timezone=True))
    last_attempt_finished_at = Column(DateTime(timezone=True))
    last_error = Column(String)
    last_good_snapshot_id = Column(String)
    next_due_at = Column(DateTime(timezone=True))


def session_factory():
    from brain.models import get_session
    return get_session()


def iso(value):
    return as_utc(value).isoformat() if value else None


def empty_snapshot(status="not_yet_calculated"):
    """Static placeholders only: this does not run build_operations()."""
    cards = []
    for key in GOALS:
        m = metric(key)
        m.update(display="Not calculated", data_status="not_calculated", summary="Awaiting the first scheduled calculation.", exception_count=0, observation=None)
        if key == "content":
            m.update(display="Deferred", data_status="deferred", goal_status="deferred", target="Team definition pending", summary="Content edit-history and refresh tracking are deferred for team discussion.")
        cards.append(m)
    return {"owner": "Usman", "deadline": "2026-12-31", "as_of": None, "generated_at": None, "metric_contract_version": CONTRACT_VERSION, "window": {"kind": "rolling_three_calendar_months", "ranges": []}, "property_windows": [], "metrics": cards, "excluded_goals": [5, 6, 9], "snapshot_state": {"status": status, "calculated_at": None, "window_as_of": None, "source_as_of": {}, "next_due_at": None, "cadence_hours": 48, "refresh_status": "idle", "last_error": None}}


def read_saved_snapshot(*, factory=session_factory, now=None):
    """One shared-state/snapshot SELECT. Never initializes storage or computes."""
    now = as_utc(now or datetime.now(timezone.utc))
    try:
        with factory() as session:
            row = session.execute(select(OperationalSnapshotState, OperationalSnapshot).outerjoin(OperationalSnapshot, OperationalSnapshot.snapshot_id == OperationalSnapshotState.last_good_snapshot_id).where(OperationalSnapshotState.state_key == STATE_KEY)).first()
            if not row:
                return empty_snapshot()
            state, saved = row
            compatible = bool(saved and saved.contract_version == CONTRACT_VERSION)
            payload = deepcopy(saved.payload) if compatible else empty_snapshot("contract_update_pending" if saved else "not_yet_calculated")
            refresh_status = state.status
            if state.status == "running" and state.lease_expires_at and as_utc(state.lease_expires_at) <= now:
                refresh_status = "interrupted"
            next_due = as_utc(state.next_due_at)
            payload["snapshot_state"] = {
                "status": "available" if compatible else "contract_update_pending" if saved else "not_yet_calculated",
                "snapshot_id": saved.snapshot_id if compatible else None,
                "contract_version": saved.contract_version if compatible else CONTRACT_VERSION,
                "calculated_at": iso(saved.calculated_at) if compatible else None,
                "window_as_of": iso(saved.window_as_of) if compatible else None,
                "source_as_of": saved.source_as_of if compatible else {},
                "next_due_at": iso(next_due), "cadence_hours": 48,
                "schedule_status": "overdue" if next_due and now >= next_due else "scheduled" if next_due else "awaiting_first_run",
                "refresh_status": refresh_status, "last_attempt_at": iso(state.last_attempt_at),
                "last_attempt_finished_at": iso(state.last_attempt_finished_at), "last_error": state.last_error,
                "snapshot_age_seconds": max(0, int((now - as_utc(saved.calculated_at)).total_seconds())) if compatible else None,
            }
            # Upstream stale flags were assessed at window_as_of by the job.
            # Viewing a normal 36-hour-old snapshot must not reclassify it stale.
            return payload
    except SQLAlchemyError:
        logger.warning("Operational KPI snapshot storage is unavailable; no source calculation attempted")
        return empty_snapshot("storage_unavailable")


def _ensure_state(factory):
    with factory() as session:
        if session.get(OperationalSnapshotState, STATE_KEY):
            return
        session.add(OperationalSnapshotState(state_key=STATE_KEY, contract_version=CONTRACT_VERSION, generation=0, status="idle"))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # A concurrent job created the singleton first.


def claim_due_snapshot(*, factory=session_factory, now=None, refresh_revision=None):
    now = as_utc(now or datetime.now(timezone.utc))
    if refresh_revision is not None and refresh_revision != CALCULATION_REVISION:
        raise ValueError('Controlled refresh must name the installed calculation revision')
    _ensure_state(factory)
    with factory() as session:
        state = session.get(OperationalSnapshotState, STATE_KEY)
        running = state.status == "running"
        expired = running and (not state.lease_expires_at or as_utc(state.lease_expires_at) <= now)
        if running and not expired:
            return {"status": "busy"}
        if refresh_revision and state.last_good_snapshot_id:
            saved = session.get(OperationalSnapshot, state.last_good_snapshot_id)
            if saved and saved.contract_version == CONTRACT_VERSION and saved.payload.get('calculation_revision') == refresh_revision:
                return {'status':'already_current','snapshot_id':saved.snapshot_id,'next_due_at':iso(state.next_due_at)}
        changed_contract = state.contract_version != CONTRACT_VERSION
        if not refresh_revision and not expired and not changed_contract and state.next_due_at and now < as_utc(state.next_due_at):
            return {"status": "not_due", "next_due_at": iso(state.next_due_at)}
        # Interrupted attempts recover the same logical slot, with a new fencing
        # token. Missed scheduled slots are not replayed as expensive backfills.
        recover_slot = expired and not changed_contract and state.next_due_at and now < as_utc(state.next_due_at)
        scheduled_for = as_utc(state.scheduled_for_at) if recover_slot and not refresh_revision else now
        next_due = as_utc(state.next_due_at) if (recover_slot or refresh_revision) and state.next_due_at and now < as_utc(state.next_due_at) else scheduled_for + CADENCE
        token, generation = str(uuid.uuid4()), state.generation + 1
        updated = session.execute(update(OperationalSnapshotState).where(OperationalSnapshotState.state_key == STATE_KEY, OperationalSnapshotState.generation == state.generation).values(contract_version=CONTRACT_VERSION, generation=generation, status="running", lease_token=token, lease_expires_at=now + LEASE, scheduled_for_at=scheduled_for, last_attempt_at=now, last_attempt_finished_at=None, last_error=None, next_due_at=next_due))
        if updated.rowcount != 1:
            session.rollback()
            return {"status": "busy"}
        session.commit()
        return {"status": "claimed", "token": token, "generation": generation, "scheduled_for_at": scheduled_for, "window_as_of": now, "next_due_at": next_due}


def validate_payload(payload, window_as_of):
    expected = set(GOALS)
    cards = payload.get("metrics", [])
    if len(cards) != 6 or {m.get("key") for m in cards} != expected:
        raise ValueError("A snapshot must contain all six goals exactly once")
    if as_utc(payload.get("as_of")) != window_as_of:
        raise ValueError("Snapshot window cutoff differs from the claimed calculation")
    for w in payload.get("property_windows", []):
        from datetime import date
        start, end = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
        if start != months_before(end) or w["days"] != (end - start).days:
            raise ValueError("Snapshot contains invalid calendar-month boundaries")
    json.dumps(payload, allow_nan=False)  # No non-finite values or unserializable fields.


def publish_snapshot(claim, payload, *, factory=session_factory, now=None):
    now = as_utc(now or datetime.now(timezone.utc))
    validate_payload(payload, claim["window_as_of"])
    payload = deepcopy(payload)
    payload.update(metric_contract_version=CONTRACT_VERSION, generated_at=iso(now), calculated_at=iso(now))
    sources = {s["name"]: {k: v for k, v in s.items() if k in {"checked_at", "analysis_completed_at", "status", "stale", "provenance", "reason", "rows_read"}} for m in payload["metrics"] for s in m.get("sources", [])}
    with factory() as session:
        updated = session.execute(update(OperationalSnapshotState).where(OperationalSnapshotState.state_key == STATE_KEY, OperationalSnapshotState.generation == claim["generation"], OperationalSnapshotState.lease_token == claim["token"], OperationalSnapshotState.status == "running", OperationalSnapshotState.lease_expires_at > now).values(status="completed", lease_token=None, lease_expires_at=None, last_good_snapshot_id=claim["token"], last_attempt_finished_at=now, last_error=None))
        if updated.rowcount != 1:
            session.rollback()
            return {"status": "superseded"}
        session.add(OperationalSnapshot(snapshot_id=claim["token"], contract_version=CONTRACT_VERSION, scheduled_for_at=claim["scheduled_for_at"], window_as_of=claim["window_as_of"], calculated_at=now, source_as_of=sources, payload=payload))
        session.commit()  # Complete immutable payload + pointer are published atomically.
    return {"status": "published", "snapshot_id": claim["token"], "calculated_at": iso(now), "next_due_at": iso(claim["next_due_at"])}


def fail_snapshot(claim, error, *, factory=session_factory, now=None):
    now = as_utc(now or datetime.now(timezone.utc))
    message = f"Calculation failed ({type(error).__name__}); previous saved results retained."
    with factory() as session:
        session.execute(update(OperationalSnapshotState).where(OperationalSnapshotState.state_key == STATE_KEY, OperationalSnapshotState.generation == claim["generation"], OperationalSnapshotState.lease_token == claim["token"]).values(status="failed", lease_token=None, lease_expires_at=None, last_attempt_finished_at=now, last_error=message))
        session.commit()
    return {"status": "failed", "error": message, "next_due_at": iso(claim["next_due_at"])}
