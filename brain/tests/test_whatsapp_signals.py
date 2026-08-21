import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from brain.services import (
    _ask_search_terms,
    BrainService,
    ask_explicit_scope_is_known,
    ask_active_signal_filter_terms,
    ask_core_operating_question_key,
    ask_insufficient_response,
    ask_model_question,
    ask_question_should_start_from_active_signals,
    ask_response_is_evidence_backed,
    ask_signal_categories_for_question,
    compact_whatsapp_focus_for_operator,
    core_operating_answer_text,
    build_whatsapp_group_memory_payload,
    infer_whatsapp_listing,
    listing_aliases_for_whatsapp,
    whatsapp_group_memory_highlight,
    whatsapp_group_memory_message_refs,
    whatsapp_memory_highlight_sort_key,
    whatsapp_open_loop_candidate,
    whatsapp_resolution_matches_topic,
    whatsapp_resolution_candidate,
    whatsapp_signal_title,
)


class WhatsAppSignalTests(unittest.TestCase):
    def test_brain_service_keeps_core_and_search_whatsapp_memory_evidence_methods_separate(self):
        self.assertTrue(callable(getattr(BrainService, "_ask_whatsapp_group_memory_core_evidence_rows", None)))
        self.assertTrue(callable(getattr(BrainService, "_ask_whatsapp_group_memory_evidence_rows", None)))
        self.assertIsNot(
            getattr(BrainService, "_ask_whatsapp_group_memory_core_evidence_rows"),
            getattr(BrainService, "_ask_whatsapp_group_memory_evidence_rows"),
        )

    def test_cleaning_request_is_open_loop(self):
        candidate = whatsapp_open_loop_candidate("Can you please confirm the cleaner has the unit ready by 4?")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["topic_key"], "cleaning_turnover")
        self.assertEqual(candidate["severity"], "medium")

    def test_access_issue_gets_high_priority_topic(self):
        candidate = whatsapp_open_loop_candidate("Guest says the door code is not working. Can someone check now?")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["topic_key"], "guest_review_risk")
        self.assertEqual(candidate["severity"], "high")

    def test_resolution_message_closes_cleaning_topic(self):
        resolution = whatsapp_resolution_candidate("Cleaner confirmed, unit is cleaned and ready.")

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution["topic_key"], "cleaning_turnover")

    def test_generic_resolution_can_close_any_topic_in_thread(self):
        resolution = whatsapp_resolution_candidate("Done.")

        self.assertIsNotNone(resolution)
        self.assertTrue(whatsapp_resolution_matches_topic(resolution, "*"))
        self.assertTrue(whatsapp_resolution_matches_topic(resolution, "maintenance_plumbing"))
        self.assertTrue(whatsapp_resolution_matches_topic(resolution, "owner_decision"))

    def test_vendor_resolution_closes_maintenance_but_not_owner_decision(self):
        resolution = whatsapp_resolution_candidate("It is fixed now.")

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution["topic_key"], "vendor_followup")
        self.assertTrue(whatsapp_resolution_matches_topic(resolution, "maintenance_plumbing"))
        self.assertTrue(whatsapp_resolution_matches_topic(resolution, "maintenance_hvac"))
        self.assertFalse(whatsapp_resolution_matches_topic(resolution, "owner_decision"))

    def test_low_signal_chat_is_ignored(self):
        self.assertIsNone(whatsapp_open_loop_candidate("Thanks"))
        self.assertIsNone(whatsapp_open_loop_candidate("Sounds good."))
        self.assertIsNone(whatsapp_open_loop_candidate("https://drive.google.com/drive/folders/abc?usp=share_link"))

    def test_routine_cleaning_schedule_is_ignored(self):
        self.assertIsNone(whatsapp_open_loop_candidate("@Saad just a reminder! We have a Check Out tomorrow. Thanks,"))
        self.assertIsNone(whatsapp_open_loop_candidate("*Cleanings on June 27, 2026:* 1. The Acorn Cabin - Check Out"))

    def test_committed_guest_update_is_resolution_not_open_loop(self):
        text = "No problem, I'll inform them late check-out is not possible."

        self.assertIsNone(whatsapp_open_loop_candidate(text))
        self.assertIsNotNone(whatsapp_resolution_candidate(text))

    def test_committed_call_and_handoff_updates_are_resolution_not_open_loop(self):
        call_text = "I will call the Lodge Guests myself."
        handoff_text = "I gave number to Robert to call them he has to call."

        self.assertIsNone(whatsapp_open_loop_candidate(call_text))
        self.assertIsNotNone(whatsapp_resolution_candidate(call_text))
        self.assertIsNone(whatsapp_open_loop_candidate(handoff_text))
        self.assertIsNotNone(whatsapp_resolution_candidate(handoff_text))

    def test_acknowledged_investigation_does_not_close_open_loop(self):
        for text in (
            "I will check the hot tub.",
            "I'll ask the cleaner and get back to you.",
            "We will schedule Luis to look at the leak.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(whatsapp_resolution_candidate(text))

    def test_routine_location_questions_are_ignored(self):
        self.assertIsNone(whatsapp_open_loop_candidate("Do you know where are the trash cans kept for the Lodge House?"))
        self.assertIsNone(whatsapp_open_loop_candidate("Where do we put the trash?"))
        self.assertIsNone(whatsapp_open_loop_candidate("Where is the firewood kept?"))

    def test_generic_project_question_is_ignored(self):
        self.assertIsNone(whatsapp_open_loop_candidate("@Brian does the crockett run photographer going to take videos too?"))
        self.assertIsNone(whatsapp_open_loop_candidate("Also, where do we keep the firewood?"))
        self.assertIsNone(whatsapp_open_loop_candidate("Did the guests let you know about this?"))
        self.assertIsNone(whatsapp_open_loop_candidate("The original one that’s with the previous owner?"))

    def test_owner_approval_request_still_surfaces(self):
        candidate = whatsapp_open_loop_candidate("We need owner approval before buying the replacement table.")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["topic_key"], "owner_decision")

    def test_concrete_supply_request_still_surfaces(self):
        candidate = whatsapp_open_loop_candidate("Can you get one from the store and share the receipt here?")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["topic_key"], "general_followup")

    def test_confirmation_request_still_surfaces(self):
        candidate = whatsapp_open_loop_candidate("@GA Concierge plz send confirmation")

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["topic_key"], "general_followup")

    def test_portal_access_does_not_become_guest_access(self):
        candidate = whatsapp_open_loop_candidate("Can you check if she has access to the building portal invoice?")

        self.assertIsNotNone(candidate)
        self.assertNotEqual(candidate["topic_key"], "checkin_access")

    def test_signal_title_includes_group_and_topic(self):
        self.assertEqual(
            whatsapp_signal_title("Luminary Cleaning", "cleaning or turnover"),
            "Open loop in Luminary Cleaning: cleaning or turnover",
        )

    def test_whatsapp_listing_aliases_keep_internal_property_names(self):
        aliases = listing_aliases_for_whatsapp(["PT300-10H", "The Acorn Middlefork", "Blue Jay"])

        self.assertIn("pt300 10h", aliases)
        self.assertIn("10h", aliases)
        self.assertIn("the acorn middlefork", aliases)
        self.assertIn("acorn middlefork", aliases)
        self.assertIn("blue jay", aliases)

    def test_whatsapp_listing_inference_matches_group_property_name(self):
        result = infer_whatsapp_listing(
            thread_name="Blue Jay Cleaning",
            message_bodies=["Can you confirm the cleaner is done?"],
            listing_aliases=[
                {
                    "listing_id": 312298,
                    "portfolio_id": 1,
                    "listing_name": "Blue Jay",
                    "alias": "blue jay",
                }
            ],
        )

        self.assertEqual(result["listing_id"], 312298)
        self.assertEqual(result["match_source"], "thread")

    def test_whatsapp_listing_inference_matches_pt300_unit_in_message(self):
        result = infer_whatsapp_listing(
            thread_name="PT300-Luis Maintenance",
            message_bodies=["Can you check PT300-10H? The door code is not working."],
            listing_aliases=[
                {
                    "listing_id": 295670,
                    "portfolio_id": 3,
                    "listing_name": "PT300-10H",
                    "alias": "pt300 10h",
                },
                {
                    "listing_id": 295676,
                    "portfolio_id": 3,
                    "listing_name": "PT300-19M",
                    "alias": "pt300 19m",
                },
            ],
        )

        self.assertEqual(result["listing_name"], "PT300-10H")
        self.assertEqual(result["match_source"], "message")

    def test_whatsapp_listing_inference_refuses_portfolio_only_group(self):
        result = infer_whatsapp_listing(
            thread_name="PT300 Management",
            message_bodies=["Can someone check the guest message?"],
            listing_aliases=[
                {
                    "listing_id": 295670,
                    "portfolio_id": 3,
                    "listing_name": "PT300-10H",
                    "alias": "pt300 10h",
                }
            ],
        )

        self.assertEqual(result, {})

    def test_ask_search_terms_keep_group_words(self):
        self.assertEqual(
            _ask_search_terms("What happened in Luminary Resorts Management today?"),
            ["luminary", "resorts"],
        )
        self.assertIn("pt300", _ask_search_terms("Show me PT300 unresolved cleaning messages"))
        self.assertIn("pt300-20m", _ask_search_terms("What happened with PT300-20M?"))
        self.assertNotIn("high", _ask_search_terms("Why is PT300-18C high ROI?"))

    def test_ask_active_signal_filter_terms_keep_listing_codes(self):
        self.assertEqual(
            ask_active_signal_filter_terms(["pt300-20m", "messages", "luminary", "20m"]),
            ["pt300-20m", "luminary", "20m"],
        )

    def test_ask_operating_questions_start_from_active_signals(self):
        self.assertTrue(ask_question_should_start_from_active_signals("What did I miss today?", ["miss"]))
        self.assertTrue(ask_question_should_start_from_active_signals("Which operational loop is still unresolved?", []))
        self.assertTrue(ask_question_should_start_from_active_signals("What happened with PT300-20M?", ["pt300-20m"]))
        self.assertFalse(ask_question_should_start_from_active_signals("What happened?", []))
        self.assertFalse(ask_question_should_start_from_active_signals("Show me Luminary Resorts messages", ["luminary", "resorts"]))

    def test_ask_model_question_clarifies_high_roi_action_language(self):
        normalized = ask_model_question("Why is PT300-18C high ROI?")

        self.assertIn("high-ROI action opportunity", normalized)
        self.assertIn("not that the listing is currently performing well", normalized)

    def test_core_operating_question_keys_match_today_snapshot_questions(self):
        self.assertEqual(ask_core_operating_question_key("What did I miss?"), "missed")
        self.assertEqual(ask_core_operating_question_key("What needs action today?"), "action_today")
        self.assertEqual(
            ask_core_operating_question_key("Which guest issue could become a bad review?"),
            "guest_review_risk",
        )
        self.assertEqual(
            ask_core_operating_question_key("Which listing is losing booking momentum?"),
            "booking_momentum",
        )
        self.assertEqual(
            ask_core_operating_question_key("What action will create the highest ROI?"),
            "highest_roi",
        )
        self.assertEqual(
            ask_core_operating_question_key("Which action will create highest ROI?"),
            "highest_roi",
        )
        self.assertIsNone(ask_core_operating_question_key("Show me Luminary Resorts messages"))

    def test_core_operating_answer_hides_raw_whatsapp_handles(self):
        answer = core_operating_answer_text(
            {
                "answer": "PT300 Management is the thread most likely to contain missed operational context.",
                "title": "PT300 Management needs a skim",
                "summary": "Recent likely open topics: vendor follow-up by ._.: @273031406567620 for pending invoice.",
                "suggested_action": "Review PT300 Management in WhatsApp.",
            }
        )

        self.assertNotIn("@273031406567620", answer)
        self.assertNotIn("by ._.:", answer)
        self.assertIn("by team member", answer)

    def test_whatsapp_missed_focus_keeps_only_top_actionable_items(self):
        focus = compact_whatsapp_focus_for_operator(
            "Recent likely open topics: vendor follow-up by ._.: @273031406567620 for pending invoice. "
            "| guest issue by Richard Chen: Can you call 6N guest? "
            "| check-in by GA Conceriege 2: @273031406567620 Luis asked about code for 23J? "
            "| cleaning by Richard Chen: did Luis give us the cleaner contact?"
        )

        self.assertTrue(focus.startswith("Top missed items:"))
        self.assertIn("vendor follow-up by team member", focus)
        self.assertIn("guest issue by Richard Chen", focus)
        self.assertNotIn("check-in", focus)
        self.assertNotIn("@273031406567620", focus)
        self.assertNotIn("by ._.:", focus)

    def test_whatsapp_missed_focus_preserves_truncated_ellipsis(self):
        focus = compact_whatsapp_focus_for_operator(
            "Likely open topics: vendor follow-up by ._.: the manager should share the buil... "
            "| guest issue by Richard Chen: Can you call 6N guest?"
        )

        self.assertIn("buil...", focus)
        self.assertNotIn("buil;", focus)

    def test_whatsapp_missed_focus_truncates_at_word_boundary(self):
        focus = compact_whatsapp_focus_for_operator(
            "Likely open topics: vendor or maintenance follow-up by team member for the pending invoice "
            "with the building management, I think they have billed this to Kelly and she should have an "
            "access to their portal before we can confirm the exact charge. "
            "| guest issue by Richard Chen: Can you call 6N guest?",
            char_limit=190,
        )

        self.assertTrue(focus.endswith("..."))
        self.assertNotIn("thei...", focus)
        self.assertNotRegex(focus, r"\b[a-zA-Z]{1,3}\.\.\.$")

    def test_core_operating_answer_uses_compact_missed_focus(self):
        answer = core_operating_answer_text(
            {
                "key": "missed",
                "answer": "PT300 Management is the thread most likely to contain missed operational context.",
                "title": "PT300 Management needs a skim",
                "summary": compact_whatsapp_focus_for_operator(
                    "Likely open topics: vendor follow-up by ._.: @273031406567620 for pending invoice. "
                    "| guest issue by Richard Chen: Can you call 6N guest? "
                    "| check-in by GA Conceriege 2: @273031406567620 Luis asked about code for 23J?"
                ),
                "suggested_action": "Review PT300 Management in WhatsApp.",
            }
        )

        self.assertIn("Top missed items:", answer)
        self.assertNotIn("check-in", answer)
        self.assertNotIn("@273031406567620", answer)

    def test_core_operating_answer_skips_redundant_missed_title(self):
        answer = core_operating_answer_text(
            {
                "key": "missed",
                "answer": "PT300 Management is the thread most likely to contain missed operational context.",
                "title": "PT300 Management needs a skim",
                "summary": "Top missed items: vendor follow-up by team member.",
                "suggested_action": "Review PT300 Management in WhatsApp.",
            }
        )

        self.assertNotIn("needs a skim", answer)
        self.assertIn("Top missed items", answer)

    def test_core_operating_answer_skips_redundant_listing_title(self):
        answer = core_operating_answer_text(
            {
                "answer": "PT300-10H is the booking-momentum watch item.",
                "title": "PT300-10H",
                "summary": "PT300-10H has no bookings in the next 7, 30, or 60 days.",
                "suggested_action": "Verify Airbnb listing status.",
                "listing_name": "PT300-10H",
            }
        )

        self.assertNotIn("item. PT300-10H PT300-10H", answer)
        self.assertIn("item. PT300-10H has no bookings", answer)
        self.assertIn("Next: Verify Airbnb listing status.", answer)

    def test_core_operating_answer_skips_generic_highest_roi_signal_title(self):
        answer = core_operating_answer_text(
            {
                "key": "highest_roi",
                "answer": "PT300-18C has the highest-ROI revenue action right now.",
                "title": "Booking health needs action for PT300-18C",
                "summary": "PT300-18C has no bookings in the next 7, 30, or 60 days.",
                "suggested_action": "Cut rates 15%+ inside 21 days.",
                "listing_name": "PT300-18C",
            }
        )

        self.assertNotIn("Booking health needs action", answer)
        self.assertIn("PT300-18C has no bookings", answer)
        self.assertIn("Next: Cut rates", answer)

    def test_ask_signal_categories_for_common_operating_questions(self):
        self.assertEqual(
            ask_signal_categories_for_question("Which guest issue could become a bad review?"),
            {"guest_experience", "review_risk", "checkin_checkout_risk"},
        )
        self.assertEqual(
            ask_signal_categories_for_question("Which operational loop is still unresolved?"),
            {"operational_open_loop"},
        )
        self.assertEqual(
            ask_signal_categories_for_question("Which listing is losing booking momentum?"),
            {"revenue_booking_health"},
        )
        self.assertIsNone(ask_signal_categories_for_question("What did I miss today?"))

    def test_ask_response_requires_valid_citations(self):
        self.assertTrue(
            ask_response_is_evidence_backed(
                "PT300 has a review risk.",
                [{"evidence_id": 42, "reason": "Guest reported a cleanliness problem."}],
            )
        )
        self.assertFalse(ask_response_is_evidence_backed("PT300 has a review risk.", []))
        self.assertFalse(ask_response_is_evidence_backed("", [{"evidence_id": 42}]))

    def test_ask_insufficient_response_is_structured_refusal(self):
        response = ask_insufficient_response(
            prompt_version="test-prompt",
            reason="No stored evidence matched the question.",
            missing_data="Try naming a portfolio.",
            evidence_count=0,
        )

        self.assertTrue(response["insufficient_evidence"])
        self.assertEqual(response["confidence"], 0.0)
        self.assertEqual(response["citations"], [])
        self.assertIn("not have enough cited evidence", response["answer"])
        self.assertEqual(response["missing_data"], "Try naming a portfolio.")

    def test_ask_explicit_scope_requires_known_scope_token(self):
        known_scope_text = "urban stays pt300 management luminary resorts crestwood cleaning"

        self.assertTrue(ask_explicit_scope_is_known("PT300 towels", known_scope_text))
        self.assertTrue(ask_explicit_scope_is_known("Luminary Resorts", known_scope_text))
        self.assertFalse(ask_explicit_scope_is_known("Atlantis towels", known_scope_text))

    def test_whatsapp_group_memory_summarizes_open_topics_and_context(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        thread = SimpleNamespace(display_name="Luminary Resorts Management", provider_thread_id="group-1")
        messages = [
            SimpleNamespace(
                whatsapp_message_id=1,
                sender_name="Usman",
                body="Can you please confirm the cleaner has the unit ready by 4?",
                received_at=now - timedelta(days=2),
                raw_payload={"group_kind": "management"},
            ),
            SimpleNamespace(
                whatsapp_message_id=2,
                sender_name="GA Concierge",
                body="We also need approval before buying the replacement table.",
                received_at=now - timedelta(days=1, hours=2),
                raw_payload={"group_kind": "management"},
            ),
            SimpleNamespace(
                whatsapp_message_id=3,
                sender_name="Saad",
                body="The hot tub is not working and the guest is upset.",
                received_at=now - timedelta(hours=3),
                raw_payload={"group_kind": "management"},
            ),
        ]

        memory = build_whatsapp_group_memory_payload(
            thread=thread,
            messages=messages,
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["message_count"], 3)
        self.assertEqual(memory["participant_count"], 3)
        self.assertEqual(memory["status"], "watch")
        self.assertIn("Luminary Resorts Management", memory["summary"])
        self.assertIn("Likely open topics", memory["open_loop_summary"])
        self.assertIn("hot tub", memory["risk_summary"].lower())
        self.assertIn("approval", memory["decision_summary"].lower())
        self.assertIn("cleaner", memory["cleaning_maintenance_summary"].lower())
        self.assertEqual(memory["source_metadata"]["recent_unresolved_topics"][0]["message_id"], 3)

    def test_whatsapp_group_memory_message_refs_prioritize_actionable_messages(self):
        refs = whatsapp_group_memory_message_refs(
            {
                "recent_unresolved_topics": [
                    {"message_id": 10, "received_at": "2026-06-29T10:00:00", "excerpt": "Can you confirm completion?"},
                ],
                "recent_risk_updates": [
                    {"message_id": 11, "received_at": "2026-06-29T10:05:00", "excerpt": "Guest is upset."},
                ],
                "latest_updates": [
                    {"message_id": 10, "received_at": "2026-06-29T10:00:00", "excerpt": "Can you confirm completion?"},
                    {"received_at": "2026-06-29T10:06:00", "excerpt": "Fallback without id."},
                ],
            },
            limit=3,
        )

        self.assertEqual([ref.get("message_id") for ref in refs[:2]], [10, 11])
        self.assertEqual(refs[2]["excerpt"], "Fallback without id.")

    def test_whatsapp_group_memory_respects_resolution_messages(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        thread = SimpleNamespace(display_name="PT300 Management", provider_thread_id="group-2")
        messages = [
            SimpleNamespace(
                whatsapp_message_id=1,
                sender_name="Richard",
                body="Can you check the door code? Guest says it is not working.",
                received_at=now - timedelta(hours=5),
                raw_payload={"group_kind": "management"},
            ),
            SimpleNamespace(
                whatsapp_message_id=2,
                sender_name="Usman",
                body="Door code is fixed and guest confirmed access.",
                received_at=now - timedelta(hours=3),
                raw_payload={"group_kind": "management"},
            ),
        ]

        memory = build_whatsapp_group_memory_payload(
            thread=thread,
            messages=messages,
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "ok")
        self.assertIn("No likely unresolved", memory["open_loop_summary"])
        self.assertIn("guest confirmed access", memory["summary"].lower())

    def test_whatsapp_group_memory_keeps_loop_open_after_acknowledgement(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        thread = SimpleNamespace(display_name="Crestwood Maintenance", provider_thread_id="group-ack")
        messages = [
            SimpleNamespace(
                whatsapp_message_id=1,
                sender_name="Richard",
                body="Can you check the leak under the sink today?",
                received_at=now - timedelta(hours=4),
                raw_payload={"group_kind": "maintenance"},
            ),
            SimpleNamespace(
                whatsapp_message_id=2,
                sender_name="Chris",
                body="I will check it this afternoon.",
                received_at=now - timedelta(hours=3),
                raw_payload={"group_kind": "maintenance"},
            ),
        ]

        memory = build_whatsapp_group_memory_payload(
            thread=thread,
            messages=messages,
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "watch")
        self.assertIn("Likely open topics", memory["open_loop_summary"])
        self.assertIn("leak", memory["open_loop_summary"].lower())

    def test_whatsapp_group_memory_keeps_stale_history_without_today_attention(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        thread = SimpleNamespace(display_name="Blue Haven", provider_thread_id="group-3")
        messages = [
            SimpleNamespace(
                whatsapp_message_id=1,
                sender_name="Saad",
                body="Can you please ask the guest if they can check in later?",
                received_at=now - timedelta(days=10),
                raw_payload={"group_kind": "cleaning"},
            )
        ]

        memory = build_whatsapp_group_memory_payload(
            thread=thread,
            messages=messages,
            window_start=now - timedelta(days=60),
            window_end=now,
            window_days=60,
        )

        self.assertEqual(memory["status"], "ok")
        self.assertEqual(memory["source_metadata"]["attention_score"], 0.0)
        self.assertIn("Likely open topics", memory["open_loop_summary"])

    def test_whatsapp_group_memory_highlight_prioritizes_watch_items(self):
        now = datetime(2026, 6, 29, 12, 0, 0)
        watch_memory = SimpleNamespace(
            whatsapp_group_memory_id=7,
            portfolio_id=2,
            group_name="Crestwood Management",
            group_kind="management",
            status="watch",
            message_count=400,
            participant_count=6,
            memory_date=now.date(),
            last_message_at=now,
            summary="Crestwood Management: active discussion.",
            open_loop_summary="Likely open topics: plumbing by Chris: water still leaking.",
            risk_summary="Risk language: Chris: water still leaking.",
            decision_summary="",
            cleaning_maintenance_summary="Cleaning/maintenance context: Chris: water still leaking.",
            source_metadata={
                "attention_score": 88.0,
                "recent_unresolved_topics": [
                    {
                        "topic_label": "plumbing or water issue",
                        "sender": "Chris",
                        "excerpt": "water still leaking.",
                        "severity": "high",
                        "received_at": now.isoformat(),
                    }
                ],
                "recent_risk_updates": [],
                "recent_cleaning_maintenance_updates": [],
                "recent_decision_updates": [],
            },
        )
        ok_memory = SimpleNamespace(
            whatsapp_group_memory_id=8,
            portfolio_id=2,
            group_name="Crockett Run Management",
            group_kind="management",
            status="ok",
            message_count=900,
            participant_count=8,
            memory_date=now.date(),
            last_message_at=now + timedelta(minutes=5),
            summary="Crockett Run Management: quiet.",
            open_loop_summary="No likely unresolved WhatsApp loop remained after the latest closure signals.",
            risk_summary="No high-risk guest/review language stood out in the window.",
            decision_summary="",
            cleaning_maintenance_summary="",
            source_metadata={"attention_score": 0.0},
        )

        watch_highlight = whatsapp_group_memory_highlight(watch_memory, portfolio_name="crestwood")
        ok_highlight = whatsapp_group_memory_highlight(ok_memory, portfolio_name="Middlefork")

        self.assertEqual(watch_highlight["portfolio_name"], "crestwood")
        self.assertIn("water still leaking", watch_highlight["focus"])
        self.assertIn("Review Crestwood Management", watch_highlight["suggested_action"])
        self.assertLess(
            whatsapp_memory_highlight_sort_key(watch_highlight),
            whatsapp_memory_highlight_sort_key(ok_highlight),
        )


if __name__ == "__main__":
    unittest.main()
