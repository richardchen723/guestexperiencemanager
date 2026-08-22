from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from brain.guest_experience import (
    GuestExperienceAnalysisService,
    analysis_window,
    build_stay_input,
    is_analysis_eligible,
    normalize_review_result,
    normalize_stay_result,
    scheduled_checkout_at_utc,
)


def test_api_backed_guest_experience_runner_is_disabled():
    with pytest.raises(RuntimeError, match="Codex scheduled task"):
        GuestExperienceAnalysisService()


def listing(**overrides):
    values = {
        "check_out_time": 10,
        "timezone_name": "America/Los_Angeles",
        "city": "Los Angeles",
        "state": "CA",
        "internal_listing_name": "Test Home",
        "name": "Test Home",
        "property_type_id": 1,
        "accommodates": 4,
        "bedrooms": 2,
        "bathrooms": 1,
        "country": "US",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def reservation(**overrides):
    values = {
        "reservation_id": 101,
        "listing_id": 5,
        "arrival_date": date(2026, 8, 18),
        "departure_date": date(2026, 8, 21),
        "channel_name": "airbnbOfficial",
        "source": "Airbnb",
        "status": "confirmed",
        "nights": 3,
        "number_of_guests": 2,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "pets": 0,
        "total_price": 800,
        "currency": "USD",
        "host_note": "Prepared a crib",
        "guest_note": "Late arrival",
        "comment": "VIP",
        "custom_field_values": '[{"name":"door","value":"blue"}]',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def message(message_id, created_at, incoming, body):
    return SimpleNamespace(
        message_id=message_id,
        created_at=created_at,
        is_incoming=1 if incoming else 0,
        sender_name="Guest" if incoming else "Support",
        sender_type="guest" if incoming else "host",
        message_type="text",
        has_attachment=0,
        content_preview=body,
    )


def test_eligibility_is_exactly_24_hours_after_property_local_checkout():
    stay = reservation()
    property_row = listing()
    checkout = scheduled_checkout_at_utc(stay, property_row)

    assert checkout == datetime(2026, 8, 21, 17, 0)
    assert not is_analysis_eligible(
        stay,
        property_row,
        reference_time=checkout + timedelta(hours=23, minutes=59),
    )
    assert is_analysis_eligible(
        stay,
        property_row,
        reference_time=checkout + timedelta(hours=24),
    )


def test_analysis_window_uses_one_calendar_month_not_unbounded_history():
    start, end = analysis_window(datetime(2026, 8, 31, 18, 0))

    assert start == datetime(2026, 7, 31, 18, 0)
    assert end == datetime(2026, 8, 30, 18, 0)


def test_stay_input_keeps_every_message_and_operational_stay_field():
    stay = reservation()
    rows = [
        message(index, datetime(2026, 8, 18) + timedelta(minutes=index), index % 2, f"message {index}")
        for index in range(1, 205)
    ]

    prepared = build_stay_input(
        stay,
        listing(),
        rows,
        analyzed_at=datetime(2026, 8, 22),
    )

    assert prepared["message_count"] == 204
    assert prepared["payload"]["messages"][-1]["text"] == "message 204"
    assert prepared["payload"]["stay"]["guest_note"] == "Late arrival"
    assert prepared["payload"]["stay"]["custom_field_values"][0]["name"] == "door"


def test_stay_quality_is_muted_when_no_guest_message_exists():
    result = normalize_stay_result(
        {
            "summary": "Model disagreed",
            "detailed_summary": "Model output",
            "issues": [],
        },
        valid_messages={1: "support", 2: "support"},
    )

    assert result["stay_quality"] == "muted"


def test_stay_issue_requires_a_real_guest_complaint_reference_and_derives_recovery():
    result = normalize_stay_result(
        {
            "summary": "Hot water restored",
            "detailed_summary": "Guest reported no hot water and later confirmed it worked.",
            "issues": [
                {
                    "issue_category": "essential amenity",
                    "summary": "No hot water",
                    "details": "The guest reported no hot water.",
                    "suggested_improvement": "Add a pre-arrival hot-water check.",
                    "severity": "material",
                    "resolution_state": "resolved",
                    "source_references": [
                        {"source_type": "message", "source_id": 11, "role": "complaint"},
                        {"source_type": "message", "source_id": 12, "role": "resolution"},
                        {"source_type": "message", "source_id": 999, "role": "context"},
                    ],
                }
            ],
        },
        valid_messages={11: "guest", 12: "support"},
    )

    assert result["stay_quality"] == "recovered"
    assert result["issues"][0]["issue_category"] == "essential_amenity"
    assert [ref["source_id"] for ref in result["issues"][0]["source_references"]] == [11, 12]


def test_review_issue_keeps_only_links_to_the_review_and_valid_source_part():
    result = normalize_review_result(
        {
            "summary": "Guest suggested a better mattress.",
            "issues": [
                {
                    "issue_category": "comfort",
                    "summary": "Uncomfortable mattress",
                    "details": "The private feedback says the mattress is too firm.",
                    "suggested_improvement": "Inspect and replace the mattress.",
                    "severity": "material",
                    "evidence_basis": "explicit_feedback",
                    "source_references": [
                        {"source_type": "review", "source_id": 77, "source_part": "private_feedback"},
                        {"source_type": "review", "source_id": 88, "source_part": "public_review"},
                    ],
                }
            ],
        },
        review_id=77,
    )

    assert len(result["issues"]) == 1
    assert result["issues"][0]["source_references"] == [
        {"source_type": "review", "source_id": 77, "source_part": "private_feedback"}
    ]
