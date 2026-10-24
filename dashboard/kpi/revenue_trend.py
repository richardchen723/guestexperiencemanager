"""Calendar-anniversary comparisons over one fixed, evidence-backed cohort."""
from collections import defaultdict
from datetime import timedelta

from dashboard.kpi.operations import months_before

SCHEMA = "revenue-weekly-yoy.v1"
ALIGNMENT = (
    "Current Monday weeks, clipped to each property-local window; compare the "
    "same month/day span one calendar year earlier, with month-end clamping, "
    "not ISO week numbers. YoY is unavailable for unequal day counts or a "
    "missing/zero prior baseline."
)


def weekly_comparison(daily, windows, cohort):
    """Daily keys are (cohort member, current/prior, local date).

    An absent daily key is not a proven zero. A recorded zero stays numeric.
    Both columns include exactly the supplied cohort; never select a different
    roster for the prior column or a weekly subset of the common-window cohort.
    Calendar-anniversary bucket boundaries partition the same prior-year window
    as the goal, including leap-year clamps. Unequal lengths retain both observed
    dollar totals but deliberately have no percentage comparison.
    """
    buckets = defaultdict(list)
    for member in sorted(cohort):
        window = windows[member]
        monday = window["start"] - timedelta(days=window["start"].weekday())
        while monday < window["end"]:
            start, end = max(monday, window["start"]), min(monday + timedelta(days=7), window["end"])
            buckets[monday].append((member, start, end, months_before(start, 12), months_before(end, 12)))
            monday += timedelta(days=7)
    rows = []
    for monday, spans in sorted(buckets.items()):
        amounts, observed = {"current": 0.0, "prior": 0.0}, {"current": False, "prior": False}
        day_counts_match = True
        for member, start, end, prior_start, prior_end in spans:
            day_counts_match &= (end - start).days == (prior_end - prior_start).days
            for period, a, b in (("current", start, end), ("prior", prior_start, prior_end)):
                while a < b:
                    key = (member, period, a)
                    if key in daily:
                        amounts[period] += daily[key]
                        observed[period] = True
                    a += timedelta(days=1)
        current = amounts["current"] if observed["current"] else None
        prior = amounts["prior"] if observed["prior"] else None
        reason = "Different calendar day counts" if not day_counts_match else "Current observations unavailable" if current is None else "Prior observations unavailable" if prior is None else "Prior booking value is zero" if prior == 0 else None
        change = (current / prior - 1) * 100 if reason is None else None
        current_days = sorted({(b - a).days for _, a, b, _, _ in spans})
        prior_days = sorted({(b - a).days for _, _, _, a, b in spans})
        rows.append({
            "date": monday.isoformat(), "value": round(current, 2) if current is not None else None,
            "booking_value_usd": round(current, 2) if current is not None else None,
            "prior_booking_value_usd": round(prior, 2) if prior is not None else None,
            "yoy_change_pct": round(change, 2) if change is not None else None,
            "yoy_unavailable_reason": reason,
            "partial_week": any(n < 7 for n in current_days),
            "current_days_per_listing": current_days, "prior_days_per_listing": prior_days,
            "current_spans": sorted({f"{a.isoformat()} → {b.isoformat()} (exclusive)" for _, a, b, _, _ in spans}),
            "prior_spans": sorted({f"{a.isoformat()} → {b.isoformat()} (exclusive)" for _, _, _, a, b in spans}),
            "cohort_size": len(cohort), "contributing_listings": len(spans),
        })
    return rows
