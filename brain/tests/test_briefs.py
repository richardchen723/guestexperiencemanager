from datetime import date, datetime
from types import SimpleNamespace
import unittest

from brain.services import (
    BrainService,
    BrainRunService,
    ai_candidate_category,
    ai_structured_signal_rank_score,
    build_today_operating_snapshot,
    format_operating_brief_body,
    guest_message_risk_rank_score,
    is_decision_signal,
    open_loop_item_sort_key,
    select_operating_signals,
    should_generate_briefs_for_run_type,
    should_skip_ai_signal_candidate,
    signal_evidence_sort_key,
    signal_visible_to_portfolio_brief,
    whatsapp_memory_highlight_brief_item,
)


class BriefFormattingTests(unittest.TestCase):
    def test_brain_service_exposes_portfolio_name_map_for_open_loop_surfaces(self):
        self.assertTrue(callable(getattr(BrainService, "_portfolio_name_map", None)))
        self.assertTrue(callable(getattr(BrainService, "_combined_open_loop_items", None)))
        self.assertTrue(callable(getattr(BrainRunService, "_brief_combined_open_loop_items", None)))

    def test_brain_run_service_exposes_brief_snapshot_converters(self):
        self.assertTrue(callable(getattr(BrainRunService, "_snapshot_signal_input", None)))
        self.assertTrue(callable(getattr(BrainRunService, "_snapshot_memory_input", None)))
        self.assertTrue(callable(getattr(BrainRunService, "_snapshot_open_loop_input", None)))
        self.assertTrue(callable(getattr(BrainRunService, "_snapshot_booking_input", None)))

    def test_operating_brief_body_groups_counts_and_next_actions(self):
        body = format_operating_brief_body(
            subject="STR Signal Brain - Daily Brief",
            generated_date=date(2026, 6, 28),
            counts={
                "active_signals": 7,
                "guest_review_risks": 2,
                "open_loops": 3,
                "booking_roi": 1,
            },
            sections=[
                {
                    "title": "Needs Action Today",
                    "items": [
                        {
                            "title": "Open loop in Luminary Cleaning",
                            "portfolio_name": "Luminary Resorts",
                            "severity": "high",
                            "action": "Confirm the cleaner has a completion time.",
                        }
                    ],
                },
                {"title": "Booking Health / ROI", "items": []},
            ],
        )

        self.assertIn("Active signals: 7", body)
        self.assertIn("Guest/review risks: 2", body)
        self.assertIn("WhatsApp misses: 0", body)
        self.assertIn("Needs Action Today:", body)
        self.assertIn("Open loop in Luminary Cleaning [Luminary Resorts] (high)", body)
        self.assertIn("Next: Confirm the cleaner has a completion time.", body)
        self.assertIn("Booking Health / ROI:\nNone surfaced.", body)

    def test_operating_brief_body_includes_whatsapp_memory_section(self):
        body = format_operating_brief_body(
            subject="STR Signal Brain - Daily Brief",
            generated_date=date(2026, 6, 29),
            counts={
                "active_signals": 7,
                "guest_review_risks": 2,
                "open_loops": 3,
                "memory_highlights": 2,
                "booking_roi": 1,
            },
            sections=[
                {
                    "title": "What You Might Have Missed",
                    "items": [
                        {
                            "title": "PT300 Management: recent WhatsApp thread to review",
                            "portfolio_name": "Urban Stays",
                            "severity": "high",
                            "action": "Review PT300 Management in WhatsApp.",
                        }
                    ],
                }
            ],
        )

        self.assertIn("WhatsApp misses: 2", body)
        self.assertIn("What You Might Have Missed:", body)
        self.assertIn("PT300 Management: recent WhatsApp thread to review [Urban Stays] (high)", body)

    def test_operating_brief_body_puts_daily_snapshot_first(self):
        body = format_operating_brief_body(
            subject="STR Signal Brain - Daily Brief",
            generated_date=date(2026, 6, 29),
            counts={
                "active_signals": 4,
                "guest_review_risks": 1,
                "open_loops": 1,
                "memory_highlights": 1,
                "booking_roi": 1,
            },
            operating_snapshot=[
                {
                    "question": "What needs action today?",
                    "title": "Guest refund request at PT300-2M",
                    "answer": "Guest refund request at PT300-2M is the highest-priority action right now.",
                    "portfolio_name": "Urban Stays",
                    "status": "new",
                    "suggested_action": "Send recovery note and decide refund handling.",
                }
            ],
            sections=[
                {
                    "title": "Needs Action Today",
                    "items": [
                        {
                            "title": "Guest refund request at PT300-2M",
                            "portfolio_name": "Urban Stays",
                            "severity": "critical",
                            "action": "Send recovery note and decide refund handling.",
                        }
                    ],
                }
            ],
        )

        self.assertIn("Daily Operating Snapshot:", body)
        self.assertLess(body.index("Daily Operating Snapshot:"), body.index("Needs Action Today:"))
        self.assertIn(
            "What needs action today?: Guest refund request at PT300-2M is the highest-priority action right now. [Urban Stays] (new)",
            body,
        )

    def test_whatsapp_memory_highlight_becomes_brief_item(self):
        item = whatsapp_memory_highlight_brief_item(
            {
                "portfolio_id": 3,
                "portfolio_name": "Urban Stays",
                "group_name": "PT300 Management",
                "status": "watch",
                "attention_score": 211.6,
                "focus": "Recent likely open topics: vendor follow-up.",
                "suggested_action": "Review PT300 Management in WhatsApp.",
                "last_message_at": "2026-06-29T01:00:00",
            }
        )

        self.assertEqual(item["kind"], "whatsapp_memory")
        self.assertEqual(item["severity"], "high")
        self.assertEqual(item["portfolio_name"], "Urban Stays")
        self.assertIn("PT300 Management", item["title"])
        self.assertIn("vendor follow-up", item["summary"])

    def test_today_operating_snapshot_answers_core_team_questions(self):
        review_signal = {
            "signal_id": 10,
            "category": "review_risk",
            "title": "Guest refund request at PT300-2M",
            "summary": "Guest is asking for a refund after unresolved stay issue.",
            "suggested_action": "Send recovery note and decide refund handling.",
            "severity": "critical",
            "status": "new",
            "rank_score": 98,
            "listing_name": "PT300-2M",
            "portfolio_name": "Urban Stays",
        }
        revenue_signal = {
            "signal_id": 11,
            "category": "revenue_booking_health",
            "title": "Booking health needs action for Dew",
            "summary": "30-day pickup is weak.",
            "suggested_action": "Relax minimum stay and test a targeted discount.",
            "severity": "high",
            "status": "new",
            "rank_score": 91,
            "listing_name": "Dew",
            "portfolio_name": "Luminary Resorts",
        }
        snapshot = build_today_operating_snapshot(
            top_signals=[review_signal, revenue_signal],
            priority_actions=[review_signal, revenue_signal],
            memory_highlights=[
                {
                    "whatsapp_group_memory_id": 7,
                    "group_name": "PT300 Management",
                    "portfolio_name": "Urban Stays",
                    "focus": "Recent likely open topics: refund follow-up.",
                    "suggested_action": "Review PT300 Management in WhatsApp.",
                    "status": "watch",
                    "attention_score": 180,
                }
            ],
            open_loops=[
                {
                    "kind": "signal",
                    "signal_id": 12,
                    "title": "Open loop in Luminary Cleaning",
                    "summary": "Cleaner confirmation is still pending.",
                    "suggested_next_step": "Ask cleaner to confirm completion.",
                    "severity": "high",
                    "status": "open",
                    "rank_score": 88,
                    "portfolio_name": "Luminary Resorts",
                }
            ],
            booking_health_warnings=[
                {
                    "listing_id": 99,
                    "listing_name": "Dew",
                    "portfolio_name": "Luminary Resorts",
                    "severity": "high",
                    "booking_pattern": "Forward pickup is thin.",
                    "action_items": ["Review minimum-stay rules."],
                    "confidence": 0.81,
                }
            ],
        )

        self.assertEqual([item["key"] for item in snapshot], [
            "missed",
            "action_today",
            "guest_review_risk",
            "unresolved_loop",
            "booking_momentum",
            "highest_roi",
        ])
        self.assertIn("PT300 Management", snapshot[0]["title"])
        self.assertEqual(snapshot[1]["source_id"], 10)
        self.assertEqual(snapshot[2]["source_id"], 10)
        self.assertEqual(snapshot[3]["source_id"], 12)
        self.assertEqual(snapshot[4]["listing_name"], "Dew")
        self.assertEqual(snapshot[5]["source_type"], "revenue_signal")
        self.assertTrue(all(item.get("answer") for item in snapshot))
        self.assertIn("highest-priority action", snapshot[1]["answer"])
        self.assertEqual(snapshot[1]["answer"], "PT300-2M is the highest-priority action right now.")
        self.assertEqual(snapshot[2]["answer"], "PT300-2M is the guest/review risk to watch right now.")
        self.assertIn("highest-ROI", snapshot[5]["answer"])

    def test_today_operating_snapshot_uses_distinct_guest_risk_when_available(self):
        action_signal = {
            "signal_id": 10,
            "category": "guest_experience",
            "title": "PT300-20M needs refund handling",
            "summary": "Guest left after a same-stay issue.",
            "suggested_action": "Confirm refund completion.",
            "severity": "critical",
            "status": "new",
            "rank_score": 100,
            "listing_name": "PT300-20M",
            "portfolio_name": "Urban Stays",
        }
        alternate_review_risk = {
            "signal_id": 11,
            "category": "review_risk",
            "title": "PT300-18C guest sentiment is at risk",
            "summary": "Guest had a repeated comfort complaint.",
            "suggested_action": "Send a recovery note before checkout.",
            "severity": "critical",
            "status": "new",
            "rank_score": 98,
            "listing_name": "PT300-18C",
            "portfolio_name": "Urban Stays",
        }

        snapshot = build_today_operating_snapshot(
            top_signals=[action_signal, alternate_review_risk],
            priority_actions=[action_signal, alternate_review_risk],
            memory_highlights=[],
            open_loops=[],
            booking_health_warnings=[],
        )

        self.assertEqual(snapshot[1]["source_id"], 10)
        self.assertEqual(snapshot[2]["source_id"], 11)
        self.assertEqual(snapshot[2]["answer"], "PT300-18C is the guest/review risk to watch right now.")

    def test_today_operating_snapshot_prefers_fresh_unresolved_loop(self):
        old_high_loop = {
            "kind": "signal",
            "signal_id": 313,
            "title": "Open loop in Emerald Haven: plumbing or water issue",
            "summary": "A water-filter follow-up is still open.",
            "suggested_next_step": "Confirm whether the water filter was ordered.",
            "severity": "high",
            "status": "open",
            "rank_score": 98.6,
            "portfolio_name": "Enchanted Havens",
            "listing_name": "Emerald Haven",
            "last_activity_at": "2026-06-26T18:12:38",
            "last_seen_at": "2026-06-29T03:08:50",
            "latest_evidence_at": "2026-06-26T18:12:38",
        }
        fresh_medium_loop = {
            "kind": "signal",
            "signal_id": 297,
            "title": "Possible plumbing blockage in Crestwood room 9 needs follow-up",
            "summary": "The team promised an update after trying to unclog the item.",
            "suggested_next_step": "Confirm whether room 9 is now guest-ready.",
            "severity": "medium",
            "status": "new",
            "rank_score": 72.2,
            "portfolio_name": "crestwood",
            "listing_name": "Crestwood",
            "last_activity_at": "2026-06-28T22:18:10",
            "last_seen_at": "2026-06-29T01:41:36",
            "latest_evidence_at": "2026-06-28T22:18:10",
        }

        snapshot = build_today_operating_snapshot(
            top_signals=[],
            priority_actions=[],
            memory_highlights=[],
            open_loops=[old_high_loop, fresh_medium_loop],
            booking_health_warnings=[],
        )

        self.assertEqual(snapshot[3]["source_id"], 297)
        self.assertIn("Crestwood room 9", snapshot[3]["title"])

    def test_today_operating_snapshot_prefers_fresh_active_guest_risk_over_stale_memory(self):
        action_signal = {
            "signal_id": 10,
            "category": "guest_experience",
            "title": "PT300-20M needs refund handling",
            "summary": "Guest left after a same-stay issue.",
            "suggested_action": "Confirm refund completion.",
            "severity": "critical",
            "status": "new",
            "rank_score": 100,
            "listing_name": "PT300-20M",
            "portfolio_name": "Urban Stays",
        }
        stale_memory = {
            "signal_id": 11,
            "category": "review_risk",
            "source": "deterministic_guest_stay_memory",
            "title": "Old review risk at PT300-18C",
            "summary": "A past guest already left a bad review.",
            "suggested_action": "Review historical root cause.",
            "severity": "critical",
            "status": "new",
            "rank_score": 98,
            "listing_name": "PT300-18C",
            "portfolio_name": "Urban Stays",
            "evidence": [{"source_type": "guest_stay_memory", "occurred_at": "2026-05-13T21:02:40"}],
        }
        fresh_active_risk = {
            "signal_id": 12,
            "category": "review_risk",
            "source": "openai_structured",
            "title": "PT300-19M active arrival issue could become a bad review",
            "summary": "Guest is currently having arrival friction after a recent poor review.",
            "suggested_action": "Monitor the current stay and send proactive recovery outreach.",
            "severity": "high",
            "status": "new",
            "rank_score": 98.5,
            "listing_name": "PT300-19M",
            "portfolio_name": "Urban Stays",
            "evidence": [{"source_type": "hostaway_message", "occurred_at": datetime.utcnow().isoformat()}],
        }

        snapshot = build_today_operating_snapshot(
            active_signals=[action_signal, stale_memory, fresh_active_risk],
            top_signals=[action_signal, stale_memory],
            priority_actions=[action_signal, stale_memory],
            memory_highlights=[],
            open_loops=[],
            booking_health_warnings=[],
        )

        self.assertEqual(snapshot[2]["source_id"], 12)
        self.assertEqual(snapshot[2]["answer"], "PT300-19M is the guest/review risk to watch right now.")

    def test_today_operating_snapshot_uses_distinct_roi_listing_when_available(self):
        top_revenue_signal = {
            "signal_id": 20,
            "category": "revenue_booking_health",
            "title": "Booking health needs action for PT300-10H",
            "summary": "PT300-10H is not picking up.",
            "suggested_action": "Verify listing status.",
            "severity": "high",
            "status": "new",
            "rank_score": 95.6,
            "listing_id": 10,
            "listing_name": "PT300-10H",
            "portfolio_name": "Urban Stays",
        }
        alternate_revenue_signal = {
            "signal_id": 21,
            "category": "revenue_booking_health",
            "title": "Booking health needs action for PT300-18C",
            "summary": "PT300-18C has weak pickup.",
            "suggested_action": "Review restrictions and pricing.",
            "severity": "high",
            "status": "new",
            "rank_score": 95.5,
            "listing_id": 18,
            "listing_name": "PT300-18C",
            "portfolio_name": "Urban Stays",
        }

        snapshot = build_today_operating_snapshot(
            top_signals=[top_revenue_signal, alternate_revenue_signal],
            priority_actions=[top_revenue_signal, alternate_revenue_signal],
            memory_highlights=[],
            open_loops=[],
            booking_health_warnings=[
                {
                    "listing_id": 10,
                    "listing_name": "PT300-10H",
                    "portfolio_name": "Urban Stays",
                    "severity": "high",
                    "booking_pattern": "Forward pickup is thin.",
                    "action_items": ["Verify listing status."],
                    "confidence": 0.95,
                }
            ],
        )

        self.assertEqual(snapshot[4]["listing_name"], "PT300-10H")
        self.assertEqual(snapshot[5]["source_id"], 21)
        self.assertEqual(snapshot[5]["listing_name"], "PT300-18C")

    def test_today_operating_snapshot_returns_empty_answers_without_noise(self):
        snapshot = build_today_operating_snapshot(
            top_signals=[],
            priority_actions=[],
            memory_highlights=[],
            open_loops=[],
            booking_health_warnings=[],
        )

        self.assertEqual(len(snapshot), 6)
        self.assertTrue(all(item["status"] == "ok" for item in snapshot))
        self.assertTrue(all(item.get("answer") for item in snapshot))
        self.assertIn("No urgent action", snapshot[1]["title"])

    def test_operating_brief_body_accepts_signal_backed_open_loop_items(self):
        body = format_operating_brief_body(
            subject="STR Signal Brain - Daily Brief",
            generated_date=date(2026, 6, 29),
            counts={
                "active_signals": 9,
                "guest_review_risks": 2,
                "open_loops": 4,
                "booking_roi": 2,
            },
            sections=[
                {
                    "title": "Operational Open Loops",
                    "items": [
                        {
                            "kind": "signal",
                            "title": "Active plumbing/clog issue in Crestwood room 9",
                            "portfolio_name": "crestwood",
                            "severity": "high",
                            "action": "Confirm whether room 9 is guest-ready.",
                        }
                    ],
                },
            ],
        )

        self.assertIn("Open loops: 4", body)
        self.assertIn("Active plumbing/clog issue in Crestwood room 9 [crestwood] (high)", body)

    def test_nightly_run_does_not_generate_operator_briefs(self):
        self.assertFalse(should_generate_briefs_for_run_type("nightly"))
        self.assertTrue(should_generate_briefs_for_run_type("morning"))
        self.assertTrue(should_generate_briefs_for_run_type("afternoon"))
        self.assertTrue(should_generate_briefs_for_run_type("manual"))

    def test_portfolio_brief_includes_booking_health(self):
        signal = SimpleNamespace(
            owner_or_manager="operator",
            category="revenue_booking_health",
        )

        self.assertTrue(signal_visible_to_portfolio_brief(signal))
        self.assertFalse(is_decision_signal(signal))

    def test_portfolio_brief_includes_decisions_and_approvals(self):
        signal = SimpleNamespace(
            owner_or_manager="operator",
            category="owner_decision",
        )

        self.assertTrue(signal_visible_to_portfolio_brief(signal))
        self.assertTrue(is_decision_signal(signal))

    def test_portfolio_brief_includes_operational_signals(self):
        signal = SimpleNamespace(
            owner_or_manager="operator",
            category="operational_open_loop",
        )

        self.assertTrue(signal_visible_to_portfolio_brief(signal))
        self.assertFalse(is_decision_signal(signal))

    def test_operating_signal_selection_balances_categories(self):
        signals = [
            SimpleNamespace(category="revenue_booking_health", severity="high", rank_score=100, title=f"booking {idx}")
            for idx in range(6)
        ] + [
            SimpleNamespace(category="review_risk", severity="high", rank_score=99, title="review risk"),
            SimpleNamespace(category="operational_open_loop", severity="high", rank_score=98, title="open loop"),
        ]

        selected = select_operating_signals(signals, limit=5, per_category_limit=2)
        categories = [signal.category for signal in selected]

        self.assertLessEqual(categories.count("revenue_booking_health"), 2)
        self.assertIn("review_risk", categories)
        self.assertIn("operational_open_loop", categories)

    def test_open_loop_item_sort_prefers_high_signal_rows_before_medium_table_rows(self):
        items = [
            {
                "kind": "open_loop",
                "title": "Existing table loop",
                "status": "open",
                "severity": "medium",
                "rank_score": 56,
                "last_seen_at": "2026-06-29T01:00:00",
            },
            {
                "kind": "signal",
                "title": "Active plumbing/clog issue",
                "status": "new",
                "severity": "high",
                "rank_score": 100,
                "last_seen_at": "2026-06-29T00:30:00",
            },
        ]

        ordered = sorted(items, key=open_loop_item_sort_key)

        self.assertEqual(ordered[0]["title"], "Active plumbing/clog issue")

    def test_ai_structured_rank_score_spreads_high_and_medium_signals(self):
        urgent_checkin = ai_structured_signal_rank_score(
            {
                "category": "guest_check_in",
                "severity": "high",
                "confidence": 0.98,
                "title": "Front-desk check-in failure left guest waiting in lobby",
                "why_it_matters": "The guest is stranded on-site and may request a refund.",
            },
            [SimpleNamespace(source_type="hostaway_message")],
            "checkin_checkout_risk",
        )
        possible_camera = ai_structured_signal_rank_score(
            {
                "category": "security",
                "severity": "medium",
                "confidence": 0.83,
                "title": "Possible fallen camera reported in Luminary Resorts portfolio",
            },
            [SimpleNamespace(source_type="whatsapp_message")],
            "operational_open_loop",
        )

        self.assertGreater(urgent_checkin, possible_camera)
        self.assertLess(urgent_checkin, 100)

    def test_guest_message_risk_rank_score_prioritizes_refund_without_saturating(self):
        refund = guest_message_risk_rank_score("The host asked me to contact you for a full refund.")
        remote = guest_message_risk_rank_score("The remotes are not working.")

        self.assertGreater(refund, remote)
        self.assertLess(refund, 100)

    def test_ai_booking_health_candidate_is_recategorized_and_skipped_when_duplicate(self):
        candidate = {
            "category": "operational_open_loop",
            "title": "Sol shows weak booking pace across 7, 30, and 60 days",
            "summary": "Booking pace is below target.",
        }
        evidence = [SimpleNamespace(source_type="booking_health")]

        category = ai_candidate_category(candidate, evidence)

        self.assertEqual(category, "revenue_booking_health")
        self.assertTrue(should_skip_ai_signal_candidate(candidate, evidence, category))

    def test_ai_booking_pace_category_stays_revenue_even_with_review_context(self):
        candidate = {
            "category": "booking_pace",
            "title": "PT300-20M has weak booking pace after recent low reviews",
            "summary": "Booking pace is below target and reviews may be hurting conversion.",
        }
        evidence = [
            SimpleNamespace(source_type="booking_health"),
            SimpleNamespace(source_type="hostaway_review"),
        ]

        category = ai_candidate_category(candidate, evidence)

        self.assertEqual(category, "revenue_booking_health")

    def test_ai_revenue_risk_category_stays_revenue_with_review_evidence(self):
        candidate = {
            "category": "revenue_risk",
            "title": "PT300-20M has weak booking pace alongside damaging recent reviews",
            "summary": "Weak pace paired with negative reviews suggests a conversion problem.",
        }
        evidence = [
            SimpleNamespace(source_type="booking_health"),
            SimpleNamespace(source_type="hostaway_review"),
        ]

        category = ai_candidate_category(candidate, evidence)

        self.assertEqual(category, "revenue_booking_health")

    def test_ai_mixed_revenue_candidate_is_kept_when_it_cites_operational_evidence(self):
        candidate = {
            "category": "operational_open_loop",
            "title": "Pricing restriction may be hurting booking pace",
        }
        evidence = [
            SimpleNamespace(source_type="booking_health"),
            SimpleNamespace(source_type="whatsapp_message"),
        ]

        category = ai_candidate_category(candidate, evidence)

        self.assertEqual(category, "revenue_booking_health")
        self.assertFalse(should_skip_ai_signal_candidate(candidate, evidence, category))

    def test_ai_mixed_revenue_candidate_is_skipped_when_listing_has_deterministic_booking_signal(self):
        candidate = {
            "category": "revenue_booking_health",
            "title": "PT300-20M weak booking pace is tied to trust issues",
        }
        evidence = [
            SimpleNamespace(source_type="booking_health"),
            SimpleNamespace(source_type="whatsapp_message"),
        ]

        self.assertTrue(
            should_skip_ai_signal_candidate(
                candidate,
                evidence,
                "revenue_booking_health",
                listing_id=295677,
                deterministic_revenue_listing_ids={295677},
            )
        )

    def test_ai_review_risk_candidate_is_recategorized(self):
        candidate = {
            "category": "operational_open_loop",
            "title": "PT300 has compounding quality risk after recent low reviews",
        }

        self.assertEqual(ai_candidate_category(candidate, []), "review_risk")

    def test_ai_checkin_alias_stays_checkin_even_with_review_consequence(self):
        candidate = {
            "category": "check_in_issue",
            "title": "Active arrival friction at PT300-18C due to front desk system mismatch",
            "why_it_matters": "This live check-in failure can quickly turn into a poor review.",
        }
        evidence = [SimpleNamespace(source_type="hostaway_message")]

        self.assertEqual(ai_candidate_category(candidate, evidence), "checkin_checkout_risk")

    def test_ai_guest_checkin_alias_stays_checkin(self):
        candidate = {
            "category": "guest_check_in",
            "title": "Front desk check-in failure at PT300-18C requires follow-up",
            "why_it_matters": "The guest is stranded on-site and may request a refund.",
        }
        evidence = [SimpleNamespace(source_type="hostaway_message")]

        self.assertEqual(ai_candidate_category(candidate, evidence), "checkin_checkout_risk")

    def test_ai_explicit_maintenance_candidate_stays_open_loop_despite_review_consequence(self):
        candidate = {
            "category": "maintenance",
            "title": "Active plumbing/clog issue in Crestwood portfolio may need escalation",
            "summary": "The cleaning team reported they were trying to unclog something.",
            "why_it_matters": "If unresolved before guest use, a clog can quickly trigger bad reviews.",
        }
        evidence = [
            SimpleNamespace(source_type="whatsapp_message"),
            SimpleNamespace(source_type="whatsapp_message"),
        ]

        self.assertEqual(ai_candidate_category(candidate, evidence), "operational_open_loop")

    def test_ai_maintenance_candidate_with_review_evidence_becomes_review_risk(self):
        candidate = {
            "category": "maintenance",
            "title": "Reflection Haven has repeated hot tub failure evidence affecting amenity reliability",
            "summary": "Multiple recent reviews reported the hot tub was not working.",
        }
        evidence = [
            SimpleNamespace(source_type="hostaway_review"),
            SimpleNamespace(source_type="hostaway_review"),
        ]

        self.assertEqual(ai_candidate_category(candidate, evidence), "review_risk")

    def test_review_risk_evidence_prefers_reviews_before_booking_health(self):
        rows = [
            SimpleNamespace(source_type="booking_health", evidence_id=1, occurred_at=None),
            SimpleNamespace(source_type="hostaway_review", evidence_id=2, occurred_at=None),
            SimpleNamespace(source_type="hostaway_message", evidence_id=3, occurred_at=None),
        ]

        ordered = sorted(rows, key=lambda row: signal_evidence_sort_key("review_risk", row))

        self.assertEqual(
            [row.source_type for row in ordered],
            ["hostaway_review", "hostaway_message", "booking_health"],
        )

    def test_revenue_evidence_prefers_booking_analysis_before_reviews(self):
        rows = [
            SimpleNamespace(source_type="hostaway_review", evidence_id=1, occurred_at=None),
            SimpleNamespace(source_type="booking_health", evidence_id=2, occurred_at=None),
            SimpleNamespace(source_type="booking_health_analysis", evidence_id=3, occurred_at=None),
        ]

        ordered = sorted(rows, key=lambda row: signal_evidence_sort_key("revenue_booking_health", row))

        self.assertEqual(
            [row.source_type for row in ordered],
            ["booking_health_analysis", "booking_health", "hostaway_review"],
        )

    def test_operating_signal_selection_prefers_listing_diversity(self):
        signals = [
            SimpleNamespace(category="guest_experience", severity="critical", rank_score=100, listing_id=1, reservation_id=10, title="guest issue"),
            SimpleNamespace(category="review_risk", severity="high", rank_score=100, listing_id=1, reservation_id=10, title="same stay review risk"),
            SimpleNamespace(category="review_risk", severity="high", rank_score=90, listing_id=2, reservation_id=20, title="different review risk"),
            SimpleNamespace(category="operational_open_loop", severity="high", rank_score=80, listing_id=3, reservation_id=None, title="ops loop"),
        ]

        selected = select_operating_signals(signals, limit=3, per_category_limit=2)
        titles = [signal.title for signal in selected]

        self.assertIn("guest issue", titles)
        self.assertIn("different review risk", titles)
        self.assertNotIn("same stay review risk", titles)


if __name__ == "__main__":
    unittest.main()
