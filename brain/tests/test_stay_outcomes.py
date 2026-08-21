from datetime import date, datetime, timedelta
from types import SimpleNamespace

from brain.ai import BrainAIClient
from brain.stay_outcomes import (
    StayOutcomeClassificationService,
    build_stay_outcome_input,
    initial_stay_outcome_checkout_cutoff,
    is_stay_outcome_eligible,
    listing_timezone_resolution,
    normalize_stay_outcome_result,
    scheduled_checkout_at_utc,
    stay_outcome_eligible_at,
)


def test_normalizer_derives_unresolved_from_material_issue_even_if_model_label_disagrees():
    result = normalize_stay_outcome_result(
        {
            "outcome": "smooth",
            "confidence": .94,
            "summary": "The hot water issue remained open.",
            "evidence_message_ids": [11, 999],
            "issues": [
                {
                    "issue_type": "essential_amenity",
                    "severity": "material",
                    "description": "Guest reported no hot water.",
                    "resolution_state": "unresolved",
                    "resolution_evidence": "No resolution appears in the conversation.",
                    "evidence_message_ids": [11, 999],
                }
            ],
        },
        valid_message_ids={11, 12},
    )

    assert result["outcome"] == "unresolved"
    assert result["unresolved_issue_count"] == 1
    assert result["evidence_message_ids"] == [11]


def test_normalizer_sends_low_confidence_result_to_needs_review():
    result = normalize_stay_outcome_result(
        {
            "outcome": "recovered",
            "confidence": .6,
            "summary": "Evidence is uncertain.",
            "evidence_message_ids": [21],
            "issues": [
                {
                    "issue_type": "maintenance",
                    "severity": "material",
                    "description": "Possible maintenance issue.",
                    "resolution_state": "resolved",
                    "resolution_evidence": "Unclear confirmation.",
                    "evidence_message_ids": [21],
                }
            ],
        },
        valid_message_ids={21},
    )

    assert result["outcome"] == "needs_review"


def test_input_uses_only_stay_window_and_excludes_automated_host_messages():
    reservation = SimpleNamespace(
        reservation_id=101,
        listing_id=5,
        arrival_date=date(2026, 8, 10),
        departure_date=date(2026, 8, 13),
    )
    messages = [
        SimpleNamespace(message_id=1, created_at=datetime(2026, 8, 7), is_incoming=1, sender_name="Guest", content_preview="Too early"),
        SimpleNamespace(message_id=2, created_at=datetime(2026, 8, 9), is_incoming=0, sender_name="Host (Automated)", content_preview="Template"),
        SimpleNamespace(message_id=3, created_at=datetime(2026, 8, 10), is_incoming=1, sender_name="Guest", content_preview="The door code is not working"),
        SimpleNamespace(message_id=4, created_at=datetime(2026, 8, 13, 12), is_incoming=0, sender_name="Host", content_preview="You confirmed that access is restored"),
    ]

    prepared = build_stay_outcome_input(reservation, messages, conversation_present=True)

    assert [message["message_id"] for message in prepared["payload"]["messages"]] == [3, 4]
    assert prepared["incoming_count"] == 1
    assert prepared["message_count"] == 2


def test_checkout_eligibility_uses_hostaway_time_and_property_timezone():
    reservation = SimpleNamespace(departure_date=date(2026, 8, 20))
    listing = SimpleNamespace(check_out_time=10, timezone_name="America/Los_Angeles")

    checkout_at = scheduled_checkout_at_utc(reservation, listing)

    assert checkout_at == datetime(2026, 8, 20, 17, 0)
    assert stay_outcome_eligible_at(reservation, listing) == datetime(2026, 8, 21, 17, 0)
    assert not is_stay_outcome_eligible(
        reservation,
        listing,
        reference_time=datetime(2026, 8, 21, 16, 59, 59),
        earliest_checkout_at=datetime(2026, 7, 20),
    )
    assert is_stay_outcome_eligible(
        reservation,
        listing,
        reference_time=datetime(2026, 8, 21, 17, 0),
        earliest_checkout_at=datetime(2026, 7, 20),
    )


def test_checkout_timezone_falls_back_to_synchronized_property_location():
    reservation = SimpleNamespace(departure_date=date(2026, 8, 20))
    listing = SimpleNamespace(
        check_out_time=11,
        timezone_name=None,
        city="Atlanta",
        state="GA",
    )

    assert listing_timezone_resolution(listing) == ("America/New_York", "state_fallback")
    assert scheduled_checkout_at_utc(reservation, listing) == datetime(2026, 8, 20, 15, 0)


def test_initial_cutoff_is_fixed_to_one_calendar_month_before_activation():
    activation = datetime(2026, 3, 31, 8, 30)

    assert initial_stay_outcome_checkout_cutoff(activation) == datetime(2026, 2, 28, 8, 30)


def test_input_is_capped_at_the_24_hour_analysis_timestamp():
    reservation = SimpleNamespace(
        reservation_id=102,
        listing_id=5,
        arrival_date=date(2026, 8, 10),
        departure_date=date(2026, 8, 13),
    )
    cutoff = datetime(2026, 8, 14, 10, 0)
    messages = [
        SimpleNamespace(message_id=1, created_at=cutoff, is_incoming=1, sender_name="Guest", content_preview="Thank you"),
        SimpleNamespace(
            message_id=2,
            created_at=cutoff + timedelta(seconds=1),
            is_incoming=1,
            sender_name="Guest",
            content_preview="This arrived after finalization",
        ),
    ]

    prepared = build_stay_outcome_input(
        reservation,
        messages,
        conversation_present=True,
        window_end=cutoff,
    )

    assert [message["message_id"] for message in prepared["payload"]["messages"]] == [1]
    assert prepared["payload"]["analysis_cutoff"] == cutoff.isoformat()


def test_classifier_uses_luna_without_changing_other_brain_models(monkeypatch):
    captured = {}

    class FakeAIClient:
        def __init__(self, model=None):
            captured["model"] = model

    monkeypatch.setattr("brain.stay_outcomes.BrainAIClient", FakeAIClient)
    service = StayOutcomeClassificationService(session=object(), main_session=object())

    assert captured["model"] == "gpt-5.6-luna"
    service.close()


def test_luna_structured_request_omits_incompatible_temperature_parameter():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"stays": []}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    client = BrainAIClient.__new__(BrainAIClient)
    client.model = "gpt-5.6-luna"
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )

    client._chat_json(
        [{"role": "user", "content": "classify"}],
        {"name": "test", "schema": {"type": "object"}, "strict": True},
    )

    assert captured["model"] == "gpt-5.6-luna"
    assert "temperature" not in captured


def test_store_never_overwrites_or_reanalyzes_an_existing_reservation():
    existing = SimpleNamespace(outcome="smooth", summary="Original result")

    class ExistingQuery:
        def filter(self, *_args):
            return self

        def first(self):
            return existing

    class FakeSession:
        def query(self, *_args):
            return ExistingQuery()

        def add(self, _row):
            raise AssertionError("An existing reservation must never get a second row")

    service = StayOutcomeClassificationService(
        session=FakeSession(),
        main_session=object(),
        ai_client=SimpleNamespace(model="gpt-5.6-luna"),
    )
    reservation = SimpleNamespace(reservation_id=77)

    stored = service._store(
        reservation,
        prepared={},
        result={},
        run_id=None,
        classification_source="openai",
    )

    assert stored is False
    assert existing.outcome == "smooth"
    assert existing.summary == "Original result"
