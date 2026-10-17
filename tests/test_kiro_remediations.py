"""Comprehensive regression tests for Kiro telemetry audit remediations:
1. Source selection in auto mode (both present, CLI only, IDE only, neither present, CLI appearing later).
2. CLI session path resolution (always targets cli/ child dir, never parent, no dir pre-creation).
3. Staged-state rollback on persistence failure (CLI and IDE).
"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from ai_telemetry_agent.config.settings import load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import EventType
from ai_telemetry_agent.storage.base import StoragePersistenceError


def _make_cli_session_data(session_id: str, turn_id: str, credits: float = 0.05) -> dict:
    return {
        "session_id": session_id,
        "updated_at": "2026-10-14T07:00:00Z",
        "session_state": {
            "id": session_id,
            "conversation_metadata": {
                "user_turn_metadatas": [
                    {
                        "id": turn_id,
                        "user_turn_metadata": {
                            "model": "claude-haiku-4.5",
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "cache_read_input_token_count": 0,
                            "cache_write_input_token_count": 0,
                            "total_request_count": 1,
                            "metering_usage": credits,
                            "end_reason": "end_turn",
                        },
                    }
                ]
            },
        },
    }


def _make_ide_session_data(session_id: str) -> dict:
    return {
        "id": session_id,
        "title": "Test IDE Session",
        "modelId": "claude-sonnet-4.5",
        "createdAt": "2026-10-14T06:00:00Z",
        "lastModifiedAt": "2026-10-14T06:05:00Z",
        "workspacePaths": [],
    }


class TestKiroAutoModeAndPathResolution(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.sessions_dir = self.base_dir / "sessions"
        self.data_dir = self.base_dir / "data"
        self.env_file = self.base_dir / ".env"
        self.collectors = []

    def tearDown(self):
        for c in self.collectors:
            try:
                c.stop()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def _get_collector(self, source_mode: str = "auto", kiro_sessions_dir: Path = None) -> TelemetryCollector:
        s_dir = kiro_sessions_dir or self.sessions_dir
        content = (
            f"MEMBER_ID=M_TEST\n"
            f"DEVICE_ID=DEV_TEST\n"
            f"TELEMETRY_DATA_DIR={self.data_dir}\n"
            f"TELEMETRY_SOURCE={source_mode}\n"
            f"KIRO_SESSIONS_DIR={s_dir}\n"
            f"KIRO_MONITOR_ENABLED=true\n"
            f"ANTIGRAVITY_MONITOR_ENABLED=true\n"
        )
        self.env_file.write_text(content, encoding="utf-8")
        settings = load_settings(env_file=str(self.env_file))
        collector = TelemetryCollector(settings)
        self.collectors.append(collector)
        return collector

    def test_auto_mode_both_cli_and_ide_present(self):
        """Case 1: Both CLI and IDE directories present. Both sources must collect concurrently."""
        cli_dir = self.sessions_dir / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        ws_dir = self.sessions_dir / "workspace_hash_abc" / "ide_sess_1"
        ws_dir.mkdir(parents=True, exist_ok=True)

        collector = self._get_collector(source_mode="auto")
        collector.start()

        # Both sources must be active in collector.sources
        self.assertIn(collector.kiro_cli_source, collector.sources)
        self.assertIn(collector.kiro_ide_source, collector.sources)

        # Write files after start to test poll_and_process
        cli_file = cli_dir / "cli_sess_1.json"
        cli_file.write_text(json.dumps(_make_cli_session_data("cli-dual-1", "turn-1")), encoding="utf-8")

        (ws_dir / "session.json").write_text(json.dumps(_make_ide_session_data("ide-dual-1")), encoding="utf-8")
        msg = {
            "id": "msg-1",
            "type": "tool_use",
            "timestamp": "2026-10-14T06:01:00Z",
            "content": {"tool": "searchCode"},
        }
        (ws_dir / "messages.jsonl").write_text(json.dumps(msg) + "\n", encoding="utf-8")

        # Poll and verify events from both CLI and IDE are captured
        count = collector.poll_and_process()
        self.assertGreaterEqual(count, 2)

        events = collector.store.get_events(limit=20)
        cli_events = [e for e in events if getattr(e.payload, "provider", None) == "kiro_cli" or e.payload.get("provider") == "kiro_cli"]
        ide_events = [e for e in events if getattr(e.payload, "provider", None) == "kiro_ide" or e.payload.get("provider") == "kiro_ide"]

        self.assertGreaterEqual(len(cli_events), 1)
        self.assertGreaterEqual(len(ide_events), 1)

    def test_auto_mode_cli_present_ide_absent(self):
        """Case 2: CLI directory present but IDE sessions absent. CLI events collected without IDE failure."""
        cli_dir = self.sessions_dir / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)

        collector = self._get_collector(source_mode="auto")
        collector.start()

        cli_file = cli_dir / "cli_sess_2.json"
        cli_file.write_text(json.dumps(_make_cli_session_data("cli-only-1", "turn-1")), encoding="utf-8")

        count = collector.poll_and_process()
        self.assertGreaterEqual(count, 1)

        events = collector.store.get_events(limit=10)
        cli_events = [e for e in events if e.payload.get("provider") == "kiro_cli"]
        self.assertEqual(len(cli_events), 1)

    def test_auto_mode_ide_present_cli_absent(self):
        """Case 3: IDE sessions present but CLI directory absent. IDE events collected without creating cli/."""
        ws_dir = self.sessions_dir / "workspace_hash_xyz" / "ide_sess_2"
        ws_dir.mkdir(parents=True, exist_ok=True)

        collector = self._get_collector(source_mode="auto")
        collector.start()

        # cli/ directory must NOT have been artificially created
        cli_dir = self.sessions_dir / "cli"
        self.assertFalse(cli_dir.exists())

        (ws_dir / "session.json").write_text(json.dumps(_make_ide_session_data("ide-only-1")), encoding="utf-8")

        count = collector.poll_and_process()
        self.assertGreaterEqual(count, 1)

        events = collector.store.get_events(limit=10)
        ide_events = [e for e in events if e.payload.get("provider") == "kiro_ide"]
        self.assertEqual(len(ide_events), 1)
        self.assertEqual(ide_events[0].context.session_id, "ide-only-1")

    def test_auto_mode_neither_kiro_directory_exists(self):
        """Case 4: Neither Kiro source directory exists. Antigravity fallback source is included."""
        non_existent = self.base_dir / "does_not_exist"
        collector = self._get_collector(source_mode="auto", kiro_sessions_dir=non_existent)
        collector.start()

        # Antigravity fallback must be in collector sources
        self.assertIn(collector.antigravity_source, collector.sources)
        self.assertIn(collector.kiro_cli_source, collector.sources)
        self.assertIn(collector.kiro_ide_source, collector.sources)

    def test_auto_mode_new_cli_directory_appears_after_init(self):
        """Case 5: A new CLI directory appears after collector initialization. Reader must ingest sessions."""
        # Sessions dir does not exist at init
        self.assertFalse(self.sessions_dir.exists())

        collector = self._get_collector(source_mode="auto")
        collector.start()

        # Poll 1: returns 0 Kiro events
        count1 = collector.poll_and_process()
        self.assertEqual(count1, 0)

        # CLI directory and session appear later
        cli_dir = self.sessions_dir / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        cli_file = cli_dir / "late_sess.json"
        cli_file.write_text(json.dumps(_make_cli_session_data("cli-late-1", "turn-late")), encoding="utf-8")

        # Poll 2: Reader dynamically discovers cli/ and ingests the session
        count2 = collector.poll_and_process()
        self.assertGreaterEqual(count2, 1)

        events = collector.store.get_events(limit=10)
        late_events = [e for e in events if e.context.session_id == "cli-late-1"]
        self.assertEqual(len(late_events), 1)
        self.assertEqual(late_events[0].payload.get("provider"), "kiro_cli")

    def test_cli_path_resolution_does_not_scan_parent_or_precreate_child(self):
        """Regression test for Finding 2: CLI source always targets cli/ child and never parent."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        # Put a loose file directly in parent sessions directory
        parent_loose_file = self.sessions_dir / "parent_loose.json"
        parent_loose_file.write_text(json.dumps(_make_cli_session_data("loose-sess", "turn-loose")), encoding="utf-8")

        collector = self._get_collector(source_mode="kiro_cli")
        collector.start()

        expected_cli_dir = self.sessions_dir / "cli"
        self.assertEqual(collector.kiro_cli_source.sessions_dir, expected_cli_dir)
        # Directory must not be created at init time
        self.assertFalse(expected_cli_dir.exists())

        # Poll 1: Must NOT scan parent directory, so loose file must NOT be ingested
        count1 = collector.poll_and_process()
        self.assertEqual(count1, 0)

        # Now create cli/ and put session file in it
        expected_cli_dir.mkdir(parents=True, exist_ok=True)
        cli_file = expected_cli_dir / "cli_valid.json"
        cli_file.write_text(json.dumps(_make_cli_session_data("valid-sess", "turn-valid")), encoding="utf-8")

        # Poll 2: Ingests the valid file from cli/
        count2 = collector.poll_and_process()
        self.assertGreaterEqual(count2, 1)

        events = collector.store.get_events(limit=10)
        valid_events = [e for e in events if e.context.session_id == "valid-sess"]
        loose_events = [e for e in events if e.context.session_id == "loose-sess"]

        self.assertEqual(len(valid_events), 1)
        self.assertEqual(len(loose_events), 0)


class TestKiroRollbackState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.sessions_dir = self.base_dir / "sessions"
        self.data_dir = self.base_dir / "data"
        self.env_file = self.base_dir / ".env"
        self.collectors = []

    def tearDown(self):
        for c in self.collectors:
            try:
                c.stop()
            except Exception:
                pass
        self.temp_dir.cleanup()

    def _get_collector(self, source_mode: str) -> TelemetryCollector:
        content = (
            f"MEMBER_ID=M_TEST\n"
            f"DEVICE_ID=DEV_TEST\n"
            f"TELEMETRY_DATA_DIR={self.data_dir}\n"
            f"TELEMETRY_SOURCE={source_mode}\n"
            f"KIRO_SESSIONS_DIR={self.sessions_dir}\n"
            f"KIRO_MONITOR_ENABLED=true\n"
        )
        self.env_file.write_text(content, encoding="utf-8")
        settings = load_settings(env_file=str(self.env_file))
        collector = TelemetryCollector(settings)
        self.collectors.append(collector)
        return collector

    def test_kiro_cli_rollback_on_persistence_failure_re_polls_cleanly(self):
        """Simulate storage failure in CLI source; verify rollback and subsequent clean ingestion without duplicate credits."""
        cli_dir = self.sessions_dir / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)

        collector = self._get_collector("kiro_cli")
        collector.start()

        # Write session file after start
        cli_file = cli_dir / "cli_sess_fail.json"
        cli_file.write_text(json.dumps(_make_cli_session_data("cli-fail-sess", "turn-fail", credits=0.12)), encoding="utf-8")

        # Mock save_event to fail on first attempt
        original_save = collector.store.save_event
        fail_mock = MagicMock(side_effect=StoragePersistenceError("Simulated disk full"))
        collector.store.save_event = fail_mock

        # Poll 1: fails persistence
        count1 = collector.poll_and_process()
        self.assertEqual(count1, 0)

        # Staged state must have been rolled back to match uncommitted state
        cli_src = collector.kiro_cli_source
        self.assertEqual(cli_src._staged_state, cli_src._state)
        self.assertNotIn("cli-fail-sess", cli_src._state)

        # Restore normal storage
        collector.store.save_event = original_save

        # Poll 2: succeeds and ingests the turn
        count2 = collector.poll_and_process()
        self.assertGreaterEqual(count2, 1)

        # Verify event saved with exact credits
        events = [e for e in collector.store.get_events(limit=10) if e.context.session_id == "cli-fail-sess"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload.get("credits"), 0.12)
        self.assertIsNone(events[0].payload.get("cost_usd"))
        self.assertIsNone(events[0].payload.get("input_tokens"))

        # Poll 3: Cursor committed, no duplicate events
        count3 = collector.poll_and_process()
        self.assertEqual(count3, 0)
        events_after = [e for e in collector.store.get_events(limit=10) if e.context.session_id == "cli-fail-sess"]
        self.assertEqual(len(events_after), 1)

    def test_kiro_ide_rollback_on_persistence_failure_re_polls_cleanly(self):
        """Simulate storage failure in IDE source; verify rollback and subsequent clean ingestion."""
        ws_dir = self.sessions_dir / "workspace_hash_fail" / "ide_sess_fail"
        ws_dir.mkdir(parents=True, exist_ok=True)

        collector = self._get_collector("kiro_ide")
        collector.start()

        # Write session file after start
        (ws_dir / "session.json").write_text(json.dumps(_make_ide_session_data("ide-fail-sess")), encoding="utf-8")

        # Mock save_event to fail on first attempt
        original_save = collector.store.save_event
        fail_mock = MagicMock(side_effect=StoragePersistenceError("Simulated write error"))
        collector.store.save_event = fail_mock

        # Poll 1: fails persistence
        count1 = collector.poll_and_process()
        self.assertEqual(count1, 0)

        # Staged state must have been rolled back
        ide_src = collector.kiro_ide_source
        self.assertEqual(ide_src._staged_state, ide_src._state)
        self.assertNotIn("ide-fail-sess", ide_src._state)

        # Restore normal storage
        collector.store.save_event = original_save

        # Poll 2: succeeds and ingests session
        count2 = collector.poll_and_process()
        self.assertGreaterEqual(count2, 1)

        events = [e for e in collector.store.get_events(limit=10) if e.context.session_id == "ide-fail-sess"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "ai_run_started")

        # Poll 3: Cursor committed, no duplicates
        count3 = collector.poll_and_process()
        self.assertEqual(count3, 0)
        events_after = [e for e in collector.store.get_events(limit=10) if e.context.session_id == "ide-fail-sess"]
        self.assertEqual(len(events_after), 1)


if __name__ == "__main__":
    unittest.main()
