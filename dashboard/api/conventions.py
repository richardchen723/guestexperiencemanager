"""Shared conventions for versioned, externally consumed JSON APIs."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from math import ceil

from flask import jsonify


class ApiParameterError(ValueError):
    """A client-correctable query parameter error."""

    def __init__(self, message: str, *, parameter: str | None = None):
        super().__init__(message)
        self.parameter = parameter


def api_error(code: str, message: str, status: int, *, details=None):
    payload = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return jsonify(payload), status


def parse_positive_int(value, *, parameter: str, default: int, maximum: int | None = None) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiParameterError(f"{parameter} must be an integer", parameter=parameter) from exc
    if parsed < 1:
        raise ApiParameterError(f"{parameter} must be at least 1", parameter=parameter)
    if maximum is not None and parsed > maximum:
        raise ApiParameterError(
            f"{parameter} must be {maximum} or fewer",
            parameter=parameter,
        )
    return parsed


def parse_api_datetime(value, *, parameter: str, end_of_day: bool = False) -> datetime | None:
    """Parse ISO 8601 input and return a naive UTC datetime for database comparisons."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            return datetime.combine(parsed_date, time.max if end_of_day else time.min)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiParameterError(
            f"{parameter} must be an ISO 8601 date or datetime",
            parameter=parameter,
        ) from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def pagination_payload(*, page: int, per_page: int, total: int) -> dict:
    pages = ceil(total / per_page) if total else 0
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_previous": page > 1 and total > 0,
        "has_next": page < pages,
        "previous_page": page - 1 if page > 1 and total > 0 else None,
        "next_page": page + 1 if page < pages else None,
    }
