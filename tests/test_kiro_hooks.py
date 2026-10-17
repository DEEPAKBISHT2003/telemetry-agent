"""Unit tests for Kiro hook handling, installer, uninstaller, and status."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.integrations.hooks.kiro_hook_handler import process_kiro_hook
from ai_telemetry_agent.integrations.hooks.kiro_installer import (
    get_kiro_hook_status,
    install_kiro_hooks,
    uninstall_kiro_hooks,
)
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore


class TestKiroHooks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hooks_file = Path(self.temp_dir.name) / "telemetry.json"
        self.data_dir = Path(self.temp_dir.name) / "data"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_installer_is_idempotent_and_preserves_other_hooks(self):
        """Installing Kiro hooks should create valid config and be idempotent."""
        # Initial install
        success, msg, backup, other = install_kiro_hooks(hooks_file=self.hooks_file)
        self.assertTrue(success)
        self.assertTrue(self.hooks_file.is_file())

        status = get_kiro_hook_status(hooks_file=self.hooks_file)
        self.assertTrue(status["config_exists"])
        self.assertTrue(status["json_valid"])
        self.assertTrue(status["telemetry_hook_installed"])

        # Second install (idempotent)
        success2, msg2, backup2, other2 = install_kiro_hooks(hooks_file=self.hooks_file)
        self.assertTrue(success2)
        self.assertIn("up to date", msg2.lower())

    def test_uninstaller_removes_cleanly(self):
        """Uninstalling Kiro hooks should cleanly remove them while creating a backup."""
        install_kiro_hooks(hooks_file=self.hooks_file)
        success, msg, backup = uninstall_kiro_hooks(hooks_file=self.hooks_file)
        self.assertTrue(success)

        status = get_kiro_hook_status(hooks_file=self.hooks_file)
        self.assertFalse(status["telemetry_hook_installed"])
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())

    def test_hook_handler_processes_stdin_without_stdout_or_failure(self):
        """Kiro hook handler must read stdin, write to store, and exit 0 without stdout pollution."""
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "hook-sess-123",
            "tool_name": "editFiles",
            "cwd": self.temp_dir.name,
        }

        # Redirect stdout and stdin
        saved_stdin = sys.stdin
        saved_stdout = sys.stdout
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()

        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        try:
            with self.assertRaises(SystemExit) as cm:
                process_kiro_hook("PostToolUse", collector=collector)
            stdout_content = sys.stdout.getvalue()
            exit_code = cm.exception.code
        finally:
            sys.stdin = saved_stdin
            sys.stdout = saved_stdout

        self.assertEqual(exit_code, 0)
        # Non-negotiable constraint: no prompt injection via stdout for Kiro
        self.assertEqual(stdout_content, "")

        events = collector.store.get_events(limit=10)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event_type, "ai_tool_call")
        self.assertEqual(ev.context.session_id, "hook-sess-123")
        self.assertEqual(ev.payload.get("tool_name"), "editFiles")
        self.assertNotIn("decision", stdout_content)

    def test_session_end_emits_run_completed(self):
        """Kiro hook handler must map SessionEnd to ai_run_completed event."""
        payload = {
            "hook_event_name": "SessionEnd",
            "session_id": "hook-sess-end-456",
            "cwd": self.temp_dir.name,
        }
        saved_stdin = sys.stdin
        saved_stdout = sys.stdout
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()

        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        try:
            with self.assertRaises(SystemExit) as cm:
                process_kiro_hook("SessionEnd", collector=collector)
            stdout_content = sys.stdout.getvalue()
            exit_code = cm.exception.code
        finally:
            sys.stdin = saved_stdin
            sys.stdout = saved_stdout

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout_content, "")
        events = collector.store.get_events(limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "ai_run_completed")
        self.assertEqual(events[0].context.session_id, "hook-sess-end-456")

    def test_stop_and_session_end_deduplication(self):
        """Firing both Stop and SessionEnd for the same session must emit exactly one run_completed event."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        # 1. Fire Stop
        payload_stop = {"session_id": "hook-sess-dual-789", "cwd": self.temp_dir.name}
        saved_stdin = sys.stdin
        saved_stdout = sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload_stop))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                process_kiro_hook("Stop", collector=collector)
        finally:
            sys.stdin = saved_stdin
            sys.stdout = saved_stdout

        # 2. Fire SessionEnd for the same session
        payload_end = {"session_id": "hook-sess-dual-789", "cwd": self.temp_dir.name}
        try:
            sys.stdin = io.StringIO(json.dumps(payload_end))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                process_kiro_hook("SessionEnd", collector=collector)
        finally:
            sys.stdin = saved_stdin
            sys.stdout = saved_stdout

        # Verify only 1 event was recorded for the session
        events = [e for e in collector.store.get_events(limit=20) if e.context.session_id == "hook-sess-dual-789"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "ai_run_completed")


if __name__ == "__main__":
    unittest.main()
