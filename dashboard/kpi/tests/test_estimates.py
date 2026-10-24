"""Independent arithmetic and safety checks for the explicitly labeled proxies."""
from datetime import date, datetime, timedelta, timezone

import pytest

from dashboard.kpi.estimates import estimate_cleaning, estimate_issues, estimate_response, estimate_revenue, estimate_reviews, timezone_for_listing
from dashboard.kpi.operations import build_operations, local_window

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
W = {1: local_window(NOW, "America/New_York")}


def booking(id=1, year=2026, **kw):
    return {"id": id, "listing_id": 1, "arrival": date(year, 8, 1), "departure": date(year, 8, 3), "status": "booked", "channel": "airbnb", "currency": "USD", "total_price": 250, "tax_amount": 20, "cleaning_fee": 30, "security_deposit_fee": None, **kw}


def data(): return {"listings": [{"id": 1, "name": "Fixture", "timezone": "America/New_York", "portfolio": "Enchanted Havens"}], "issues_readable": True}


def test_jurisdiction_fallback_is_explicit_and_no_silent_utc_or_split_state_guess():
    assert timezone_for_listing({"country": "United States of America", "state": "TN", "city": "Pigeon Forge"})[0] == "America/New_York"
    zone, proof = timezone_for_listing({"country": "US", "state": "CA", "city": "San Gabriel"})
    assert zone == "America/Los_Angeles" and "jurisdiction" in proof
    assert timezone_for_listing({"country": "US", "state": "TN", "city": "unknown"})[0] is None
    assert timezone_for_listing({"state": "CA"})[0] is None
    assert timezone_for_listing({"timezone": "America/Chicago"})[0] == "America/Chicago"


def test_revenue_independent_boundary_proration_and_unreconciled_refund_label():
    d = data()
    # 250-20-30 = 200 for two nights; only July23 contributes -> 100.
    d["reservations"] = [booking(arrival=date(2026, 7, 22), departure=date(2026, 7, 24)), booking(2, 2025, total_price=150)]
    r = estimate_revenue(d, W)
    assert r["breakdown"][0]["current_proxy_usd"] == 100
    assert r["breakdown"][0]["prior_proxy_usd"] == 100
    assert r["breakdown"][0]["estimated_growth_pct"] == 0
    assert r["data_status"] == "estimated" and r["goal_status"] == "unknown"
    assert "refund" in r["method"] and "unknown" in r["method"].lower() or "unreconciled" in r["method"]


def test_revenue_does_not_invent_missing_cleaning_or_allow_portfolio_average_to_pass():
    d = data(); d["reservations"] = [booking(cleaning_fee=None), booking(2, 2025)]
    assert estimate_revenue(d, W)["value"] is None
    d["listings"].append({**d["listings"][0], "id": 2, "name": "B"})
    d["reservations"] = [booking(total_price=1050), booking(2, 2025), booking(3, listing_id=2, total_price=230), booking(4, 2025, listing_id=2)]
    r = estimate_revenue(d, {**W, 2: W[1]})
    assert r["display"] == "1 / 2"
    assert min(x["estimated_growth_pct"] for x in r["breakdown"]) == -10


def test_shared_booking_across_listings_is_excluded_not_double_counted():
    d = data(); d["listings"].append({**d["listings"][0], "id": 2, "name": "Bundle"})
    d["reservations"] = [booking(channel_booking_id="same"), booking(2, listing_id=2, channel_booking_id="same"), booking(3, 2025)]
    r = estimate_revenue(d, {**W, 2: W[1]})
    assert r["value"] is None and next(x['value'] for x in r['diagnostics'] if x['label']=='Shared-booking bundle exclusions') == 2


def test_review_checkout_boundary_cancel_sensitivity_and_rating_cohort_are_separate():
    d = data(); d["reservations"] = [booking(arrival=date(2026, 7, 22), departure=date(2026, 7, 23)), booking(2, status="cancelled", cancellation_date=date(2026, 8, 2))]
    d["reviews"] = [{"id": 1, "listing_id": 1, "reservation_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 8, 1), "channel": "airbnb", "rating": 10}, {"id": 2, "listing_id": 1, "reservation_id": None, "origin": "Guest", "status": "published", "date": date(2026, 8, 2), "channel": "airbnb", "rating": 8}, {"id": 3, "listing_id": 1, "origin": None, "status": None, "rating": None}]
    r = estimate_reviews(d, W)
    assert r["numerator"] == r["denominator"] == 1
    assert "50–100%" in r["breakdown"][0]["sensitivity"]
    rating = next(x for x in r["breakdown"] if x["group"] == "EH")
    assert rating["value"] == 9 and rating["native_scale"] == "Unknown"
    assert "not comparable" in rating["target"] and r["goal_status"] == "unknown"


def msg(id, minute, incoming, body):
    return {"id": id, "listing_id": 1, "conversation_id": 1, "at": NOW - timedelta(days=10) + timedelta(minutes=minute), "incoming": incoming, "body": body}


def test_reply_proxy_closes_each_exchange_and_skips_ack_and_unrelated_schedule():
    d = data(); d["messages"] = [msg(1, 0, True, "What is the wifi password?"), msg(2, .5, False, "Thanks, we received your message"), msg(3, 1, False, "The wifi password is in the router instructions."), msg(4, 10, True, "Where can we park our car?"), msg(5, 11, False, "Checkout is at 10 tomorrow morning."), msg(6, 20, False, "Please park your car in the marked parking space.")]
    r = estimate_response(d, W, NOW)
    assert r["value"] == 5.5 and r["denominator"] == 2 and r["numerator"] == 1
    assert r["goal_status"] == "unknown"
    assert "dispatch" in r["method"] and "not confirmed missed" in r["method"]


def test_active_issue_load_keeps_carryins_and_dedupes_only_saved_identity():
    d = data(); d["issues"] = [{"id": 1, "listing_id": 1, "reservation_id": 10, "complaint_key": "sink", "date": date(2024, 1, 1), "operational_status": "need_attention", "resolution_state": "resolved"}, {"id": 2, "listing_id": 1, "reservation_id": 10, "complaint_key": "sink", "operational_status": "stuck"}, {"id": 3, "listing_id": 1, "reservation_id": 11, "complaint_key": "sink", "operational_status": "resolved"}]
    r = estimate_issues(d)
    assert r["value"] == 1 and r["denominator"] == 1 and not r["trend"]
    assert "proxy" in r["denominator_label"] and r["data_status"] == "observed"
    assert r["diagnostics"][1]["value"] == 1
    assert "catalog entries (unit proxy)" in r["target"]
    assert "catalog entries (unit proxy)" in r["visual_label"]
    assert "provisional" in r["visual_label"] and r["goal_status"] == "unknown"


def test_cleaning_includes_closed_incidents_excludes_old_and_shows_ambiguity():
    d = data(); d["reservations"] = [booking(), booking(2)]
    d["issues"] = [{"id": 1, "listing_id": 1, "reservation_id": 1, "category": "cleanliness", "operational_status": "resolved"}, {"id": 2, "listing_id": 1, "reservation_id": 1, "category": "cleanliness", "operational_status": "need_attention"}, {"id": 3, "listing_id": 1, "reservation_id": 2, "category": "linens", "operational_status": "resolved"}, {"id": 4, "listing_id": 1, "reservation_id": 99, "category": "cleanliness", "date": date(2024, 1, 1)}]
    r = estimate_cleaning(d, W)
    assert r["value"] == 50 and r["numerator"] == 1 and r["denominator"] == 2
    assert r['audit_rate_per_10'] == 5
    assert r["diagnostics"][2]["value"] == 0
    assert r["diagnostics"][4]["value"] == "Active and resolved"
    assert r['label'] == 'stays had cleaning issues'
    d["reservations"] = []
    assert estimate_cleaning(d, W)["value"] is None


def test_no_data_does_not_create_estimated_zero_and_goal2_is_deferred():
    p = build_operations({"allow_estimates": True}, as_of=NOW)
    assert all(x["value"] is None for x in p["metrics"])
    assert p["metrics"][1]["data_status"] == "deferred"
    assert p["metrics"][1]["display"] == "Deferred"


def test_upstream_stale_is_separate_from_estimate_confidence():
    d = data(); d.update(allow_estimates=True, sources={"catalog": {"status": "observed", "checked_at": (NOW - timedelta(days=3)).isoformat()}})
    d["issues"] = []
    r = next(x for x in build_operations(d, as_of=NOW)["metrics"] if x["key"] == "issues")
    assert r["data_status"] == "observed" and r["source_freshness"] == "stale_at_calculation"


def test_rolling_review_cohorts_include_recent_vrbo_booking_and_open_opportunities():
    d = data()
    d["reservations"] = [booking(1, channel="vrbo", arrival=date(2026, 10, 20), departure=date(2026, 10, 22)), booking(2, channel="vrbo"), booking(3, channel="booking", arrival=date(2026, 10, 20), departure=date(2026, 10, 22)), booking(4, channel="booking", arrival=date(2026, 7, 23), departure=date(2026, 7, 24)), booking(5, arrival=date(2026, 10, 20), departure=date(2026, 10, 22))]
    d["reviews"] = [{"id": 1, "listing_id": 1, "reservation_id": 1, "channel": "vrbo", "date": date(2026, 10, 22), "origin": "Guest", "status": "published"}]
    r = estimate_reviews(d, W)
    channels = {x["group"]: x for x in r["breakdown"][:3]}
    assert channels["Vrbo"]["reviewed"] == 1
    assert channels["Vrbo"]["unreviewed"] == channels["Vrbo"]["still_open_opportunities"] == 1
    assert channels["Vrbo"]["value"] == "50%"
    assert channels["Booking"]["opportunities"] == 2
    assert channels["Booking"]["still_open_opportunities"] == channels["Booking"]["unreviewed_window_elapsed"] == 1
    assert channels["Airbnb"]["still_open_opportunities"] == r["denominator"] == 1
    assert r["value"] == 0 and r["goal_status"] == "unknown"


def test_review_open_window_can_use_verified_source_policy_without_dropping_cohort():
    d = data(); d["reservations"] = [booking()]
    d["review_policies"] = {"airbnb": {"verified": True, "review_window_days": 100}}
    row = estimate_reviews(d, W)["breakdown"][0]
    assert row["opportunities"] == row["still_open_opportunities"] == 1
    assert "Verified source policy" in row["review_window"]


def test_ratings_stay_native_by_channel_and_only_use_provenanced_scale_contracts():
    d = data()
    template = {"listing_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 8, 2)}
    d["reviews"] = [{**template, "id": 1, "channel": "airbnb", "rating": 4.9, "native_scale": 5, "scale_verified": True, "scale_provenance": "Verified original Airbnb API field"}, {**template, "id": 2, "channel": "booking", "rating": 9.8}, {**template, "id": 3, "channel": "vrbo", "rating": 4.8, "native_scale": 5, "scale_verified": True}]
    d["rating_scales"] = {"booking": {"verified": True, "native_scale": 10, "provenance": "Verified Booking source schema"}}
    rows = {x["channel"]: x for x in estimate_reviews(d, W)["breakdown"] if x["group"] == "EH"}
    assert rows["Airbnb"]["value"] == 4.9 and rows["Airbnb"]["native_scale"] == 5
    assert rows["Booking"]["value"] == 9.8 and rows["Booking"]["native_scale"] == 10
    assert "not comparable" in rows["Booking"]["target"]
    assert rows["Vrbo"]["value"] == 4.8 and rows["Vrbo"]["native_scale"] == "Unknown"
    assert "not comparable" in rows["Vrbo"]["target"]


def test_cleaning_affected_checkins_do_not_hide_multiple_record_identity_scenarios():
    d = data(); d["reservations"] = [booking(), booking(2)]
    common = {"listing_id": 1, "reservation_id": 1, "category": "cleanliness", "operational_status": "resolved"}
    d["issues"] = [{**common, "id": 1, "complaint_key": "dirty_bathroom"}, {**common, "id": 2, "complaint_key": "dirty_bathroom"}, {**common, "id": 3, "complaint_key": "dirty_kitchen"}, {"id": 4, "listing_id": 1, "reservation_id": 2, "category": "linens"}]
    d["issues"].append(d["issues"][0])  # Same source ID is not a second source record.
    r = estimate_cleaning(d, W); row = r["breakdown"][0]
    assert r["value"] == 50 and r["numerator"] == 1
    assert row["strict_saved_identities"] == 2 and row["strict_source_records"] == 3
    assert row["saved_identity_scenario_per_10"] == 10
    assert row["source_record_scenario_per_10"] == row["including_ambiguous_identity_per_10"] == 15
    assert r["goal_status"] == "unknown" and "distinct-incident goal" in r["method"]
    assert all("target" not in x for x in r["trend"])
