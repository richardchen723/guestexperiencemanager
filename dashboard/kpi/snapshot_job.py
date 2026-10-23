"""Explicit background-only entry point; never imported to calculate by HTTP."""
from datetime import datetime, timezone
import argparse
import json

from dashboard.kpi.snapshot_store import Base, claim_due_snapshot, fail_snapshot, publish_snapshot, session_factory


def run_due_snapshot(*, factory=session_factory, now=None, clock=None, loader=None, calculator=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    claim = claim_due_snapshot(factory=factory, now=now or clock())
    if claim["status"] != "claimed":
        return claim
    try:
        if loader is None:
            from dashboard.kpi.operations_data import load_operational_evidence
            loader = load_operational_evidence
        if calculator is None:
            from dashboard.kpi.operations import build_operations
            calculator = build_operations
        evidence = loader(now=claim["window_as_of"])
        if any(s.get("status") == "error" for s in evidence.get("sources", {}).values()):
            raise RuntimeError("Source read failed")
        payload = calculator(evidence, as_of=claim["window_as_of"])
        return publish_snapshot(claim, payload, factory=factory, now=clock())
    except Exception as exc:
        return fail_snapshot(claim, exc, factory=factory, now=clock())


def main():
    parser = argparse.ArgumentParser(description="Persist operational KPI results on a 48-hour cadence")
    parser.add_argument("action", choices=("init-storage", "run-due"))
    args = parser.parse_args()
    if args.action == "init-storage":
        # Separate explicit action: never called by HTTP or normal job startup.
        from brain.models import get_engine
        Base.metadata.create_all(get_engine())
        result = {"status": "storage_initialized"}
    else:
        result = run_due_snapshot()
    print(json.dumps(result, default=str))
    if result["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
