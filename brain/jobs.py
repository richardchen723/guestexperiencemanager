#!/usr/bin/env python3
"""CLI jobs for STR Signal Brain timers."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from brain.models import init_brain_database
from brain.services import BrainRunService
from utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description="Run STR Signal Brain jobs")
    parser.add_argument(
        "job",
        choices=[
            "morning",
            "afternoon",
            "nightly",
            "manual",
            "aggregate",
            "audit",
            "intelligence-pack",
            "intelligence-import",
            "intelligence-list",
            "stay-outcomes",
        ],
        help="Job to run",
    )
    parser.add_argument("--deliver", action="store_true", help="Deliver generated briefs by configured channels")
    parser.add_argument(
        "--pull",
        action="store_true",
        help="Refresh due Hostaway sources, bounded recent Hostaway messages, PriceLabs, calendar, booking-health, and memory snapshots before aggregate",
    )
    parser.add_argument(
        "--deep-hostaway",
        action="store_true",
        help="Force the expensive Hostaway sync path, including message backfill when normally skipped",
    )
    parser.add_argument("--window-days", type=int, default=30, help="Analysis window for intelligence packets")
    parser.add_argument("--packet-dir", help="Directory where intelligence packet files should be written")
    parser.add_argument("--insights-file", help="JSON file of Codex-authored insights to import")
    parser.add_argument("--run-key", help="Codex intelligence run key")
    parser.add_argument("--category", help="Filter stored intelligence by category")
    parser.add_argument("--limit", type=int, default=100, help="Result limit for list jobs")
    args = parser.parse_args()

    setup_logging()
    init_brain_database()
    if args.job == "aggregate":
        from brain.aggregator import BrainDataAggregator

        source_refresh_result = None
        if args.pull:
            source_runner = BrainRunService()
            try:
                source_refresh_result = source_runner.refresh_source_snapshots(
                    pull_hostaway=True,
                    force_hostaway=args.deep_hostaway,
                )
            finally:
                source_runner.close()

        runner = BrainDataAggregator()
        try:
            result = runner.run(mode="manual", pull=False)
            if source_refresh_result is not None:
                result["pull_result"] = source_refresh_result
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            runner.close()
        return

    if args.job == "audit":
        from brain.services import BrainService, system_user

        service = BrainService(system_user())
        try:
            result = service.data_foundation_audit()
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            service.close()
        return

    if args.job == "intelligence-pack":
        from brain.intelligence import CodexIntelligenceService

        service = CodexIntelligenceService()
        try:
            result = service.prepare_packet(
                window_days=args.window_days,
                run_key=args.run_key,
                packet_dir=args.packet_dir,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            service.close()
        return

    if args.job == "intelligence-import":
        from brain.intelligence import CodexIntelligenceService

        if not args.insights_file:
            raise SystemExit("--insights-file is required for intelligence-import")
        payload = json.loads(Path(args.insights_file).read_text(encoding="utf-8"))
        service = CodexIntelligenceService()
        try:
            result = service.import_insights(payload, run_key=args.run_key)
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            service.close()
        return

    if args.job == "intelligence-list":
        from brain.intelligence import CodexIntelligenceService

        service = CodexIntelligenceService()
        try:
            result = service.list_insights(category=args.category, limit=args.limit)
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            service.close()
        return

    if args.job == "stay-outcomes":
        from brain.stay_outcomes import StayOutcomeClassificationService

        service = StayOutcomeClassificationService()
        try:
            result = service.refresh(window_days=max(args.window_days, 1), max_stays=max(args.limit, 1))
            print(json.dumps(result, indent=2, sort_keys=True))
        finally:
            service.close()
        return

    runner = BrainRunService()
    try:
        result = runner.run(run_type=args.job, deliver=args.deliver)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        runner.close()


if __name__ == "__main__":
    logging.getLogger(__name__).info("Starting Brain job")
    main()
