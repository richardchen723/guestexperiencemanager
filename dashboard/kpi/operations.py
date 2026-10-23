"""Operational goals over explicit evidence contracts; no I/O or inferred success.

Dates are property-local, half-open calendar ranges. A source being readable does
not establish completeness, semantic verification, or a physical-unit mapping.
"""
from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GOALS = {
    "revenue": (1, "Lodging revenue growth", "≥10% at every comparable physical property", "properties at target"),
    "content": (2, "Listing content & channel health", "≥2 verified meaningful refreshes / listing / week", "listings at target"),
    "reviews": (3, "Guest reviews & ratings", "Airbnb ≥60% · Vrbo ≥30% · Booking ≥40%", "channels at coverage target"),
    "response": (4, "Meaningful guest replies", "Every reply <5 min · zero overdue unanswered", "median reply time"),
    "issues": (7, "Active guest issues", "Active guest issues ≤ active physical units", "operationally active records"),
    "cleaning": (8, "Cleaning incidents", "<1 distinct incident per 10 qualifying check-ins", "incidents per 10 check-ins"),
}
RATING_TARGETS = {"EH": 4.92, "LR": 4.92, "MF": 4.92, "CR": 4.92, "SG": 4.9, "US": 4.8}
COVERAGE_TARGETS = {"airbnb": 60, "vrbo": 30, "booking": 40}
ACTIVE_ISSUES = {"need_attention", "scheduled", "in_progress", "stuck"}
METHODS = {
    "revenue": "Discounted lodging charges less reconciled lodging refunds, allocated to occupied nights in each local window, compared with the same dates one year earlier. Taxes, cleaning and pass-through charges are excluded. A verified stable physical-property cohort and bundle allocation are required; new properties are separate. No total-price substitution.",
    "content": "Count unique verified meaningful content publications per listing in Monday–Sunday local calendar weeks. Pricing changes do not prove content edits. Clipped edge weeks are shown separately; passing requires at least two edits in every full week and verified channel/quality health.",
    "reviews": "Coverage counts distinct eligible, mature checkouts in the window with a published guest review linked by reservation ID. Channel eligibility and maturation policies must be verified. Ratings use guest reviews published in the local window; native scales and portfolio mapping must be verified. Native 10-point scores are displayed separately, never blindly converted or pooled.",
    "response": "Time from the original timestamp of the first inbound guest message in a sequence to a sent, meaningful answer. Consecutive guest messages share one clock. Generic acknowledgements and unrelated scheduled messages do not close it; a meaningful AI answer can. Timing proxies are separate from verified semantic results. Overdue unanswered includes carry-ins open at the as-of timestamp.",
    "issues": "Count all operationally active guest issues across the portfolio at the as-of timestamp, including unresolved carry-ins. Narrative resolution does not override operational status. Daily history requires complete initial state plus ordered guest-issue status events; ticket changes alone cannot reconstruct it. Compare only to verified active physical units, never listing count.",
    "cleaning": "Count distinct validated cleaning incidents linked to qualifying check-ins in the local rolling window, divided by distinct qualifying check-ins, multiplied by ten. A documented category mapping and reviewed duplicate reconciliation are required. Candidate records and possible duplicate groups are diagnostics, never the validated numerator.",
}


def as_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def day(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def months_before(value, months=3):
    y, m = divmod(value.year * 12 + value.month - 1 - months, 12)
    return value.replace(year=y, month=m + 1, day=min(value.day, calendar.monthrange(y, m + 1)[1]))


def local_window(as_of, timezone_name):
    """Missing timezone is an evidence gap, never an implicit UTC property."""
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return None
    end = as_utc(as_of).astimezone(zone).date()
    start = months_before(end)
    return {"start": start, "end": end, "days": (end - start).days, "timezone": timezone_name}


def channel(value):
    v = "".join(c for c in str(value or "").lower() if c.isalnum())
    return {"airbnbofficial": "airbnb", "homeaway": "vrbo", "bookingcom": "booking"}.get(v, v or "unknown")


def qualifying(row):
    return str(row.get("status", "")).lower() in {"new", "modified", "confirmed", "booked", "accepted"} and "ical" not in channel(row.get("channel"))


def ratio(n, d, multiplier=100):
    return round(n / d * multiplier, 2) if d else None


def metric(key):
    goal, title, target, label = GOALS[key]
    return {"key": key, "goal": goal, "title": title, "target": target, "label": label,
            "value": None, "display": "Not verified", "data_status": "unavailable", "goal_status": "unknown",
            "summary": "Evidence is incomplete.", "numerator": None, "denominator": None,
            "numerator_label": "Verified qualifying observations", "denominator_label": "Verified eligible cohort",
            "breakdown": [], "trend": [], "exceptions": [], "diagnostics": [], "method": METHODS[key], "sources": []}


def exception(result, action, source_id=None, listing_id=None):
    result["exceptions"].append({"action": action, "source_id": str(source_id) if source_id is not None else None, "listing_id": listing_id})


def finish(result, verified, passed=None):
    result["data_status"] = "verified" if verified else "partial" if result["value"] is not None else "unverified"
    result["goal_status"] = ("met" if passed else "off_target") if verified and passed is not None else "unknown"
    return result


def revenue(data, windows):
    r = metric("revenue")
    totals, nights, seen, weekly = defaultdict(float), defaultdict(int), {}, defaultdict(float)
    invalid = set()
    listings = {x["id"]: x for x in data.get("listings", [])}
    for row in data.get("reservations", []):
        lid = row["listing_id"]
        w = windows.get(lid)
        if not w or not qualifying(row):
            continue
        start, end = day(row["arrival"]), day(row["departure"])
        if end <= start:
            exception(r, "Correct invalid stay dates.", row["id"], lid)
            invalid.add(lid)
            continue
        if not (start < w["end"] and end > months_before(w["start"], 12)):
            continue
        if not all(row.get(f) for f in ("financials_verified", "refunds_reconciled", "allocation_verified")) or row.get("currency") != "USD":
            invalid.add(lid)
            exception(r, "Reconcile discounted lodging, refunds, USD currency and physical-unit allocation.", row["id"], lid)
            continue
        allocations = row.get("nightly_lodging", [])
        if not allocations:
            invalid.add(lid)
            exception(r, "Supply reconciled nightly lodging allocations, including boundary nights.", row["id"], lid)
            continue
        expected_dates = {start + timedelta(days=i) for i in range((end - start).days)}
        if {day(x["date"]) for x in allocations} != expected_dates or not all(math.isfinite(float(x["amount"])) for x in allocations):
            invalid.add(lid)
            exception(r, "Reconcile missing nightly allocations or invalid monetary values across the entire stay.", row["id"], lid)
            continue
        for allocation in allocations:
            night = day(allocation["date"])
            if not start <= night < end:
                invalid.add(lid)
                exception(r, "Reconcile lodging allocation outside the stay.", row["id"], lid)
                continue
            period = "current" if w["start"] <= night < w["end"] else "prior" if months_before(w["start"], 12) <= night < months_before(w["end"], 12) else None
            if not period:
                continue
            unit = allocation.get("unit_id")
            if unit not in (listings.get(lid, {}).get("physical_unit_ids") or []):
                invalid.add(lid)
                exception(r, "Verify nightly allocation against the physical-unit crosswalk.", row["id"], lid)
                continue
            identity = (row.get("booking_key", row["id"]), unit, night)
            if identity in seen:
                if seen[identity] != float(allocation["amount"]):
                    invalid.add(lid)
                    exception(r, "Conflicting bundle allocation; reconcile source booking identity.", row["id"], lid)
                continue
            seen[identity] = float(allocation["amount"])
            totals[(unit, period)] += float(allocation["amount"])
            nights[(unit, period)] += 1
            if period == "current":
                weekly[(unit, night - timedelta(days=night.weekday()))] += float(allocation["amount"])
    comparable = {}
    for listing in listings.values():
        if listing.get("cohort_verified") and listing.get("comparable"):
            for unit in listing.get("physical_unit_ids") or []:
                comparable.setdefault(unit, []).append(listing["id"])
    met = 0
    for unit, lids in comparable.items():
        prior, current = totals[(unit, "prior")], totals[(unit, "current")]
        valid = not invalid.intersection(lids) and nights[(unit, "prior")] > 0 and nights[(unit, "current")] > 0
        growth = ratio(current - prior, prior) if valid and prior > 0 else None
        met += growth is not None and growth >= 10
        r["breakdown"].append({"group": f"Physical property {unit}", "current_usd": round(current, 2) if valid else None, "prior_usd": round(prior, 2) if valid else None, "growth_pct": growth, "target_pct": 10})
    if comparable and any(x["growth_pct"] is not None for x in r["breakdown"]):
        r.update(value=ratio(met, len(comparable)), display=f"{met} / {len(comparable)}", numerator=met, denominator=len(comparable))
    r.update(numerator_label="Comparable properties with growth ≥10%", denominator_label="All verified comparable physical properties")
    if not comparable:
        exception(r, "Verify the stable physical-property cohort, new properties and package crosswalk before comparing revenue.")
    r["trend"] = [{"date": d.isoformat(), "group": f"Physical property {u}", "value": round(v, 2), "unit": "USD lodging after reconciled refunds"} for (u, d), v in sorted(weekly.items(), key=lambda x: (str(x[0][0]), x[0][1]))]
    observed = defaultdict(int)
    for x in data.get("reservations", []):
        w = windows.get(x["listing_id"])
        if w and qualifying(x) and day(x["arrival"]) < w["end"] and day(x["departure"]) > w["start"]:
            observed[(x["listing_id"], channel(x.get("channel")))] += 1
    r["breakdown"].extend({"group": listings.get(lid, {}).get("name", str(lid)), "channel": ch, "observed_overlapping_bookings": n, "certified_lodging_usd": None} for (lid, ch), n in sorted(observed.items()) if lid in invalid or not comparable)
    r["diagnostics"] = [{"label": "Observed current-window overlapping bookings (not revenue)", "value": sum(observed.values()) if len(windows) == len(listings) and listings else None}, {"label": "New / unverified cohort listings", "value": sum(not x.get("comparable") or not x.get("cohort_verified") for x in listings.values()) if listings else None}]
    r["summary"] = "Every comparable property must grow; portfolio growth cannot hide a declining property."
    verified = bool(comparable) and not r["exceptions"] and all(x.get("growth_pct") is not None for x in r["breakdown"]) and all(x.get("cohort_verified") for x in listings.values())
    return finish(r, verified, met == len(comparable))


def content(data, windows):
    r = metric("content")
    events = {x["id"]: x for x in data.get("content_events", [])}
    passed = 0
    for listing in data.get("listings", []):
        w = windows.get(listing["id"])
        if not w:
            continue
        counts = defaultdict(int)
        for e in events.values():
            if e["listing_id"] != listing["id"] or not e.get("verified") or not e.get("meaningful") or e.get("kind") == "pricing":
                continue
            d = as_utc(e["published_at"]).astimezone(ZoneInfo(w["timezone"])).date()
            if w["start"] <= d < w["end"]:
                counts[d - timedelta(days=d.weekday())] += 1
        full = []
        cursor = w["start"] - timedelta(days=w["start"].weekday())
        while cursor < w["end"]:
            complete = cursor >= w["start"] and cursor + timedelta(days=7) <= w["end"]
            count = counts[cursor] if data.get("content_ledger_complete") else None
            r["trend"].append({"date": cursor.isoformat(), "group": listing["name"], "value": count, "target": 2, "partial_week": not complete})
            if complete:
                full.append(count)
            cursor += timedelta(days=7)
        ok = bool(full) and all(x is not None and x >= 2 for x in full) and listing.get("channel_health_verified") is True
        passed += ok
        r["breakdown"].append({"group": listing["name"], "full_weeks_meeting_target": sum(x is not None and x >= 2 for x in full) if data.get("content_ledger_complete") else None, "full_weeks": len(full), "channel_quality": "Verified healthy" if listing.get("channel_health_verified") else "Unverified"})
    if data.get("content_ledger_complete") and r["breakdown"]:
        r.update(value=ratio(passed, len(r["breakdown"])), display=f"{passed} / {len(r['breakdown'])}", numerator=passed, denominator=len(r["breakdown"]))
    else:
        exception(r, "Establish a verified content-publication ledger with source edit IDs, meaningful change evidence and channel/quality checks. Pricing logs are insufficient.")
    r["summary"] = "Two meaningful edits each week, with channel and quality checks."
    return finish(r, bool(r["breakdown"]) and data.get("content_ledger_complete", False) and all(x.get("channel_health_verified") for x in data.get("listings", [])), passed == len(r["breakdown"]))


def reviews(data, windows):
    r = metric("reviews")
    published = {x["id"]: x for x in data.get("reviews", []) if x.get("origin", "").lower() == "guest" and x.get("status", "").lower() == "published"}
    policies = data.get("review_policies", {})
    numerator, denominator, weeks = defaultdict(set), defaultdict(set), defaultdict(lambda: [set(), set()])
    for row in data.get("reservations", []):
        w, ch = windows.get(row["listing_id"]), channel(row.get("channel"))
        policy = policies.get(ch, {})
        if not w or ch not in COVERAGE_TARGETS or not policy.get("verified"):
            continue
        departure = day(row["departure"])
        if w["start"] <= departure < w["end"] and departure <= w["end"] - timedelta(days=policy["maturation_days"]):
            matching_review = any(x.get("reservation_id") == row["id"] and x.get("listing_id") == row["listing_id"] and channel(x.get("channel")) == ch and x.get("date") and day(x["date"]) < w["end"] for x in published.values())
            eligible = qualifying(row) or matching_review or row.get("review_eligibility_verified") and row.get("review_eligible") is True
            if not eligible:
                if "cancel" in str(row.get("status", "")).lower() and not row.get("review_eligibility_verified") and (not row.get("cancellation_date") or day(row["cancellation_date"]) >= day(row["arrival"])):
                    exception(r, "Verify review eligibility for this unreviewed cancellation after arrival or with unknown cancellation timing; it is not silently treated as an ineligible stay.", row["id"], row["listing_id"])
                continue
            denominator[ch].add(row["id"])
            bucket = (ch, departure - timedelta(days=departure.weekday()))
            weeks[bucket][1].add(row["id"])
            if matching_review:
                numerator[ch].add(row["id"])
                weeks[bucket][0].add(row["id"])
    met = 0
    for ch, target in COVERAGE_TARGETS.items():
        count = len(denominator[ch]) if policies.get(ch, {}).get("verified") else None
        n = len(numerator[ch]) if count is not None else None
        coverage = ratio(n, count) if count else None
        met += coverage is not None and coverage >= target
        r["breakdown"].append({"group": ch.title(), "reviewed_checkouts": n, "eligible_mature_checkouts": count, "coverage_pct": coverage, "target_pct": target})
    for x in published.values():
        w = windows.get(x["listing_id"])
        if w and x.get("date") and w["start"] <= day(x["date"]) < w["end"] and not x.get("reservation_id"):
            exception(r, "Link published review to its verified source reservation ID.", x["id"], x["listing_id"])
    ratings, listing_map, observed_reviews = defaultdict(list), {x["id"]: x for x in data.get("listings", []) + data.get("historical_listings", [])}, defaultdict(int)
    for x in published.values():
        listing, w = listing_map.get(x["listing_id"], {}), windows.get(x["listing_id"])
        if not w or not x.get("date") or not w["start"] <= day(x["date"]) < w["end"] or x.get("rating") is None:
            continue
        # Do not infer rating scale from score magnitude or channel name.
        portfolio = listing.get("portfolio_code") if listing.get("portfolio_verified") else "Unverified mapping"
        scale = x.get("native_scale") if x.get("scale_verified") else "Unverified scale"
        ratings[(portfolio, channel(x.get("channel")), scale)].append(float(x["rating"]))
        observed_reviews[(x["listing_id"], channel(x.get("channel")))] += 1
    for (p, ch, scale), values in ratings.items():
        r["diagnostics"].append({"label": f"{p} · {ch} · native scale {scale}", "value": f"{sum(values)/len(values):.3f} ({len(values)} reviews)"})
    rating_checks, rating_complete = [], True
    for p, target in RATING_TARGETS.items():
        values = [v for (code, _, scale), items in ratings.items() if code == p and scale == 5 for v in items]
        incompatible = any(code == p and scale != 5 for code, _, scale in ratings)
        avg = sum(values) / len(values) if values and not incompatible else None
        rating_complete = rating_complete and avg is not None
        rating_checks.append(avg is not None and avg > target)
        r["breakdown"].append({"group": f"{p} guest rating", "native_5_average": round(avg, 3) if avg is not None else None, "target_strictly_above": target, "rated_reviews": len(values) if not incompatible else None})
    r["breakdown"].extend({"group": listing_map[lid]["name"], "channel": ch, "observed_published_rated_guest_reviews": n, "coverage_pct": None} for (lid, ch), n in sorted(observed_reviews.items()))
    eligible = sum(len(x) for x in denominator.values())
    if eligible:
        r.update(value=ratio(met, 3), display=f"{met} / 3", numerator=sum(len(x) for x in numerator.values()), denominator=eligible)
    r["trend"] = [{"date": d.isoformat(), "group": ch, "value": ratio(len(n), len(de)), "reviewed": len(n), "eligible": len(de), "target": COVERAGE_TARGETS[ch]} for (ch, d), (n, de) in sorted(weeks.items())]
    exception(r, "Verify channel eligibility/maturation, source-native rating scales and EH/LR/MF/CR/SG/US portfolio mapping before evaluating all rating goals.") if not data.get("review_contract_verified") else None
    r.update(numerator_label="Distinct eligible mature checkouts with a published guest review", denominator_label="Distinct eligible mature checkouts", summary="Channel coverage and portfolio ratings must both meet their individual targets.")
    r["diagnostics"].insert(0, {"label": "Observed published rated guest reviews in synchronized date window (publication time unverified)", "value": sum(observed_reviews.values()) if len(windows) == len(listing_map) and listing_map else None})
    if any(x["id"] not in windows for x in data.get("historical_listings", [])):
        exception(r, "Verify historical property timezones; archived properties cannot be silently removed from the checkout cohort.")
    return finish(r, data.get("review_contract_verified", False) and rating_complete and not r["exceptions"] and all(x["coverage_pct"] is not None for x in r["breakdown"][:3]), met == 3 and all(rating_checks))


def response(data, windows, as_of):
    r = metric("response")
    grouped = defaultdict(list)
    for x in {m["id"]: m for m in data.get("messages", [])}.values():
        grouped[x["conversation_id"]].append(x)
    durations, proxy, overdue, breakdown, weekly = [], [], [], defaultdict(list), defaultdict(list)
    unverified, proxy_seen, dispatch_uncertain = 0, set(), set()
    for messages in grouped.values():
        pending = None
        for x in sorted(messages, key=lambda m: (as_utc(m["at"]), str(m["id"]))):
            at = as_utc(x["at"])
            if at > as_of:
                continue
            if x.get("incoming"):
                if x.get("needs_reply_verified") and x.get("needs_reply") is False:
                    continue
                pending = pending or x
                continue
            if not pending:
                continue
            if x.get("sent") is not True and x.get("answer_kind") == "meaningful" and x.get("semantics_verified"):
                dispatch_uncertain.add(x["id"])
                exception(r, "Verify dispatch of this written meaningful answer. No dispatch match does not establish a missed request.", x["id"], x["listing_id"])
            if not x.get("sent", True):
                continue
            # Explicitly known non-answers never close an inbound sequence.
            if x.get("answer_kind") in {"acknowledgement", "unrelated_scheduled"} or x.get("automated") and x.get("answer_kind") != "meaningful":
                continue
            w = windows.get(pending["listing_id"])
            start = as_utc(pending["at"])
            d = start.astimezone(ZoneInfo(w["timezone"])).date() if w else None
            verified = x.get("sent") is True and x.get("answer_kind") == "meaningful" and x.get("semantics_verified") and x.get("original_timestamp_verified") and pending.get("original_timestamp_verified") and pending.get("needs_reply_verified") and pending.get("needs_reply") is True
            elapsed = (at - start).total_seconds() / 60
            if w and w["start"] <= d < w["end"]:
                if verified:
                    durations.append(elapsed)
                    breakdown[pending["listing_id"]].append(elapsed)
                    weekly[d - timedelta(days=d.weekday())].append(elapsed)
                elif pending["id"] not in proxy_seen:
                    proxy.append(elapsed)
                    unverified += 1
                    proxy_seen.add(pending["id"])
            # An unverified outgoing message is only a timing proxy, not proof
            # the sequence was answered. Keep it pending for meaningful evidence.
            if verified:
                pending = None
        if pending and (as_of - as_utc(pending["at"])).total_seconds() >= 300:
            overdue.append(pending)
    for x in overdue:
        exception(r, "Review this possibly overdue sequence against the original conversation, including carry-ins; a missing verified answer is not proof of a missed request.", x["id"], x["listing_id"])
    if durations:
        value = round(median(durations), 2)
        r.update(value=value, display=f"{value:g} min", numerator=sum(x < 5 for x in durations), denominator=len(durations))
    r["breakdown"] = [{"group": str(lid), "median_minutes": round(median(v), 2), "under_5": sum(x < 5 for x in v), "answered": len(v)} for lid, v in breakdown.items()]
    r["trend"] = [{"date": d.isoformat(), "value": round(median(v), 2), "sample": len(v), "target": 5} for d, v in sorted(weekly.items())]
    r["diagnostics"] = [{"label": "Timing proxy median (unverified answers)", "value": round(median(proxy), 2) if proxy else None}, {"label": "Unverified outbound timing observations", "value": unverified}, {"label": "Overdue / possible unanswered sequences, including carry-ins", "value": len(overdue) if data.get("messages_complete") else None}]
    r.update(numerator_label="Meaningful verified sent replies strictly under five minutes", denominator_label="Verified answered guest sequences", summary="A fast acknowledgement is not proof of an answer. Every reply must beat five minutes.")
    if not data.get("messages_complete") or unverified:
        exception(r, "Verify complete original message history, carry-ins, timestamps and meaningful answer labels. Timing proxy is not the goal actual.")
    return finish(r, data.get("messages_complete", False) and not unverified and not dispatch_uncertain and bool(durations), all(x < 5 for x in durations) and not overdue)


def issues(data, windows, as_of):
    r = metric("issues")
    active, counts = [], defaultdict(int)
    all_issues = {x["id"]: x for x in data.get("issues", [])}
    for x in all_issues.values():
        status = x.get("operational_status")
        if status != "resolved":
            active.append(x)
            counts[x["listing_id"]] += 1
            if x.get("resolution_state") == "resolved" or x.get("workflow_status") == "resolved":
                exception(r, "Reconcile narrative/workflow resolution with operationally active status; retain as active until reviewed.", x["id"], x["listing_id"])
            if status not in ACTIVE_ISSUES:
                exception(r, "Verify missing operational status; retained as possibly active.", x["id"], x["listing_id"])
    units = {u for x in data.get("listings", []) for u in x.get("physical_unit_ids") or [] if x.get("units_verified")}
    verified_units = bool(units) and all(x.get("units_verified") for x in data["listings"])
    if data.get("issues_readable"):
        r.update(value=len(active), display=str(len(active)), numerator=len(active), denominator=len(units) if verified_units else None)
    r["breakdown"] = [{"group": str(lid), "active_guest_issue_records": count} for lid, count in sorted(counts.items())]
    if not verified_units:
        exception(r, "Reconcile the active physical-unit register and package listings. Catalog count is not the denominator.")
    # Full initial state and status events are required for each property's local
    # daily closing count. Current status is never projected backwards.
    if data.get("issue_history_complete"):
        events = sorted(data.get("issue_events", []), key=lambda e: as_utc(e["at"]))
        for lid, w in windows.items():
            d = w["start"]
            while d < w["end"]:
                closing = datetime.combine(d + timedelta(days=1), time.min, ZoneInfo(w["timezone"])).astimezone(timezone.utc)
                state = dict(data.get("issue_initial_states", {}))
                for e in events:
                    if as_utc(e["at"]) < closing:
                        state[e["issue_id"]] = e["status"]
                count = sum(s in ACTIVE_ISSUES for i, s in state.items() if all_issues.get(i, {}).get("listing_id") == lid)
                r["trend"].append({"date": d.isoformat(), "group": str(lid), "value": count})
                d += timedelta(days=1)
    else:
        exception(r, "Collect guest-issue initial state and timestamped status events. Historical backlog cannot be reconstructed from today's status or ticket transitions.")
    r.update(numerator_label="Operationally active guest issue records including carry-ins", denominator_label="Verified active physical units, portfolio-wide", summary="Current active load includes old unresolved issues. Candidate duplicates remain separate until reviewed.")
    r["diagnostics"] = [{"label": "Active physical-unit denominator", "value": len(units) if verified_units else None}, {"label": "Possible duplicate groups (review required)", "value": data.get("possible_duplicate_groups")}, {"label": "Historical daily backlog", "value": "Verified event history" if data.get("issue_history_complete") else "Unavailable"}]
    verified = verified_units and data.get("issues_complete", False) and data.get("issue_history_complete", False) and not r["exceptions"]
    return finish(r, verified, len(active) <= len(units))


def cleaning(data, windows):
    r = metric("cleaning")
    checkins = {x["id"]: x for x in data.get("reservations", []) if qualifying(x) and x.get("checkin_verified") and windows.get(x["listing_id"]) and windows[x["listing_id"]]["start"] <= day(x["arrival"]) < windows[x["listing_id"]]["end"]}
    incidents = {}
    for x in data.get("issues", []):
        if x.get("cleaning_candidate"):
            exception(r, "Validate cleaning category and incident identity; candidate is excluded from the numerator.", x["id"], x["listing_id"])
        if x.get("category_verified") and x.get("category") == "cleaning" and x.get("incident_verified") and x.get("reservation_id") in checkins:
            incidents[x["incident_id"]] = x
    if data.get("cleaning_contract_verified") and checkins:
        value = ratio(len(incidents), len(checkins), 10)
        r.update(value=value, display=f"{value:g}", numerator=len(incidents), denominator=len(checkins))
    for lid in windows:
        n = sum(x["listing_id"] == lid for x in incidents.values())
        d = sum(x["listing_id"] == lid for x in checkins.values())
        r["breakdown"].append({"group": str(lid), "distinct_verified_incidents": n if data.get("cleaning_contract_verified") else None, "qualifying_checkins": d if data.get("checkins_complete") else None, "per_10": ratio(n, d, 10) if data.get("cleaning_contract_verified") else None})
    if data.get("cleaning_contract_verified") and data.get("checkins_complete"):
        weeks = defaultdict(lambda: [set(), set()])
        for x in checkins.values():
            arrival = day(x["arrival"])
            key = (x["listing_id"], arrival - timedelta(days=arrival.weekday()))
            weeks[key][1].add(x["id"])
        for identity, x in incidents.items():
            arrival = day(checkins[x["reservation_id"]]["arrival"])
            weeks[(x["listing_id"], arrival - timedelta(days=arrival.weekday()))][0].add(identity)
        r["trend"] = [{"date": d.isoformat(), "group": str(lid), "value": ratio(len(n), len(de), 10), "incidents": len(n), "checkins": len(de), "target": 1} for (lid, d), (n, de) in sorted(weeks.items())]
    if not data.get("cleaning_contract_verified"):
        exception(r, "Approve documented cleaning category mapping, check-in eligibility and reviewed incident deduplication before calculating the rate.")
    r.update(numerator_label="Distinct validated cleaning incidents", denominator_label="Distinct qualifying verified check-ins", summary="Candidate messages are not distinct incidents; zero eligible check-ins means no rate.")
    r["diagnostics"] = [{"label": "Observed cleaning-category candidates (not validated incidents)", "value": sum(x.get("cleaning_candidate", False) for x in data.get("issues", [])) if data.get("issues_readable") else None}]
    return finish(r, data.get("cleaning_contract_verified", False) and data.get("checkins_complete", False) and bool(checkins) and not r["exceptions"], r["value"] is not None and r["value"] < 1)


SOURCE_DEPENDENCIES = {"revenue": ("catalog", "reservations"), "content": ("catalog", "content"), "reviews": ("catalog", "reservations", "reviews"), "response": ("catalog", "messages"), "issues": ("catalog", "issues"), "cleaning": ("catalog", "reservations", "issues")}


def build_operations(data, *, as_of=None):
    as_of = as_utc(as_of or datetime.now(timezone.utc))
    windows = {x["id"]: local_window(as_of, x.get("timezone")) for x in data.get("listings", [])}
    windows = {k: v for k, v in windows.items() if v}
    review_windows = {**windows, **{x["id"]: w for x in data.get("historical_listings", []) if (w := local_window(as_of, x.get("timezone")))}}
    metrics = [revenue(data, windows), content(data, windows), reviews(data, review_windows), response(data, windows, as_of), issues(data, windows, as_of), cleaning(data, windows)]
    for r in metrics:
        for source in SOURCE_DEPENDENCIES[r["key"]]:
            s = dict(data.get("sources", {}).get(source, {"status": "missing", "checked_at": None}))
            s["name"] = source
            stamp = as_utc(s.get("checked_at"))
            s["stale"] = bool(stamp and (as_of - stamp).total_seconds() > 86400)
            if stamp and stamp > as_of:
                s["status"] = "unverified"
                s["reason"] = "Source check timestamp is later than as-of; verify the source clock."
            r["sources"].append(s)
        if len(windows) != len(data.get("listings", [])):
            exception(r, "Verify missing/invalid property timezones before evaluating the complete portfolio.")
            r["data_status"], r["goal_status"] = "partial" if r["value"] is not None else "unverified", "unknown"
        if not data.get("listings"):
            r["data_status"], r["goal_status"] = "unavailable", "unknown"
            exception(r, "No readable property catalog is available; restore source coverage.")
        if any(s.get("status") != "complete" or not s.get("checked_at") for s in r["sources"]):
            r["data_status"] = "partial" if r["value"] is not None else "unavailable"
            r["goal_status"] = "unknown"
        if any(s["stale"] for s in r["sources"]):
            r["data_status"], r["goal_status"] = "stale", "unknown"
        # Bounded diagnostic payloads with honest total counts; no PII fields.
        r["exception_count"] = len(r["exceptions"])
        r["exceptions"] = r["exceptions"][:100]
        r["observation"] = None
        if r["key"] == "revenue" and r["diagnostics"][0]["value"] is not None and data.get("reservations"):
            r["observation"] = f"{r['diagnostics'][0]['value']} overlapping bookings observed · lodging unverified"
        elif r["key"] == "content" and data.get("listings"):
            r["observation"] = f"{len(data['listings'])} listing snapshots · edit history unverified"
        elif r["key"] == "reviews" and r["diagnostics"][0]["value"] is not None and data.get("reviews"):
            r["observation"] = f"{r['diagnostics'][0]['value']} published rated records observed · coverage unverified"
        elif r["key"] == "response" and r["diagnostics"][0]["value"] is not None:
            r["observation"] = f"Timing proxy {r['diagnostics'][0]['value']:g} min · meaningful answers unverified"
        elif r["key"] == "cleaning" and data.get("issues_readable"):
            r["observation"] = f"{r['diagnostics'][0]['value']} category candidates · distinct incidents unverified"
    dates = sorted({(w["start"].isoformat(), w["end"].isoformat(), w["days"]) for w in windows.values()})
    names = {x["id"]: x["name"] for x in data.get("listings", []) + data.get("historical_listings", [])}
    for r in metrics:
        for row in r["breakdown"] + r["trend"]:
            if str(row.get("group", "")).isdigit():
                row["group"] = names.get(int(row["group"]), f"Unmapped listing {row['group']}")
    property_windows = [{"listing_id": lid, "property": names.get(lid), "scope": "Current catalog" if lid in windows else "Historical review cohort", **{k: v.isoformat() if isinstance(v, date) else v for k, v in w.items()}} for lid, w in review_windows.items()]
    return {"owner": "Usman", "deadline": "2026-12-31", "as_of": as_of.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(), "window": {"kind": "rolling_three_calendar_months", "ranges": [{"start_inclusive": a, "end_exclusive": b, "days": d} for a, b, d in dates], "semantics": "Property-local calendar dates; current local day excluded. Active issue load and overdue unanswered are snapshots at as-of."}, "property_windows": property_windows, "metrics": metrics, "excluded_goals": [5, 6, 9]}
