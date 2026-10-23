"""Synthetic fixtures test contracts, never act as dashboard actuals."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from dashboard.kpi.operations import build_operations, local_window, months_before

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def evidence():
    return {"listings": [{"id": 1, "name": "Property A", "timezone": "America/New_York", "physical_unit_ids": ["A"], "cohort_verified": True, "comparable": True, "units_verified": True}], "sources": {s: {"status": "complete", "checked_at": NOW.isoformat()} for s in ("catalog", "reservations", "reviews", "messages", "issues", "content")}}


def result(data, key):
    return next(x for x in build_operations(data, as_of=NOW)["metrics"] if x["key"] == key)


def booking(id=1, year=2026, amount=110, **kwargs):
    d = date(year, 8, 1)
    return {"id": id, "listing_id": 1, "arrival": d, "departure": d + timedelta(days=1), "status": "booked", "channel": "airbnbOfficial", "currency": "USD", "financials_verified": True, "refunds_reconciled": True, "allocation_verified": True, "nightly_lodging": [{"date": d, "unit_id": "A", "amount": amount}], **kwargs}


def test_calendar_months_reference_leap_and_local_day_boundaries():
    w = local_window(NOW, "America/New_York")
    assert (w["start"], w["end"], w["days"]) == (date(2026, 7, 23), date(2026, 10, 23), 92)
    assert months_before(date(2024, 5, 31)) == date(2024, 2, 29)
    assert months_before(date(2025, 5, 31)) == date(2025, 2, 28)
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)
    assert local_window(datetime(2026, 10, 23, 1, tzinfo=timezone.utc), "America/Los_Angeles")["end"] == date(2026, 10, 22)
    assert local_window(NOW, "unknown") is None


def test_exact_six_original_goals_no_fabricated_actuals_or_history():
    p = build_operations({}, as_of=NOW)
    assert [x["goal"] for x in p["metrics"]] == [1, 2, 3, 4, 7, 8]
    assert p["owner"] == "Usman" and p["deadline"] == "2026-12-31"
    assert all(x["value"] is None and x["goal_status"] == "unknown" for x in p["metrics"])
    assert not next(x for x in p["metrics"] if x["key"] == "issues")["trend"]


def test_revenue_reconciled_lodging_not_total_price_cancelled_or_ical():
    data = evidence()
    data["reservations"] = [booking(1), booking(2, 2025, 100), booking(3, amount=10000, status="cancelled"), booking(4, amount=10000, channel="customIcal")]
    r = result(data, "revenue")
    assert r["value"] == 100 and r["goal_status"] == "met"
    assert r["breakdown"][0]["current_usd"] == 110
    assert r["breakdown"][0]["growth_pct"] == 10
    # A reconciled lodging refund changes net nightly lodging, not taxes or fees.
    data["reservations"][0]["nightly_lodging"][0]["amount"] = 90
    r = result(data, "revenue")
    assert r["breakdown"][0]["growth_pct"] == -10
    assert r["goal_status"] == "off_target"


def test_revenue_boundary_nights_and_duplicate_package_allocation():
    data = evidence()
    row = booking()
    row.update(arrival=date(2026, 7, 22), departure=date(2026, 7, 24), booking_key="booking-source-1", nightly_lodging=[{"date": "2026-07-22", "unit_id": "A", "amount": 100}, {"date": "2026-07-23", "unit_id": "A", "amount": 110}])
    duplicate = deepcopy(row); duplicate["id"] = 2
    data["reservations"] = [row, duplicate, booking(3, 2025, 100)]
    r = result(data, "revenue")
    assert r["breakdown"][0]["current_usd"] == 110
    duplicate["nightly_lodging"][1]["amount"] = 999
    assert result(data, "revenue")["goal_status"] == "unknown"


@pytest.mark.parametrize("field", ["refunds_reconciled", "financials_verified", "allocation_verified"])
def test_unreconciled_finances_never_pass(field):
    data = evidence(); data["reservations"] = [booking(), booking(2, 2025, 100)]
    data["reservations"][0][field] = False
    assert result(data, "revenue")["goal_status"] == "unknown"


def test_missing_night_wrong_unit_zero_prior_and_missing_timezone():
    for change in ("night", "unit", "prior", "timezone"):
        data = evidence(); data["reservations"] = [booking(), booking(2, 2025, 100)]
        if change == "night": data["reservations"][0]["departure"] += timedelta(days=1)
        if change == "unit": data["reservations"][0]["nightly_lodging"][0]["unit_id"] = "unmapped"
        if change == "prior": data["reservations"][1]["nightly_lodging"][0]["amount"] = 0
        if change == "timezone": data["listings"][0]["timezone"] = None
        assert result(data, "revenue")["goal_status"] == "unknown"


def test_each_property_not_portfolio_growth():
    data = evidence(); data["listings"].append({**data["listings"][0], "id": 2, "name": "B", "physical_unit_ids": ["B"]})
    b = booking(3, amount=90, listing_id=2); b["nightly_lodging"][0]["unit_id"] = "B"
    old = booking(4, 2025, 100, listing_id=2); old["nightly_lodging"][0]["unit_id"] = "B"
    data["reservations"] = [booking(amount=1000), booking(2, 2025, 100), b, old]
    r = result(data, "revenue")
    assert r["display"] == "1 / 2" and r["goal_status"] == "off_target"


def test_content_pricing_and_duplicate_ids_do_not_prove_refresh():
    data = evidence(); data["content_ledger_complete"] = True
    data["content_events"] = [{"id": 1, "listing_id": 1, "kind": "pricing", "verified": True, "meaningful": True, "published_at": "2026-08-04T12:00Z"}]
    r = result(data, "content")
    assert all(x["value"] == 0 for x in r["trend"])
    assert r["goal_status"] == "unknown"  # channel health unverified
    data.pop("content_ledger_complete")
    assert all(x["value"] is None for x in result(data, "content")["trend"])


def test_review_cohort_maturation_duplicate_reviews_and_unconverted_native_scale():
    data = evidence(); data["review_policies"] = {"airbnb": {"verified": True, "maturation_days": 14}}
    data["reservations"] = [booking(), booking(2, arrival=date(2026, 10, 20), departure=date(2026, 10, 22)), booking(3, status="cancelled")]
    review = {"id": 1, "listing_id": 1, "reservation_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 8, 3), "rating": 10, "native_scale": 10, "scale_verified": True, "channel": "airbnb"}
    data["reviews"] = [review, {**review, "id": 2}, {**review, "id": 3, "reservation_id": None}]
    r = result(data, "reviews")
    assert r["numerator"] == 1 and r["denominator"] == 1
    assert r["breakdown"][0]["coverage_pct"] == 100
    assert r["goal_status"] == "unknown"
    assert r["breakdown"][3]["native_5_average"] is None
    assert any("native scale 10" in x["label"] for x in r["diagnostics"])


def test_review_checkout_left_boundary_counts_without_revenue_window_nights():
    data = evidence(); data["review_policies"] = {"airbnb": {"verified": True, "maturation_days": 14}}
    data["reservations"] = [booking(arrival=date(2026, 7, 22), departure=date(2026, 7, 23), nightly_lodging=[{"date": "2026-07-22", "unit_id": "A", "amount": 110}])]
    data["reviews"] = [{"id": 1, "listing_id": 1, "reservation_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 7, 24), "rating": 10, "channel": "airbnb"}]
    r = result(data, "reviews")
    assert r["numerator"] == r["denominator"] == 1
    assert result(data, "revenue")["diagnostics"][0]["value"] == 0


def test_cancelled_published_review_proves_opportunity_unknown_cancellation_does_not():
    data = evidence(); data["review_policies"] = {"airbnb": {"verified": True, "maturation_days": 14}}
    data["reservations"] = [booking(status="cancelled"), booking(2, status="cancelled", cancellation_date=datetime(2026, 8, 2)), booking(3, status="cancelled", cancellation_date=datetime(2026, 7, 30))]
    data["reviews"] = [{"id": 1, "listing_id": 1, "reservation_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 8, 3), "rating": 10, "channel": "airbnb"}]
    r = result(data, "reviews")
    assert r["numerator"] == r["denominator"] == 1
    unknowns = [x for x in r["exceptions"] if "cancellation after arrival" in x["action"]]
    assert [x["source_id"] for x in unknowns] == ["2"]
    assert r["goal_status"] == "unknown"
    data["reviews"][0]["channel"] = "vrbo"
    assert result(data, "reviews")["value"] is None


def test_currently_archived_listing_remains_in_historical_review_cohort():
    data = evidence(); data["historical_listings"] = [{"id": 2, "name": "Archived property", "timezone": "America/New_York"}]
    data["review_policies"] = {"airbnb": {"verified": True, "maturation_days": 14}}
    data["reservations"] = [booking(listing_id=2)]
    data["reviews"] = [{"id": 1, "listing_id": 2, "reservation_id": 1, "origin": "Guest", "status": "published", "date": date(2026, 8, 3), "rating": 10, "channel": "airbnb"}]
    r = result(data, "reviews")
    assert r["numerator"] == r["denominator"] == 1
    assert any(x["group"] == "Archived property" for x in r["breakdown"])


def message(id, minute, incoming, **kwargs):
    return {"id": id, "listing_id": 1, "conversation_id": 1, "at": f"2026-08-01T12:{minute:02d}:00Z", "incoming": incoming, "original_timestamp_verified": True, "sent": not incoming, "needs_reply_verified": True, "needs_reply": True, **kwargs}


def test_response_meaningful_ai_answers_ack_and_exact_five_minute_limit():
    data = evidence(); data["messages_complete"] = True
    data["messages"] = [message(1, 0, True), message(2, 1, True), message(3, 2, False, answer_kind="acknowledgement"), message(4, 3, False, answer_kind="unrelated_scheduled"), message(5, 5, False, automated=True, answer_kind="meaningful", semantics_verified=True)]
    r = result(data, "response")
    assert r["value"] == 5 and r["numerator"] == 0 and r["denominator"] == 1
    assert r["goal_status"] == "off_target"
    data["messages"][-1]["at"] = "2026-08-01T12:04:59Z"
    assert result(data, "response")["goal_status"] == "met"


def test_response_proxy_is_not_actual_and_carryin_overdue_blocks_success():
    data = evidence(); data["messages_complete"] = True
    data["messages"] = [message(1, 0, True), message(2, 1, False), message(3, 2, False)]
    r = result(data, "response")
    assert r["value"] is None and r["diagnostics"][1]["value"] == 1
    data["messages"] = [message(1, 0, True), message(2, 1, False, answer_kind="meaningful", semantics_verified=True), {**message(3, 0, True), "at": "2026-07-01T12:00Z", "conversation_id": 2}]
    r = result(data, "response")
    assert r["value"] == 1 and r["goal_status"] == "off_target"


@pytest.mark.parametrize("sent", [False, None])
def test_written_answer_without_dispatch_match_keeps_sla_unscored(sent):
    data = evidence(); data["messages_complete"] = True
    data["messages"] = [message(1, 0, True), message(2, 1, False, answer_kind="meaningful", semantics_verified=True), {**message(3, 0, True), "conversation_id": 2}, {**message(4, 1, False, answer_kind="meaningful", semantics_verified=True, sent=sent), "conversation_id": 2}]
    r = result(data, "response")
    assert r["value"] == 1 and r["goal_status"] == "unknown"
    assert any("No dispatch match does not establish a missed request" in x["action"] for x in r["exceptions"])


def test_issue_carryins_conflicts_and_no_fabricated_history():
    data = evidence(); data.update(issues_readable=True, issues_complete=True)
    data["issues"] = [{"id": 1, "listing_id": 1, "date": "2024-01-01", "operational_status": "need_attention", "resolution_state": "resolved"}, {"id": 2, "listing_id": 1, "operational_status": "resolved"}]
    r = result(data, "issues")
    assert r["value"] == 1 and r["denominator"] == 1 and r["goal_status"] == "unknown"
    assert not r["trend"] and any("Reconcile narrative" in x["action"] for x in r["exceptions"])
    data["listings"][0].pop("units_verified")
    assert result(data, "issues")["denominator"] is None


def test_daily_issue_status_events_in_property_local_time():
    data = evidence(); data.update(issues_readable=True, issues_complete=True, issue_history_complete=True)
    data["issues"] = [{"id": 1, "listing_id": 1, "operational_status": "resolved"}]
    data["issue_initial_states"] = {1: "need_attention"}
    data["issue_events"] = [{"issue_id": 1, "status": "resolved", "at": "2026-07-25T02:00Z"}]
    r = result(data, "issues")
    assert len(r["trend"]) == 92
    assert r["trend"][0]["value"] == 1 and r["trend"][1]["value"] == 0
    assert r["goal_status"] == "met"


def test_cleaning_candidates_not_numerator_and_incident_deduplication():
    data = evidence(); data.update(cleaning_contract_verified=True, checkins_complete=True)
    data["reservations"] = [booking(checkin_verified=True)]
    incident = {"id": 1, "listing_id": 1, "reservation_id": 1, "category": "cleaning", "category_verified": True, "incident_verified": True, "incident_id": "distinct-1"}
    data["issues"] = [incident, {**incident, "id": 2}]
    r = result(data, "cleaning")
    assert r["numerator"] == 1 and r["denominator"] == 1 and r["value"] == 10
    data["issues"].append({"id": 3, "listing_id": 1, "cleaning_candidate": True})
    assert result(data, "cleaning")["goal_status"] == "unknown"
    data["reservations"] = []
    assert result(data, "cleaning")["value"] is None


@pytest.mark.parametrize("state", ["partial", "error", "inaccessible", "missing"])
def test_incomplete_inaccessible_sources_never_green(state):
    data = evidence(); data["reservations"] = [booking(), booking(2, 2025, 100)]
    data["sources"]["reservations"]["status"] = state
    r = result(data, "revenue")
    assert r["value"] == 100 and r["goal_status"] == "unknown" and r["data_status"] == "partial"


def test_stale_sources_no_green_and_calculation_does_not_refresh_them():
    data = evidence(); data["reservations"] = [booking(), booking(2, 2025, 100)]
    old = (NOW - timedelta(days=2)).isoformat()
    data["sources"]["reservations"]["checked_at"] = old
    r = result(data, "revenue")
    assert r["data_status"] == "stale" and r["goal_status"] == "unknown"
    assert r["sources"][1]["checked_at"] == old


def test_missing_timezones_do_not_produce_zero_observed_window_counts():
    data = evidence(); data["listings"][0]["timezone"] = None
    data["reservations"] = [booking()]
    r = result(data, "revenue")
    assert r["diagnostics"][0]["value"] is None and r["observation"] is None


def test_unknown_issue_status_retained_as_possibly_active():
    data = evidence(); data["issues_readable"] = True
    data["issues"] = [{"id": 1, "listing_id": 1, "operational_status": "unrecognized-status"}]
    r = result(data, "issues")
    assert r["value"] == 1 and r["goal_status"] == "unknown"


def test_nullable_review_origin_status_cannot_crash_scorecard():
    data = evidence(); data["reviews"] = [{"id": 1, "listing_id": 1, "origin": None, "status": None}]
    assert len(build_operations(data, as_of=NOW)["metrics"]) == 6


def test_two_unverified_reply_exchanges_have_independent_proxy_clocks():
    data = evidence(); data["messages"] = [message(1, 0, True), message(2, 1, False), message(3, 20, True), message(4, 30, False)]
    r = result(data, "response")
    assert r["diagnostics"][0]["value"] == 5.5 and r["diagnostics"][1]["value"] == 2


def test_unknown_reply_obligation_cannot_certify_off_target():
    data = evidence(); data["messages_complete"] = True
    data["messages"] = [message(1, 0, True), message(2, 1, False, answer_kind="meaningful", semantics_verified=True), {**message(3, 20, True, needs_reply_verified=False), "conversation_id": 2}]
    assert result(data, "response")["goal_status"] == "unknown"


def test_growth_just_below_threshold_is_not_rounded_to_success():
    data = evidence(); data["reservations"] = [booking(amount=109.996), booking(2, 2025, 100)]
    r = result(data, "revenue")
    assert r["numerator"] == 0 and r["goal_status"] == "off_target"


def test_complete_zero_booking_window_is_minus_100_and_unrelated_dates_ignored():
    data = evidence(); data["revenue_window_complete"] = True
    data["reservations"] = [booking(2, 2025, 100), booking(3, arrival=date(2026, 1, 1), departure=date(2026, 1, 2), financials_verified=False)]
    r = result(data, "revenue")
    assert r["breakdown"][0]["growth_pct"] == -100
    assert r["goal_status"] == "off_target" and r["breakdown"][0]["current_usd"] == 0
    data.pop("revenue_window_complete")
    assert result(data, "revenue")["goal_status"] == "unknown"


def test_cleaning_threshold_uses_exact_fraction_and_ignores_old_candidates():
    data = evidence(); data.update(cleaning_contract_verified=True, checkins_complete=True, issues_readable=True)
    data["reservations"] = [booking(i, checkin_verified=True) for i in range(1, 202)]
    data["issues"] = [{"id": i, "listing_id": 1, "reservation_id": i, "category": "cleaning", "category_verified": True, "incident_verified": True, "incident_id": str(i), "operational_status": "resolved"} for i in range(1, 21)]
    data["issues"].append({"id": 999, "listing_id": 1, "cleaning_candidate": True, "date": date(2024, 1, 1)})
    r = result(data, "cleaning")
    assert r["value"] == .995 and r["goal_status"] == "met"
    assert r["diagnostics"][0]["value"] == 0


def test_source_errors_do_not_become_observed_zero_window_counts():
    data = evidence(); data["sources"]["reservations"]["status"] = "error"
    data["sources"]["reviews"]["status"] = "error"
    assert result(data, "revenue")["diagnostics"][0]["value"] is None
    assert result(data, "reviews")["diagnostics"][0]["value"] is None
