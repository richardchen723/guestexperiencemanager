"""Scoped authentication, auditing, and rate limiting for external APIs."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import g, make_response, request
from sqlalchemy import text

import dashboard.config as config
from dashboard.api.conventions import api_error
from dashboard.auth.api_keys import authenticate_request_api_key
from dashboard.auth.models import ApiAccessLog, get_session

logger = logging.getLogger(__name__)


def scoped_api_key_required(scope: str):
    """Require one API-key scope and record every authorized request."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            api_key = authenticate_request_api_key(required_scope=scope)
            if not api_key:
                if getattr(g, "api_key_scope_denied", None):
                    return api_error(
                        "insufficient_scope",
                        "This API key does not have access to Guest Issues.",
                        403,
                        details={"required_scope": scope},
                    )
                return api_error(
                    "authentication_required",
                    "Provide a valid API key in X-API-Key or Authorization: Bearer.",
                    401,
                )

            try:
                access = reserve_api_access(api_key.api_key_id, scope)
            except Exception:
                logger.exception("Could not reserve audited API access")
                return api_error(
                    "audit_unavailable",
                    "The API cannot safely process requests right now.",
                    503,
                )

            if not access["allowed"]:
                response = make_response(api_error(
                    "rate_limit_exceeded",
                    "Too many requests. Try again after the rate-limit window resets.",
                    429,
                ))
                _apply_rate_headers(response, access)
                response.headers["Retry-After"] = str(access["retry_after"])
                return response

            try:
                response = make_response(view(*args, **kwargs))
            except Exception:
                finalize_api_access(access["log_id"], status_code=500, response_count=0)
                raise

            finalize_api_access(
                access["log_id"],
                status_code=response.status_code,
                response_count=_response_count(response),
            )
            _apply_rate_headers(response, access)
            return response
        return wrapped
    return decorator


def reserve_api_access(api_key_id: int, scope: str, *, now=None, limit=None) -> dict:
    """Atomically reserve one request slot and create its minimal audit row."""
    now = now or datetime.utcnow()
    limit = limit or config.GUEST_ISSUES_API_RATE_LIMIT_PER_MINUTE
    window_start = now - timedelta(minutes=1)
    session = get_session()
    try:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            # Serialize counters for one credential across all Gunicorn workers.
            session.execute(
                text("SELECT pg_advisory_xact_lock(779481698, :api_key_id)"),
                {"api_key_id": int(api_key_id)},
            )
        accepted = session.query(ApiAccessLog).filter(
            ApiAccessLog.api_key_id == api_key_id,
            ApiAccessLog.created_at >= window_start,
            ApiAccessLog.rate_limited.is_(False),
        ).count()
        allowed = accepted < limit
        reset_at = now + timedelta(minutes=1)
        if accepted:
            oldest = session.query(ApiAccessLog.created_at).filter(
                ApiAccessLog.api_key_id == api_key_id,
                ApiAccessLog.created_at >= window_start,
                ApiAccessLog.rate_limited.is_(False),
            ).order_by(ApiAccessLog.created_at.asc()).first()
            if oldest and oldest[0]:
                reset_at = oldest[0] + timedelta(minutes=1)

        log = ApiAccessLog(
            api_key_id=api_key_id,
            scope=scope,
            method=request.method[:12],
            path=(request.path or "")[:500],
            query_keys_json=json.dumps(sorted(request.args.keys())),
            client_fingerprint=_client_fingerprint(),
            status_code=429 if not allowed else None,
            response_count=0 if not allowed else None,
            rate_limited=not allowed,
            created_at=now,
            completed_at=now if not allowed else None,
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        remaining = max(0, limit - accepted - (1 if allowed else 0))
        retry_after = max(1, int((reset_at - now).total_seconds()) + 1)
        return {
            "allowed": allowed,
            "log_id": log.api_access_log_id,
            "limit": limit,
            "remaining": remaining,
            "reset_at": reset_at,
            "retry_after": retry_after,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def finalize_api_access(log_id: int, *, status_code: int, response_count: int) -> None:
    session = get_session()
    try:
        row = session.query(ApiAccessLog).filter(
            ApiAccessLog.api_access_log_id == log_id
        ).first()
        if row:
            row.status_code = int(status_code)
            row.response_count = max(0, int(response_count))
            row.completed_at = datetime.utcnow()
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Could not finalize API access audit row %s", log_id)
    finally:
        session.close()


def _client_fingerprint() -> str:
    material = "|".join((request.remote_addr or "", request.user_agent.string or ""))
    return hmac.new(
        config.API_KEY_SECRET.encode("utf-8"),
        material.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _response_count(response) -> int:
    if not response.is_json or response.status_code >= 400:
        return 0
    payload = response.get_json(silent=True)
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return len(payload["data"])
    return 1 if isinstance(payload, dict) and payload.get("data") is not None else 0


def _apply_rate_headers(response, access: dict) -> None:
    response.headers["X-RateLimit-Limit"] = str(access["limit"])
    response.headers["X-RateLimit-Remaining"] = str(access["remaining"])
    response.headers["X-RateLimit-Reset"] = access["reset_at"].isoformat() + "Z"
    response.headers["Cache-Control"] = "private, no-store"
