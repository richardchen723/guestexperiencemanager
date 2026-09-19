"""Consolidate source reports into property issues without losing raw evidence.

Source rows remain immutable evidence from the stay/review analyzers. Both the
queue and its workflow use these same groups, so a repair applies to every report.
Matching is deliberately conservative and never crosses properties, conflicting
locations, existing tickets, or separate resolution events.
"""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache


_STOP_WORDS = set("""
a an the in on at of to for from with and was were is are been be being has have
had guest guests reported reports report reporting noted said identified experienced
issue issues problem problems concern concerns during stay feedback still very
primary master main secondary second upstairs downstairs first third floor's
""".split())
_ALIASES = {
    "tiles": "tile", "floors": "floor", "bathrooms": "bathroom",
    "toilets": "toilet", "doors": "door", "locks": "lock", "windows": "window",
    "linens": "linen", "towels": "towel", "sheets": "sheet",
    "lights": "light", "bulbs": "bulb", "beds": "bed",
    "internet": "wifi", "wi-fi": "wifi", "wi fi": "wifi",
    "air conditioning": "ac", "air conditioner": "ac", "a/c": "ac",
    "not working": "broken", "does not work": "broken", "did not work": "broken",
    "doesn't work": "broken", "didn't work": "broken", "malfunctioning": "broken",
    "unavailable": "missing", "absent": "missing", "lacking": "missing",
    "unclean": "dirty", "filthy": "dirty", "stained": "dirty",
    "clogged": "blocked", "clog": "blocked", "clogs": "blocked",
    "leaking": "leak", "leaks": "leak", "leaked": "leak",
    "loosened": "loose", "wobbly": "loose", "detached": "loose",
    "cracks": "cracked", "crack": "cracked", "noisy": "noise",
}
_DEFECTS = set("loose cracked broken missing dirty blocked leak noise slow cooling heating".split())
_ROOMS = set("bathroom kitchen bedroom garage patio balcony pool hallway".split())


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    text = text.lower().replace("’", "'")
    for original, replacement in _ALIASES.items():
        text = re.sub(r"\b" + re.escape(original) + r"\b", replacement, text)
    return frozenset(re.findall(r"[a-z0-9]+", text)) - _STOP_WORDS


def _location(report) -> tuple[frozenset[str], frozenset[str]]:
    title = str(report.summary or "").lower()
    # Details help disambiguate a short title, but only explicit room modifiers
    # are used; unrelated room mentions in the narrative must not drive matches.
    text = title + " " + str(report.details or "").lower()
    rooms = _tokens(title) & _ROOMS
    modifiers = set()
    for match in re.finditer(
        r"\b(primary|master|main|secondary|second|third|upstairs|downstairs|guest)\s+"
        r"(?:\w+\s+)?(?:bathroom|bath|bedroom)\b|\b(?:bathroom|bedroom)\s+(\d+)\b",
        text,
    ):
        value = match.group(1) or match.group(2)
        modifiers.add({"master": "primary", "main": "primary", "second": "secondary"}.get(value, value))
    return rooms, frozenset(modifiers)


def _lifecycle(report):
    if report.workflow_status != "resolved":
        return ("active",)
    # Resolutions performed together share a timestamp. Separately fixed incidents
    # remain separate, even when a later guest describes the same symptom.
    return ("resolved", report.resolved_at or report.issue_id, report.linked_ticket_id)


def _matches(left, right) -> bool:
    # Codex compares the evidence once during analysis. Its saved complaint
    # identity handles paraphrases independently of the display category.
    left_key = getattr(left, "complaint_key", None)
    right_key = getattr(right, "complaint_key", None)
    if left_key and right_key:
        return left_key == right_key

    # Older reports retain conservative text matching until their identities
    # have been backfilled. Category labels must never exclude candidates.
    left_rooms, left_locations = _location(left)
    right_rooms, right_locations = _location(right)
    if left_rooms and right_rooms and left_rooms != right_rooms:
        return False
    if left_locations and right_locations and left_locations != right_locations:
        return False
    a, b = _tokens(str(left.summary or "")), _tokens(str(right.summary or ""))
    if len(a) < 2 or len(b) < 2:
        return False
    if a & _DEFECTS and b & _DEFECTS and a & _DEFECTS != b & _DEFECTS:
        return False
    common = a & b
    return len(common) >= 2 and len(common) / len(a | b) >= 0.72


def group_issue_reports(reports) -> list[list]:
    """Return deterministic groups; ambiguous reports keep their own card."""
    buckets = defaultdict(list)
    for report in reports:
        buckets[(report.listing_id, _lifecycle(report))].append(report)
    result = []
    for bucket in buckets.values():
        groups = []
        # Establish specific locations and existing tickets before generic reports.
        ordered = sorted(bucket, key=lambda row: (
            -bool(row.linked_ticket_id), -len(_location(row)[1]), row.issue_id,
        ))
        for report in ordered:
            candidates = []
            for group in groups:
                tickets = {row.linked_ticket_id for row in group if row.linked_ticket_id}
                if report.linked_ticket_id and tickets and report.linked_ticket_id not in tickets:
                    continue
                if all(_matches(report, member) for member in group):
                    candidates.append(group)
            if len(candidates) == 1:
                candidates[0].append(report)
            else:
                groups.append([report])
        result.extend(groups)
    return result


def representative_issue(reports):
    """Keep links stable as newer reports arrive, and honor an existing ticket."""
    return min(reports, key=lambda row: (not bool(row.linked_ticket_id), row.issue_id))


def report_count(reports) -> int:
    """Count a stay once across messages and reviews; retain unknown-source reports."""
    return len({
        ("reservation", row.reservation_id) if row.reservation_id else
        ("review", row.review_id) if row.review_id else
        ("stay", row.stay_analysis_id) if row.stay_analysis_id else
        (row.source_kind, row.source_issue_key)
        for row in reports
    })
