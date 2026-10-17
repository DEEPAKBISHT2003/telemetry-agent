"""Unit tests for Kiro IDE session parsing, tool calls, offset tracking, and credit summaries."""

import json
from pathlib import Path
import tempfile
import unittest

from ai_telemetry_agent.sources.kiro_ide_sessions import KiroIDESessionSource


class TestKiroIDESessionSource(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions_dir = Path(self.temp_dir.name) / "ide"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(self.temp_dir.name) / "kiro_ide_state.json"

        self.source = KiroIDESessionSource(
            sessions_dir=self.sessions_dir,
            state_file=self.state_file,
        )
        self.source.start()

    def tearDown(self):
        self.source.stop()
        self.temp_dir.cleanup()

    def test_parses_ide_session_and_messages(self):
        """Kiro IDE session.json and messages.jsonl should be parsed into structured telemetry events."""
        workspace_hash = "w-hash-abc"
        session_id = "ide-session-001"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        session_meta = {
            "session_id": session_id,
            "created_at": "2026-10-14T06:00:00Z",
            "model": "claude-3-7-sonnet",
            "working_directory": "C:/Projects/myapp",
        }
        (sess_dir / "session.json").write_text(json.dumps(session_meta), encoding="utf-8")

        messages_file = sess_dir / "messages.jsonl"
        messages = [
            {
                "type": "user",
                "timestamp": "2026-10-14T06:00:05Z",
            },
            {
                "type": "tool_call",
                "name": "readFile",
                "timestamp": "2026-10-14T06:00:08Z",
            },
            {
                "type": "turn_completed",
                "timestamp": "2026-10-14T06:00:15Z",
                "context_usage_percentage": 0.12,
                "credits": 0.045,
            }
        ]
        messages_file.write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        self.assertTrue(len(events) >= 2)

        tool_evs = [e for e in events if e.get("event_type") in ("ai_tool_call", "tool_call")]
        self.assertEqual(len(tool_evs), 1)
        self.assertEqual(tool_evs[0].get("payload", {}).get("tool_name"), "readFile")

        completed_evs = [e for e in events if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(completed_evs), 1)
        self.assertEqual(completed_evs[0].get("payload", {}).get("credits"), 0.045)
        self.assertEqual(completed_evs[0].get("payload", {}).get("context_usage_percentage"), 0.12)

    def test_line_offset_tracking(self):
        """Second poll without appending lines should yield zero new messages."""
        workspace_hash = "w-hash-offset"
        session_id = "ide-session-offset"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        session_meta = {"session_id": session_id}
        (sess_dir / "session.json").write_text(json.dumps(session_meta), encoding="utf-8")

        messages_file = sess_dir / "messages.jsonl"
        messages_file.write_text(json.dumps({"type": "tool_call", "name": "runCommand"}) + "\n", encoding="utf-8")

        events1 = [e for e in self.source.collect() if e.get("event_type") in ("ai_tool_call", "ai_run_completed")]
        self.assertEqual(len(events1), 1)

        # Polling again immediately
        events2 = [e for e in self.source.collect() if e.get("event_type") in ("ai_tool_call", "ai_run_completed")]
        self.assertEqual(len(events2), 0)

        # Append another message
        with open(messages_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "turn_completed", "credits": 0.02}) + "\n")

        events3 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events3), 1)
        self.assertEqual(events3[0].get("payload", {}).get("credits"), 0.02)


if __name__ == "__main__":
    unittest.main()
