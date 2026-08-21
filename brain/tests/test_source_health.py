import json
import os
import tempfile
from datetime import datetime
from types import SimpleNamespace
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from brain.services import (
    canonical_whatsapp_thread_rows,
    load_whatsapp_bridge_state,
    load_whatsapp_group_profiles,
    summarize_brief_delivery,
    summarize_pricelabs_health,
    summarize_scheduled_read_health,
    summarize_today_readiness,
    summarize_whatsapp_ingestion,
    whatsapp_source_group_health,
    whatsapp_thread_memory_key,
)


class SourceHealthTests(unittest.TestCase):
    def test_scheduled_read_health_accepts_recent_morning_or_afternoon_completion(self):
        health = summarize_scheduled_read_health(
            [
                {
                    "run_type": "morning",
                    "status": "completed",
                    "signal_run_id": 10,
                    "completed_at": "2026-06-28T14:00:00",
                    "brief_count": 1,
                },
                {
                    "run_type": "afternoon",
                    "status": "completed",
                    "signal_run_id": 11,
                    "completed_at": "2026-06-28T21:00:00",
                    "brief_count": 1,
                },
                {
                    "run_type": "nightly",
                    "status": "completed",
                    "signal_run_id": 12,
                    "completed_at": "2026-06-29T10:30:00",
                    "brief_count": 0,
                },
            ],
            now=datetime(2026, 6, 29, 1, 0, 0),
        )

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["latest_read_completed_at"], "2026-06-28T21:00:00")
        self.assertEqual(health["reads"][2]["brief_count"], 0)

    def test_scheduled_read_health_marks_missing_required_read(self):
        health = summarize_scheduled_read_health(
            [{"run_type": "morning", "status": "completed", "completed_at": "2026-06-28T14:00:00"}],
            now=datetime(2026, 6, 29, 1, 0, 0),
        )

        self.assertEqual(health["status"], "missing")
        self.assertIn("afternoon", health["message"])

    def test_brief_delivery_health_treats_whatsapp_queued_as_ok(self):
        brief = type("Brief", (), {"status": "sent", "generated_at": datetime(2026, 6, 28, 21, 1, 0)})()
        email_log = type(
            "Log",
            (),
            {
                "channel": "email",
                "status": "skipped",
                "recipient": "ops@example.com",
                "provider": "smtp",
                "attempted_at": datetime(2026, 6, 28, 21, 1, 1),
                "error_message": "SMTP not configured",
            },
        )()
        whatsapp_log = type(
            "Log",
            (),
            {
                "channel": "whatsapp",
                "status": "queued",
                "recipient": "+17709255856",
                "provider": "twilio",
                "attempted_at": datetime(2026, 6, 28, 21, 1, 2),
                "error_message": None,
            },
        )()
        run = type("Run", (), {"signal_run_id": 7, "run_type": "afternoon"})()

        health = summarize_brief_delivery([brief], [email_log, whatsapp_log], run=run)

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["signal_run_id"], 7)
        self.assertEqual(health["channel_status_counts"]["whatsapp"]["queued"], 1)

    def test_brief_delivery_health_marks_brief_error_degraded(self):
        brief = type("Brief", (), {"status": "error", "generated_at": datetime(2026, 6, 28, 10, 30, 0)})()

        health = summarize_brief_delivery([brief], [], run=type("Run", (), {"signal_run_id": 5, "run_type": "nightly"})())

        self.assertEqual(health["status"], "degraded")

    def test_pricelabs_health_requires_fresh_complete_snapshots(self):
        rows = [
            SimpleNamespace(status="ok", listing_id=1, created_at=datetime(2026, 6, 29, 1, 0, 0), snapshot_date="2026-06-29"),
            SimpleNamespace(status="ok", listing_id=2, created_at=datetime(2026, 6, 29, 1, 1, 0), snapshot_date="2026-06-29"),
        ]

        health = summarize_pricelabs_health(
            rows,
            run_id=44,
            expected_snapshot_count=2,
            configured=True,
            base_url="https://api.pricelabs.co/v1",
            now=datetime(2026, 6, 29, 2, 0, 0),
        )

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["snapshot_count"], 2)
        self.assertEqual(health["expected_snapshot_count"], 2)
        self.assertLess(health["snapshot_age_hours"], 2)

    def test_pricelabs_health_marks_stale_snapshots(self):
        rows = [
            SimpleNamespace(status="ok", listing_id=1, created_at=datetime(2026, 6, 27, 1, 0, 0), snapshot_date="2026-06-27"),
        ]

        health = summarize_pricelabs_health(
            rows,
            expected_snapshot_count=1,
            now=datetime(2026, 6, 29, 2, 0, 0),
        )

        self.assertEqual(health["status"], "stale")
        self.assertIn("older than 36 hours", health["message"])

    def test_pricelabs_health_marks_incomplete_snapshots_degraded(self):
        rows = [
            SimpleNamespace(status="ok", listing_id=1, created_at=datetime(2026, 6, 29, 1, 0, 0), snapshot_date="2026-06-29"),
        ]

        health = summarize_pricelabs_health(
            rows,
            expected_snapshot_count=2,
            now=datetime(2026, 6, 29, 2, 0, 0),
        )

        self.assertEqual(health["status"], "degraded")
        self.assertIn("missing", health["message"])

    def test_today_readiness_ok_when_reads_delivery_and_sources_are_ok(self):
        readiness = summarize_today_readiness(
            scheduled_reads={"status": "ok", "latest_read_completed_at": "2026-06-28T21:00:00"},
            brief_delivery={"status": "ok", "channel_status_counts": {"whatsapp": {"queued": 1}}},
            hostaway={"status": "ok", "active_listing_count": 36, "mapped_listing_count": 32, "booking_analysis_count": 32},
            pricelabs={"status": "ok", "snapshot_count": 32, "expected_snapshot_count": 32, "latest_snapshot_at": "2026-06-28T21:00:00"},
            whatsapp={"status": "ok", "configured_group_count": 21, "synced_group_count": 21},
        )

        self.assertEqual(readiness["status"], "ok")
        self.assertEqual(readiness["components"][0]["key"], "scheduled_reads")
        self.assertEqual(readiness["components"][2]["key"], "hostaway")
        self.assertIn("32/32 snapshots", readiness["components"][3]["detail"])
        self.assertIn("32/36 mapped listings", readiness["components"][2]["detail"])
        self.assertIn("healthy", readiness["message"])

    def test_today_readiness_treats_whatsapp_history_limit_as_informational(self):
        readiness = summarize_today_readiness(
            scheduled_reads={"status": "ok", "latest_read_completed_at": "2026-06-28T21:00:00"},
            brief_delivery={"status": "ok", "channel_status_counts": {"whatsapp": {"queued": 1}}},
            hostaway={"status": "ok", "active_listing_count": 36, "mapped_listing_count": 32, "booking_analysis_count": 32},
            pricelabs={"status": "ok", "snapshot_count": 32},
            whatsapp={
                "status": "history_limited",
                "configured_group_count": 21,
                "synced_group_count": 20,
                "history_limited_group_count": 5,
                "ongoing_ingestion": {"active_group_count_24h": 12},
            },
        )

        self.assertEqual(readiness["status"], "ok")
        self.assertIn("healthy", readiness["message"])
        self.assertIn("12 active in 24h", readiness["components"][4]["detail"])
        self.assertIn("5 older-history partial, informational", readiness["components"][4]["detail"])

    def test_today_readiness_marks_hostaway_degraded_when_artifacts_missing(self):
        readiness = summarize_today_readiness(
            scheduled_reads={"status": "ok", "latest_read_completed_at": "2026-06-28T21:00:00"},
            brief_delivery={"status": "ok", "channel_status_counts": {"whatsapp": {"queued": 1}}},
            hostaway={"status": "degraded", "message": "Hostaway run is missing calendar snapshots."},
            pricelabs={"status": "ok", "snapshot_count": 32},
            whatsapp={"status": "ok", "configured_group_count": 21, "synced_group_count": 21},
        )

        self.assertEqual(readiness["status"], "degraded")
        self.assertIn("Hostaway", readiness["message"])

    def test_whatsapp_ingestion_is_ok_with_recent_batch_and_messages(self):
        health = summarize_whatsapp_ingestion(
            [
                {
                    "display_name": "PT300 Management",
                    "message_count_24h": 15,
                    "account_sent_count_24h": 3,
                    "last_received_at": "2026-06-29T00:30:00",
                },
                {
                    "display_name": "Luminary Resorts Management",
                    "message_count_24h": 4,
                    "account_sent_count_24h": 1,
                    "last_received_at": "2026-06-28T23:45:00",
                },
            ],
            {"last_backfill": {"completed_at": "2026-06-29T00:40:00"}},
            now=datetime(2026, 6, 29, 1, 0, 0),
        )

        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["message_count_24h"], 19)
        self.assertEqual(health["account_sent_count_24h"], 4)
        self.assertEqual(health["active_group_count_24h"], 2)

    def test_whatsapp_ingestion_is_stale_when_batch_stops(self):
        health = summarize_whatsapp_ingestion(
            [
                {
                    "display_name": "PT300 Management",
                    "message_count_24h": 0,
                    "last_received_at": "2026-06-27T08:00:00",
                }
            ],
            {"last_backfill": {"completed_at": "2026-06-27T08:30:00"}},
            now=datetime(2026, 6, 29, 1, 0, 0),
        )

        self.assertEqual(health["status"], "stale")
        self.assertIn("batch reader", health["message"])

    def test_whatsapp_ingestion_accepts_current_bridge_heartbeat_without_recent_messages(self):
        health = summarize_whatsapp_ingestion(
            [
                {
                    "display_name": "PT300 Management",
                    "message_count_24h": 0,
                    "last_received_at": "2026-06-27T08:00:00",
                }
            ],
            {
                "last_backfill": {"completed_at": "2026-06-29T00:40:00"},
                "last_ready_at": "2026-06-29T00:55:00",
            },
            now=datetime(2026, 6, 29, 1, 0, 0),
        )

        self.assertEqual(health["status"], "ok")
        self.assertIn("reader is current", health["message"])
        self.assertEqual(health["bridge_ready_at"], "2026-06-29T00:55:00")

    def test_load_whatsapp_group_profiles_reads_allowlist(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(
                {
                    "groups": [
                        {
                            "name": "PT300 Management",
                            "aliases": ["PT300 management"],
                            "portfolio": "Urban Stays",
                            "kind": "management",
                        }
                    ]
                },
                handle,
            )
            handle.flush()

            groups = load_whatsapp_group_profiles(handle.name)

        self.assertEqual(groups[0]["name"], "PT300 Management")
        self.assertEqual(groups[0]["aliases"], ["PT300 management"])
        self.assertEqual(groups[0]["portfolio"], "Urban Stays")

    def test_canonical_whatsapp_thread_rows_merge_alias_threads(self):
        groups = [
            {
                "name": "Crockett's Run Management",
                "aliases": ["Crockett Run Management"],
                "portfolio": "Middlefork",
                "kind": "management",
            }
        ]
        rows = [
            {
                "display_name": "Crockett Run Management",
                "provider_thread_id": "alias-thread",
                "portfolio_name": None,
                "message_count_60d": 1,
                "message_count_7d": 1,
                "message_count_24h": 1,
                "account_sent_count_24h": 1,
                "first_received_at": "2026-06-28T23:02:59",
                "last_received_at": "2026-06-28T23:02:59",
            },
            {
                "display_name": "Crockett's Run Management",
                "provider_thread_id": "canonical-thread",
                "portfolio_name": "Middlefork",
                "message_count_60d": 3,
                "message_count_7d": 3,
                "message_count_24h": 3,
                "account_sent_count_24h": 2,
                "first_received_at": "2026-06-28T23:47:41",
                "last_received_at": "2026-06-28T23:49:10",
            },
        ]

        canonical = canonical_whatsapp_thread_rows(groups, rows)

        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["display_name"], "Crockett's Run Management")
        self.assertEqual(canonical[0]["portfolio_name"], "Middlefork")
        self.assertEqual(canonical[0]["message_count_60d"], 4)
        self.assertEqual(canonical[0]["message_count_7d"], 4)
        self.assertEqual(canonical[0]["message_count_24h"], 4)
        self.assertEqual(canonical[0]["account_sent_count_24h"], 3)
        self.assertEqual(canonical[0]["first_received_at"], "2026-06-28T23:02:59")
        self.assertEqual(canonical[0]["last_received_at"], "2026-06-28T23:49:10")
        self.assertIn("Crockett Run Management", canonical[0]["matched_thread"])

    def test_whatsapp_thread_memory_key_collapses_group_aliases(self):
        groups = [
            {
                "name": "Crockett's Run Management",
                "aliases": ["Crockett Run Management"],
                "portfolio": "Middlefork",
                "kind": "management",
            }
        ]
        canonical = SimpleNamespace(display_name="Crockett's Run Management", provider="whatsapp_web", provider_thread_id="canonical")
        alias = SimpleNamespace(display_name="Crockett Run Management", provider="whatsapp_web", provider_thread_id="alias")

        self.assertEqual(whatsapp_thread_memory_key(canonical, groups), whatsapp_thread_memory_key(alias, groups))

    def test_load_whatsapp_bridge_state_uses_newest_backfill(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(
                {
                    "last_ready_at": "2026-06-29T00:55:00Z",
                    "last_live_forwarded_at": "2026-06-29T00:56:00Z",
                    "last_backfills": [
                        {"reason": "oldest", "started_at": "2026-06-28T20:00:00Z"},
                        {"reason": "newest", "started_at": "2026-06-28T22:00:00Z"},
                    ]
                },
                handle,
            )
            handle.flush()

            state = load_whatsapp_bridge_state(handle.name)

        self.assertEqual(state["last_backfill"]["reason"], "newest")
        self.assertEqual(state["last_ready_at"], "2026-06-29T00:55:00Z")
        self.assertEqual(state["last_live_forwarded_at"], "2026-06-29T00:56:00Z")

    def test_load_whatsapp_bridge_state_preserves_60_day_coverage_after_short_catchup(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(
                {
                    "last_backfills": [
                        {
                            "reason": "scheduled-catchup",
                            "started_at": "2026-06-29T13:35:00Z",
                            "lookback_days": 7,
                            "groups": {
                                "PT300 Management": {
                                    "fetched": 100,
                                    "within_lookback": 100,
                                    "reached_cutoff": True,
                                },
                            },
                        },
                        {
                            "reason": "initial-history-sync",
                            "started_at": "2026-06-28T22:00:00Z",
                            "lookback_days": 60,
                            "groups": {
                                "PT300 Management": {
                                    "fetched": 1200,
                                    "within_lookback": 900,
                                    "oldest_fetched_at": "2026-04-20T12:00:00Z",
                                    "reached_cutoff": True,
                                },
                            },
                        },
                    ]
                },
                handle,
            )
            handle.flush()

            state = load_whatsapp_bridge_state(handle.name)

        self.assertEqual(state["last_backfill"]["reason"], "scheduled-catchup")
        self.assertEqual(state["coverage_backfill"]["reason"], "merged-60-day-coverage")
        self.assertEqual(state["coverage_backfill"]["groups"]["PT300 Management"]["fetched"], 1200)

    def test_whatsapp_source_health_flags_missing_and_alias_matches(self):
        groups = [
            {"name": "PT300 Management", "aliases": ["PT300 management"], "portfolio": "Urban Stays", "kind": "management"},
            {"name": "Crockett's Run Management", "portfolio": "Middlefork", "kind": "management"},
        ]
        rows = [
            {
                "display_name": "PT300 management",
                "message_count_60d": 12,
                "message_count_7d": 3,
                "first_received_at": "2026-06-01T12:00:00",
                "last_received_at": "2026-06-28T12:00:00",
            }
        ]
        bridge_state = {
            "last_backfill": {
                "lookback_days": 60,
                "groups": {
                    "PT300 Management": {"fetched": 20, "stored": 12, "reached_cutoff": True},
                    "Crockett's Run Management": {"status": "not_found", "fetched": 0, "stored": 0},
                }
            }
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "ok")
        self.assertEqual(health[0]["matched_thread"], "PT300 management")
        self.assertEqual(health[1]["status"], "not_found")
        self.assertEqual(health[1]["backfill"]["fetched"], 0)

    def test_whatsapp_source_health_marks_current_partial_history_informational(self):
        groups = [
            {"name": "Luminary Resorts Management", "portfolio": "Luminary Resorts", "kind": "management"},
        ]
        rows = [
            {
                "display_name": "Luminary Resorts Management",
                "message_count_60d": 42,
                "message_count_7d": 18,
                "first_received_at": "2026-06-25T12:00:00",
                "last_received_at": "2026-06-28T12:00:00",
            }
        ]
        bridge_state = {
            "last_backfill": {
                "lookback_days": 60,
                "groups": {
                    "Luminary Resorts Management": {
                        "fetched": 500,
                        "within_lookback": 500,
                        "oldest_fetched_at": "2026-06-25T12:00:00",
                        "newest_fetched_at": "2026-06-28T12:00:00",
                        "reached_cutoff": False,
                        "exhausted": False,
                    },
                },
            }
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "history_limited")
        self.assertFalse(health[0]["backfill"]["reached_cutoff"])
        self.assertEqual(health[0]["backfill"]["oldest_fetched_at"], "2026-06-25T12:00:00")

    def test_whatsapp_source_health_marks_exhausted_partial_history_limited(self):
        groups = [
            {"name": "Crestwood Cleaning", "portfolio": "crestwood", "kind": "cleaning"},
        ]
        rows = [
            {
                "display_name": "Crestwood Cleaning",
                "message_count_60d": 326,
                "message_count_7d": 50,
                "first_received_at": "2026-06-13T18:04:41",
                "last_received_at": "2026-06-28T22:18:10",
            }
        ]
        bridge_state = {
            "coverage_backfill": {
                "lookback_days": 60,
                "groups": {
                    "Crestwood Cleaning": {
                        "fetched": 326,
                        "within_lookback": 326,
                        "oldest_fetched_at": "2026-06-13T18:04:41",
                        "newest_fetched_at": "2026-06-28T22:18:10",
                        "reached_cutoff": False,
                        "exhausted": True,
                        "maxed_out": False,
                    },
                },
            }
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "history_limited")
        self.assertIn("current messages", health[0]["status_reason"])
        self.assertIn("No urgent action", health[0]["recommended_action"])
        self.assertTrue(health[0]["backfill"]["exhausted"])

    def test_whatsapp_source_health_treats_short_backfill_as_informational_history_limit(self):
        groups = [
            {"name": "PT300 Management", "portfolio": "Urban Stays", "kind": "management"},
        ]
        rows = [
            {
                "display_name": "PT300 Management",
                "message_count_60d": 10,
                "message_count_7d": 10,
                "first_received_at": "2026-06-28T08:00:00",
                "last_received_at": "2026-06-28T12:00:00",
            }
        ]
        bridge_state = {
            "last_backfill": {
                "lookback_days": 1,
                "groups": {
                    "PT300 Management": {
                        "fetched": 100,
                        "within_lookback": 10,
                        "oldest_fetched_at": "2026-06-28T08:00:00",
                        "reached_cutoff": True,
                    },
                },
            }
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "history_limited")

    def test_whatsapp_source_health_uses_60_day_coverage_when_latest_backfill_is_short(self):
        groups = [
            {"name": "PT300 Management", "portfolio": "Urban Stays", "kind": "management"},
        ]
        rows = [
            {
                "display_name": "PT300 Management",
                "message_count_60d": 900,
                "message_count_7d": 100,
                "first_received_at": "2026-06-28T08:00:00",
                "last_received_at": "2026-06-28T12:00:00",
            }
        ]
        bridge_state = {
            "last_backfill": {
                "lookback_days": 7,
                "groups": {
                    "PT300 Management": {
                        "fetched": 100,
                        "within_lookback": 100,
                        "oldest_fetched_at": "2026-06-21T12:00:00",
                        "reached_cutoff": True,
                    },
                },
            },
            "coverage_backfill": {
                "lookback_days": 60,
                "groups": {
                    "PT300 Management": {
                        "fetched": 1200,
                        "within_lookback": 900,
                        "oldest_fetched_at": "2026-04-20T12:00:00",
                        "reached_cutoff": True,
                    },
                },
            },
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "ok")
        self.assertEqual(health[0]["backfill"]["lookback_days"], 60)

    def test_whatsapp_source_health_marks_alias_message_with_not_found_history_incomplete(self):
        groups = [
            {
                "name": "Crockett's Run Management",
                "aliases": ["Crockett Run Management"],
                "portfolio": "Middlefork",
                "kind": "management",
            },
        ]
        rows = [
            {
                "display_name": "Crockett Run Management",
                "message_count_60d": 1,
                "message_count_7d": 1,
                "first_received_at": "2026-06-28T12:00:00",
                "last_received_at": "2026-06-28T12:00:00",
            }
        ]
        bridge_state = {
            "coverage_backfill": {
                "lookback_days": 60,
                "groups": {
                    "Crockett's Run Management": {
                        "status": "not_found",
                        "fetched": 0,
                        "within_lookback": 0,
                        "reached_cutoff": False,
                    },
                },
            }
        }

        health = whatsapp_source_group_health(groups, rows, bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["matched_thread"], "Crockett Run Management")
        self.assertEqual(health[0]["status"], "history_limited")

    def test_whatsapp_source_health_treats_verified_empty_group_as_ok(self):
        groups = [
            {"name": "Grace Haven", "portfolio": "Enchanted Havens", "kind": "cleaning"},
        ]
        bridge_state = {
            "coverage_backfill": {
                "lookback_days": 60,
                "groups": {
                    "Grace Haven": {
                        "fetched": 1,
                        "within_lookback": 0,
                        "oldest_fetched_at": "2025-07-31T01:25:33",
                        "reached_cutoff": True,
                    },
                },
            }
        }

        health = whatsapp_source_group_health(groups, [], bridge_state, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "ok")
        self.assertTrue(health[0]["verified_empty"])
        self.assertIn("found no messages", health[0]["status_reason"])
        self.assertEqual(health[0]["message_count_60d"], 0)
        self.assertTrue(health[0]["backfill"]["reached_cutoff"])

    def test_whatsapp_source_health_recommends_action_for_missing_group(self):
        groups = [
            {"name": "Grace Haven", "portfolio": "Enchanted Havens", "kind": "cleaning"},
        ]

        health = whatsapp_source_group_health(groups, [], {"last_backfill": None}, now=datetime(2026, 6, 28, 22, 0, 0))

        self.assertEqual(health[0]["status"], "missing")
        self.assertFalse(health[0]["verified_empty"])
        self.assertIn("no stored messages", health[0]["status_reason"])
        self.assertIn("exact WhatsApp group name", health[0]["recommended_action"])


if __name__ == "__main__":
    unittest.main()
