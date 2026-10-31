"""Validate Hostaway timezone aliases without guessing a property's timezone."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_FIELDS = ("timeZoneName", "timezoneName")


def valid_timezone_name(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    return value


def resolve_listing_timezone(payload, *, existing=None):
    """Resolve native and legacy keys, preserving valid stored data on gaps.

    A valid zone means the supplied IANA identifier can be used for calendar
    arithmetic; it does not certify other listing fields or KPI evidence.
    Conflicting valid aliases require review rather than silently picking one.
    """
    candidates, issues = {}, []
    supplied = False
    for field in TIMEZONE_FIELDS:
        raw = payload.get(field)
        if raw is None or raw == "":
            continue
        supplied = True
        value = valid_timezone_name(raw)
        if value:
            candidates[field] = value
        else:
            issues.append(f"invalid:{field}")
    conflict = len(set(candidates.values())) > 1
    if conflict:
        issues.append("conflicting_timezone_fields")
    if candidates and not conflict:
        field, value = next(iter(candidates.items()))
        return {"value": value, "source_field": field, "status": "valid", "issues": issues}
    previous = valid_timezone_name(existing)
    if previous:
        return {"value": previous, "source_field": "stored", "status": "preserved", "issues": issues or ["source_timezone_missing"]}
    return {"value": None, "source_field": None, "status": "conflict" if conflict else "invalid" if supplied else "missing", "issues": issues or ["source_timezone_missing"]}
