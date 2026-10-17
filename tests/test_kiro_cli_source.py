"""Unit tests for Kiro CLI session parsing, normalization, credits, and token handling."""

import json
from pathlib import Path
import tempfile
import unittest

from ai_telemetry_agent.sources.kiro_cli_sessions import KiroCLISessionSource


class TestKiroCLISessionSource(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions_dir = Path(self.temp_dir.name) / "cli"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(self.temp_dir.name) / "kiro_cli_state.json"

        self.source = KiroCLISessionSource(
            sessions_dir=self.sessions_dir,
            state_file=self.state_file,
        )
        self.source.start()

    def tearDown(self):
        self.source.stop()
        self.temp_dir.cleanup()

    def test_parses_turn_with_credits_and_zero_tokens_mapped_to_none(self):
        """Zero token counts must be mapped to None, and meteringUsage mapped to credits."""
        session_id = "test-session-001"
        session_file = self.sessions_dir / f"{session_id}.json"

        raw_data = {
            "session_state": {
                "id": session_id,
                "created_at": "2026-10-14T05:00:00Z",
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-100",
                                    "user_turn_metadata": {
                                        "turn_duration_ms": 1420,
                                        "input_token_count": 0,
                                        "output_token_count": 0,
                                        "cache_read_input_token_count": 0,
                                        "cache_write_input_token_count": 0,
                                        "metering_usage": 0.0075,
                                        "context_usage_percentage": 0.05,
                                    }
                                }
                            },
                            "timestamp": "2026-10-14T05:00:02Z",
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(raw_data), encoding="utf-8")

        # Exclude detection event if present
        all_events = self.source.collect()
        events = [e for e in all_events if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)

        ev = events[0]
        payload = ev.get("payload", {})
        self.assertEqual(ev.get("context", {}).get("session_id"), session_id)
        self.assertEqual(payload.get("selected_model"), "claude-haiku-4.5")
        self.assertEqual(payload.get("model"), "claude-haiku-4.5")
        self.assertEqual(payload.get("credits"), 0.0075)
        self.assertEqual(payload.get("credit_unit"), "credit")
        self.assertEqual(payload.get("context_usage_percentage"), 0.05)
        self.assertIsNone(payload.get("cost_usd"))

        # Non-negotiable: zero tokens MUST be mapped to None, not 0
        self.assertIsNone(payload.get("input_tokens"))
        self.assertIsNone(payload.get("output_tokens"))
        self.assertIsNone(payload.get("cache_read_tokens"))
        self.assertIsNone(payload.get("total_tokens"))

        # Verify raw diagnostic presence
        raw_diag = payload.get("metadata", {}).get("raw_token_diagnostics")
        self.assertIsNotNone(raw_diag)
        self.assertEqual(raw_diag.get("raw_input_token_count"), 0)

    def test_auto_model_mapping(self):
        """When selected_model is 'auto', selected_model is 'auto' and resolved_model is None."""
        session_id = "test-session-auto"
        session_file = self.sessions_dir / f"{session_id}.json"

        raw_data = {
            "session_state": {
                "id": session_id,
                "created_at": "2026-10-14T05:10:00Z",
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-auto-1",
                                    "user_turn_metadata": {
                                        "turn_duration_ms": 900,
                                        "input_token_count": 0,
                                        "output_token_count": 0,
                                        "metering_usage": 0.002,
                                    }
                                }
                            },
                            "timestamp": "2026-10-14T05:10:01Z",
                            "selected_model": "auto",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(raw_data), encoding="utf-8")

        all_events = self.source.collect()
        events = [e for e in all_events if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        ev = events[0]
        payload = ev.get("payload", {})
        self.assertEqual(payload.get("selected_model"), "auto")
        self.assertIsNone(payload.get("resolved_model"))
        self.assertEqual(payload.get("model"), "auto")

    def test_deduplication_via_cursor(self):
        """Subsequent polls without new turns should produce 0 events."""
        session_id = "test-session-dedup"
        session_file = self.sessions_dir / f"{session_id}.json"

        raw_data = {
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-1",
                                    "user_turn_metadata": {"metering_usage": 0.01}
                                }
                            },
                            "timestamp": "2026-10-14T05:20:00Z",
                            "selected_model": "deepseek-3.2",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(raw_data), encoding="utf-8")

        all_events1 = self.source.collect()
        events1 = [e for e in all_events1 if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events1), 1)

        # Second poll: no new turns
        events2 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events2), 0)

        # Append turn 2
        raw_data["session_state"]["conversation_metadata"]["user_turn_metadatas"].append({
            "result": {
                "Ok": {
                    "id": "turn-2",
                    "user_turn_metadata": {"metering_usage": 0.015}
                }
            },
            "timestamp": "2026-10-14T05:21:00Z",
            "selected_model": "deepseek-3.2",
        })
        session_file.write_text(json.dumps(raw_data), encoding="utf-8")

        events3 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events3), 1)
        self.assertEqual(events3[0].get("payload", {}).get("credits"), 0.015)

    def test_corrupted_json_handled_resiliently(self):
        """Corrupted or partially written JSON files should not crash the source."""
        corrupt_file = self.sessions_dir / "corrupted.json"
        corrupt_file.write_text("{\"incomplete\": ", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 0)


if __name__ == "__main__":
    unittest.main()
