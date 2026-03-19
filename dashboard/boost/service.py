#!/usr/bin/env python3
"""
Boost service layer - orchestrates automation sessions, aggregates rankings,
and supports both manual and scheduled execution.
"""

import asyncio
import logging
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from dashboard.boost.models import (
    BoostCampaign,
    BoostRanking,
    BoostSession,
    get_session,
    init_boost_database,
)
from dashboard.boost.proxy_manager import get_random_proxy, mark_proxy_failed, mark_proxy_used
from dashboard.boost.automation import run_boost_session

logger = logging.getLogger(__name__)

# Track running sessions so the UI can poll status
_running_sessions: Dict[int, Dict[str, Any]] = {}
_lock = threading.Lock()


def get_running_status(campaign_id: int) -> Optional[Dict[str, Any]]:
    """Check if a session is currently running for a campaign.
    Returns a JSON-safe dict (excludes the cancel_event)."""
    with _lock:
        info = _running_sessions.get(campaign_id)
        if info is None:
            return None
        return {k: v for k, v in info.items() if k != "cancel_event"}


def _set_running(campaign_id: int, session_id: int, cancel_event: threading.Event):
    with _lock:
        _running_sessions[campaign_id] = {
            "session_id": session_id,
            "started_at": datetime.utcnow().isoformat(),
            "status": "running",
            "cancel_event": cancel_event,
        }


def _clear_running(campaign_id: int):
    with _lock:
        _running_sessions.pop(campaign_id, None)


def stop_session(campaign_id: int) -> Dict[str, Any]:
    """Signal a running session to stop gracefully."""
    with _lock:
        info = _running_sessions.get(campaign_id)
        if not info:
            return {"error": "No running session for this campaign"}
        cancel_event = info.get("cancel_event")
        if cancel_event:
            cancel_event.set()
            info["status"] = "cancelling"
        return {"ok": True, "session_id": info["session_id"]}


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

def create_campaign(data: Dict[str, Any]) -> Dict[str, Any]:
    session = get_session()
    try:
        campaign = BoostCampaign(
            name=data["name"],
            platform=data.get("platform", "airbnb"),
            search_area=data["search_area"],
            date_window_start=date.fromisoformat(data["date_window_start"]),
            date_window_end=date.fromisoformat(data["date_window_end"]),
            min_nights=data.get("min_nights", 2),
            max_nights=data.get("max_nights", 5),
            target_listing_id=data.get("target_listing_id"),
            target_listing_url=data.get("target_listing_url"),
            target_listing_name=data.get("target_listing_name"),
            target_lat=data.get("target_lat"),
            target_lng=data.get("target_lng"),
            sessions_per_day=data.get("sessions_per_day", 3),
            is_active=data.get("is_active", True),
        )
        session.add(campaign)
        session.commit()
        result = campaign.to_dict()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_campaign(campaign_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session = get_session()
    try:
        campaign = session.query(BoostCampaign).get(campaign_id)
        if not campaign:
            return None

        updatable = [
            "name", "platform", "search_area", "target_listing_id",
            "target_listing_url", "target_listing_name", "target_lat",
            "target_lng", "sessions_per_day", "is_active",
            "min_nights", "max_nights",
        ]
        for field in updatable:
            if field in data:
                setattr(campaign, field, data[field])

        if "date_window_start" in data:
            campaign.date_window_start = date.fromisoformat(data["date_window_start"])
        if "date_window_end" in data:
            campaign.date_window_end = date.fromisoformat(data["date_window_end"])

        campaign.updated_at = datetime.utcnow()
        session.commit()
        return campaign.to_dict()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_campaign(campaign_id: int) -> bool:
    session = get_session()
    try:
        campaign = session.query(BoostCampaign).get(campaign_id)
        if not campaign:
            return False
        session.delete(campaign)
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    session = get_session()
    try:
        campaign = session.query(BoostCampaign).get(campaign_id)
        return campaign.to_dict() if campaign else None
    finally:
        session.close()


def list_campaigns() -> List[Dict[str, Any]]:
    session = get_session()
    try:
        campaigns = session.query(BoostCampaign).order_by(BoostCampaign.id.desc()).all()
        return [c.to_dict() for c in campaigns]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Session execution
# ---------------------------------------------------------------------------

def trigger_session(campaign_id: int, headless: bool = True) -> Dict[str, Any]:
    """
    Start a boost session in a background thread.
    Returns immediately with the session record.
    """
    if get_running_status(campaign_id):
        return {"error": "A session is already running for this campaign"}

    session = get_session()
    try:
        campaign = session.query(BoostCampaign).get(campaign_id)
        if not campaign:
            return {"error": "Campaign not found"}
        campaign_dict = campaign.to_dict()
    finally:
        session.close()

    # Create session record
    db_session = get_session()
    try:
        boost_sess = BoostSession(
            campaign_id=campaign_id,
            status="running",
            started_at=datetime.utcnow(),
        )
        db_session.add(boost_sess)
        db_session.commit()
        sess_id = boost_sess.id
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()

    cancel_event = threading.Event()
    _set_running(campaign_id, sess_id, cancel_event)

    # Run in background thread
    thread = threading.Thread(
        target=_run_session_thread,
        args=(campaign_id, sess_id, campaign_dict, headless, cancel_event),
        daemon=True,
    )
    thread.start()

    return {"session_id": sess_id, "status": "running"}


def _run_session_thread(
    campaign_id: int,
    sess_id: int,
    campaign_dict: Dict,
    headless: bool,
    cancel_event: threading.Event,
):
    """Background thread that runs the Playwright automation."""
    try:
        proxy_obj = get_random_proxy()
        proxy_dict = None
        proxy_display = None
        if proxy_obj:
            proxy_dict = proxy_obj.to_playwright_proxy()
            proxy_display = f"{proxy_obj.protocol}://{proxy_obj.host}:{proxy_obj.port}"
            mark_proxy_used(proxy_obj.id)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                run_boost_session(
                    campaign_dict,
                    proxy_dict=proxy_dict,
                    headless=headless,
                    cancel_event=cancel_event,
                )
            )
        finally:
            loop.close()

        cancelled = cancel_event.is_set()

        # Update session record
        db_session = get_session()
        try:
            sess = db_session.query(BoostSession).get(sess_id)
            if sess:
                sess.proxy_used = proxy_display
                sess.search_dates = result.get("search_dates")
                sess.target_found = result.get("target_found", False)
                sess.target_page_number = result.get("target_page_number")
                sess.target_position_on_page = result.get("target_position_on_page")
                sess.total_pages_browsed = result.get("total_pages_browsed", 0)
                sess.other_listings_browsed = result.get("other_listings_browsed", [])
                sess.session_log = result.get("session_log", [])
                sess.error_message = result.get("error_message")
                if cancelled:
                    sess.status = "cancelled"
                elif result.get("error_message"):
                    sess.status = "failed"
                else:
                    sess.status = "completed"
                sess.completed_at = datetime.utcnow()
                db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
            db_session.close()

        # Update daily ranking aggregate
        if result.get("target_found"):
            _update_ranking(campaign_id, date.today())

        if result.get("error_message") and proxy_obj:
            mark_proxy_failed(proxy_obj.id)

    except Exception as e:
        logger.exception(f"Boost session thread error: {e}")
        db_session = get_session()
        try:
            sess = db_session.query(BoostSession).get(sess_id)
            if sess:
                sess.status = "failed"
                sess.error_message = str(e)
                sess.completed_at = datetime.utcnow()
                db_session.commit()
        except Exception:
            db_session.rollback()
        finally:
            db_session.close()
    finally:
        _clear_running(campaign_id)


# ---------------------------------------------------------------------------
# Ranking aggregation
# ---------------------------------------------------------------------------

def _update_ranking(campaign_id: int, for_date: date):
    """Recompute the daily ranking aggregate from all sessions that day."""
    db_session = get_session()
    try:
        sessions = (
            db_session.query(BoostSession)
            .filter(
                BoostSession.campaign_id == campaign_id,
                BoostSession.status == "completed",
                func.date(BoostSession.started_at) == for_date,
            )
            .all()
        )

        total = len(sessions)
        found_sessions = [s for s in sessions if s.target_found and s.target_page_number]
        found_count = len(found_sessions)

        if found_count == 0:
            positions = []
        else:
            positions = [
                (s.target_page_number - 1) * 20 + (s.target_position_on_page or 1)
                for s in found_sessions
            ]

        ranking = (
            db_session.query(BoostRanking)
            .filter_by(campaign_id=campaign_id, date=for_date)
            .first()
        )
        if not ranking:
            ranking = BoostRanking(campaign_id=campaign_id, date=for_date)
            db_session.add(ranking)

        ranking.sessions_count = total
        ranking.found_count = found_count
        if positions:
            ranking.avg_position = sum(positions) / len(positions)
            ranking.avg_page_number = sum(s.target_page_number for s in found_sessions) / found_count
            ranking.best_position = min(positions)
            ranking.worst_position = max(positions)
        else:
            ranking.avg_position = None
            ranking.avg_page_number = None
            ranking.best_position = None
            ranking.worst_position = None

        db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()


# ---------------------------------------------------------------------------
# Data retrieval for dashboard
# ---------------------------------------------------------------------------

def get_sessions(campaign_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    db_session = get_session()
    try:
        sessions = (
            db_session.query(BoostSession)
            .filter_by(campaign_id=campaign_id)
            .order_by(BoostSession.started_at.desc())
            .limit(limit)
            .all()
        )
        return [s.to_dict() for s in sessions]
    finally:
        db_session.close()


def get_rankings(campaign_id: int, limit: int = 90) -> List[Dict[str, Any]]:
    db_session = get_session()
    try:
        rankings = (
            db_session.query(BoostRanking)
            .filter_by(campaign_id=campaign_id)
            .order_by(BoostRanking.date.desc())
            .limit(limit)
            .all()
        )
        return [r.to_dict() for r in rankings]
    finally:
        db_session.close()


def get_dashboard_stats(campaign_id: int) -> Dict[str, Any]:
    """Compute summary stats for the dashboard header cards."""
    db_session = get_session()
    try:
        today = date.today()

        today_ranking = (
            db_session.query(BoostRanking)
            .filter_by(campaign_id=campaign_id, date=today)
            .first()
        )

        # 7-day average
        from datetime import timedelta
        week_ago = today - timedelta(days=7)
        recent_rankings = (
            db_session.query(BoostRanking)
            .filter(
                BoostRanking.campaign_id == campaign_id,
                BoostRanking.date >= week_ago,
                BoostRanking.avg_position.isnot(None),
            )
            .all()
        )

        # All-time best
        best_ever = (
            db_session.query(func.min(BoostRanking.best_position))
            .filter(
                BoostRanking.campaign_id == campaign_id,
                BoostRanking.best_position.isnot(None),
            )
            .scalar()
        )

        total_sessions = (
            db_session.query(func.count(BoostSession.id))
            .filter_by(campaign_id=campaign_id)
            .scalar()
        )

        week_avg = None
        if recent_rankings:
            positions = [r.avg_position for r in recent_rankings if r.avg_position]
            if positions:
                week_avg = round(sum(positions) / len(positions), 1)

        return {
            "today_position": round(today_ranking.avg_position, 1) if today_ranking and today_ranking.avg_position else None,
            "today_sessions": today_ranking.sessions_count if today_ranking else 0,
            "today_found_rate": (
                round(today_ranking.found_count / today_ranking.sessions_count * 100)
                if today_ranking and today_ranking.sessions_count > 0
                else None
            ),
            "week_avg_position": week_avg,
            "best_position_ever": best_ever,
            "total_sessions": total_sessions or 0,
        }
    finally:
        db_session.close()


# ---------------------------------------------------------------------------
# Scheduled execution (called by cron / systemd)
# ---------------------------------------------------------------------------

def run_scheduled_sessions():
    """
    Check all active campaigns and run sessions up to their daily quota.
    Intended to be called by cron or systemd timer several times per day.
    """
    init_boost_database()

    db_session = get_session()
    try:
        campaigns = (
            db_session.query(BoostCampaign)
            .filter_by(is_active=True)
            .all()
        )
        campaign_dicts = [c.to_dict() for c in campaigns]
    finally:
        db_session.close()

    today = date.today()

    for campaign_dict in campaign_dicts:
        cid = campaign_dict["id"]

        # Check how many sessions already ran today
        db_session = get_session()
        try:
            today_count = (
                db_session.query(func.count(BoostSession.id))
                .filter(
                    BoostSession.campaign_id == cid,
                    func.date(BoostSession.started_at) == today,
                )
                .scalar()
            )
        finally:
            db_session.close()

        remaining = campaign_dict["sessions_per_day"] - (today_count or 0)
        if remaining <= 0:
            logger.info(f"Campaign {cid} ({campaign_dict['name']}): daily quota reached")
            continue

        # Run one session per scheduled invocation
        logger.info(f"Campaign {cid} ({campaign_dict['name']}): starting scheduled session")
        trigger_session(cid, headless=True)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Boost service CLI")
    parser.add_argument("--run-scheduled", action="store_true", help="Run scheduled sessions for all active campaigns")
    parser.add_argument("--init-db", action="store_true", help="Initialize boost database tables")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.init_db:
        init_boost_database()
        print("Boost database initialized")
    elif args.run_scheduled:
        run_scheduled_sessions()
    else:
        parser.print_help()
