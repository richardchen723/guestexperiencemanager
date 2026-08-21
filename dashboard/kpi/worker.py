#!/usr/bin/env python3
"""Durable database-backed worker for manual KPI source refreshes."""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Any

from dashboard.kpi.refresh import (
    KPI_REFRESH_STALE_AFTER,
    claim_next_kpi_refresh,
    recover_stale_kpi_refreshes,
    refresh_listing_ids,
    run_kpi_refresh_async,
)
from dashboard.sync.job_manager import get_job_manager
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def process_next_kpi_refresh() -> bool:
    """Claim and execute one queued refresh, returning whether work was found."""
    job = claim_next_kpi_refresh()
    if job is None:
        return False

    job_id = str(job["job_id"])
    listing_ids = refresh_listing_ids(job)
    if not listing_ids:
        logger.error("KPI refresh %s has no persisted listing scope", job_id)
        get_job_manager().update_job_status(
            job_id,
            "error",
            error="The queued refresh has no property scope. Please start a new refresh.",
        )
        return True

    logger.info(
        "KPI worker claimed refresh %s for %s properties",
        job_id,
        len(listing_ids),
    )
    run_kpi_refresh_async(job_id, listing_ids)
    return True


def run_worker(
    *,
    poll_interval_seconds: float = 2.0,
    stop_event: Any | None = None,
    once: bool = False,
) -> None:
    """Poll the durable queue until shutdown; stale work is automatically resumed."""
    stop_event = stop_event or threading.Event()
    logger.info("KPI refresh worker started")

    while not stop_event.is_set():
        try:
            recovered = recover_stale_kpi_refreshes(stale_after=KPI_REFRESH_STALE_AFTER)
            if recovered:
                logger.warning("Requeued %s interrupted KPI refresh job(s)", recovered)
            found_work = process_next_kpi_refresh()
        except Exception:
            logger.exception("KPI worker loop failed; polling will continue")
            found_work = False

        if once:
            return
        if not found_work:
            stop_event.wait(max(0.2, float(poll_interval_seconds)))

    logger.info("KPI refresh worker stopped")


def main() -> None:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    log_file = os.getenv("KPI_WORKER_LOG_FILE", os.path.join(project_root, "logs", "kpi-worker.log"))
    setup_logging(log_file=log_file)
    stop_event = threading.Event()

    def request_stop(signum, _frame):
        logger.info(
            "KPI worker received signal %s; it will exit after the active refresh finishes",
            signum,
        )
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run_worker(stop_event=stop_event)


if __name__ == "__main__":
    main()
