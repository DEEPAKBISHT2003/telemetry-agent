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


    def test_scenario_a_single_tool(self):
        """A completed run with one correlated tool receives the tool_name and one entry in tools."""
        workspace_hash = "w-hash-a"
        session_id = "ide-sess-a"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_a1",
                    "executionId": "exec-a1",
                    "status": "approved",
                },
            },
            {
                "type": "tool_result",
                "payload": {
                    "toolCallId": "tooluse_a1",
                    "executionId": "exec-a1",
                    "success": True,
                },
            },
            {
                "type": "usage_summary",
                "payload": {
                    "executionId": "exec-a1",
                    "credits": 0.05,
                    "status": "success",
                },
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertEqual(payload.get("tool_name"), "read_file")
        self.assertEqual(len(payload.get("tools", [])), 1)
        self.assertEqual(
            payload["tools"][0],
            {"tool_name": "read_file", "status": "success", "tool_use_id": "tooluse_a1"},
        )

    def test_scenario_b_multiple_distinct_tools(self):
        """A completed run with two different tools contains both and tool_name is None."""
        workspace_hash = "w-hash-b"
        session_id = "ide-sess-b"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_b1",
                    "executionId": "exec-b1",
                    "status": "approved",
                },
            },
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "write_file",
                    "toolCallId": "tooluse_b2",
                    "executionId": "exec-b1",
                    "status": "approved",
                },
            },
            {
                "type": "tool_result",
                "payload": {"toolCallId": "tooluse_b1", "success": True},
            },
            {
                "type": "tool_result",
                "payload": {"toolCallId": "tooluse_b2", "success": True},
            },
            {
                "type": "usage_summary",
                "payload": {
                    "executionId": "exec-b1",
                    "credits": 0.08,
                    "status": "success",
                },
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertIsNone(payload.get("tool_name"))
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 2)
        tool_names = [t["tool_name"] for t in tools]
        self.assertIn("read_file", tool_names)
        self.assertIn("write_file", tool_names)

    def test_scenario_c_repeated_tool_name(self):
        """Two calls with the same tool name but different invocation IDs remain distinct."""
        workspace_hash = "w-hash-c"
        session_id = "ide-sess-c"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_c1",
                    "executionId": "exec-c1",
                },
            },
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_c2",
                    "executionId": "exec-c1",
                },
            },
            {
                "type": "tool_result",
                "payload": {"toolCallId": "tooluse_c1", "success": True},
            },
            {
                "type": "tool_result",
                "payload": {"toolCallId": "tooluse_c2", "success": True},
            },
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-c1", "credits": 0.03},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertIsNone(payload.get("tool_name"))
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["tool_use_id"], "tooluse_c1")
        self.assertEqual(tools[1]["tool_use_id"], "tooluse_c2")

    def test_scenario_d_wrong_execution_id(self):
        """A tool event with a different execution ID is not attached to the completed run."""
        workspace_hash = "w-hash-d"
        session_id = "ide-sess-d"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_d1",
                    "executionId": "exec-other",
                },
            },
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-target", "credits": 0.02},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertIsNone(payload.get("tool_name"))
        self.assertEqual(payload.get("tools", []), [])

    def test_scenario_e_wrong_session_or_provider(self):
        """Events from a different session or provider are not attached."""
        from ai_telemetry_agent.sources.kiro_ide_sessions import correlate_and_enrich_run_tools

        completed_event = {
            "event_type": "ai_run_completed",
            "context": {"session_id": "sess-main"},
            "payload": {
                "provider": "kiro_ide",
                "run_id": "exec-main",
                "tool_name": None,
                "tools": [],
            },
        }

        tool_events = [
            # Wrong session ID
            {
                "event_type": "ai_tool_call",
                "context": {"session_id": "sess-diff"},
                "payload": {
                    "provider": "kiro_ide",
                    "tool_name": "tool_a",
                    "run_id": "call-1",
                    "metadata": {"execution_id": "exec-main", "tool_call_id": "call-1"},
                },
            },
            # Wrong provider
            {
                "event_type": "ai_tool_call",
                "context": {"session_id": "sess-main"},
                "payload": {
                    "provider": "antigravity",
                    "tool_name": "tool_b",
                    "run_id": "call-2",
                    "metadata": {"execution_id": "exec-main", "tool_call_id": "call-2"},
                },
            },
        ]

        enriched = correlate_and_enrich_run_tools(completed_event, tool_events)
        self.assertEqual(enriched["payload"]["tools"], [])
        self.assertIsNone(enriched["payload"]["tool_name"])

    def test_scenario_f_missing_execution_id(self):
        """If safe attribution is impossible due to missing execution ID, tool remains unattached."""
        workspace_hash = "w-hash-f"
        session_id = "ide-sess-f"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            # Tool call has NO execution ID
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_f1",
                    # executionId is intentionally missing
                },
            },
            # Completed run has an explicit execution ID
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-explicit", "credits": 0.04},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertIsNone(payload.get("tool_name"))
        self.assertEqual(payload.get("tools", []), [])

    def test_scenario_g_incomplete_execution_result_represented_as_unknown(self):
        """Do not mark an approved invocation as successful without result evidence."""
        workspace_hash = "w-hash-g"
        session_id = "ide-sess-g"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_g1",
                    "executionId": "exec-g1",
                    "status": "approved",  # Approved, but no tool_result follows!
                },
            },
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-g1", "credits": 0.05},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        payload = completed["payload"]
        self.assertEqual(payload.get("tool_name"), "read_file")
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "unknown")

    def test_scenario_h_privacy_boundary(self):
        """Ensure serialized completed-run events contain no synthetic arguments, paths, outputs, or prompts."""
        workspace_hash = "w-hash-h"
        session_id = "ide-sess-h"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        secret_arg = "API_KEY_SUPER_SECRET_XYZ"
        secret_path = "/var/keys/private.pem"
        secret_output = "INTERNAL_CERTIFICATE_DATA_RAW"

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_h1",
                    "executionId": "exec-h1",
                    "status": "approved",
                    "args": {"path": secret_path, "token": secret_arg},
                },
            },
            {
                "type": "tool_result",
                "payload": {
                    "toolCallId": "tooluse_h1",
                    "executionId": "exec-h1",
                    "success": True,
                    "content": secret_output,
                },
            },
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-h1", "credits": 0.05},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        serialized = json.dumps(completed)

        self.assertNotIn(secret_arg, serialized)
        self.assertNotIn(secret_path, serialized)
        self.assertNotIn(secret_output, serialized)

        tool_entry = completed["payload"]["tools"][0]
        self.assertEqual(set(tool_entry.keys()), {"tool_name", "status", "tool_use_id"})

    def test_scenario_i_deduplication_and_idempotency(self):
        """Repeated processing does not attach duplicate invocations or emit duplicate completed events."""
        workspace_hash = "w-hash-i"
        session_id = "ide-sess-i"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_i1",
                    "executionId": "exec-i1",
                },
            },
            # Duplicate tool_call line with the same toolCallId
            {
                "type": "tool_call",
                "payload": {
                    "toolName": "read_file",
                    "toolCallId": "tooluse_i1",
                    "executionId": "exec-i1",
                },
            },
            {
                "type": "tool_result",
                "payload": {"toolCallId": "tooluse_i1", "success": True},
            },
            {
                "type": "usage_summary",
                "payload": {"executionId": "exec-i1", "credits": 0.05},
            },
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events1 = self.source.collect()
        completed1 = [e for e in events1 if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(completed1), 1)
        self.assertEqual(len(completed1[0]["payload"]["tools"]), 1)

        # Repeated poll
        events2 = self.source.collect()
        completed2 = [e for e in events2 if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(completed2), 0)

    def test_scenario_j_accounting_integrity_preserved(self):
        """Enriched completed-run event produces exactly one accounting operation with unchanged credits."""
        from ai_telemetry_agent.core.collector import TelemetryCollector
        from ai_telemetry_agent.core.event import TelemetryEvent

        completed_dict = {
            "event_id": "ev-test-j1",
            "event_type": "ai_run_completed",
            "timestamp": "2026-10-18T16:00:00Z",
            "context": {"session_id": "sess-j"},
            "payload": {
                "provider": "kiro_ide",
                "model": "auto",
                "session_id": "sess-j",
                "run_id": "exec-j1",
                "credits": 0.0566367237,
                "capture_method": "messages_jsonl",
                "tool_name": "read_file",
                "tools": [
                    {
                        "tool_name": "read_file",
                        "status": "success",
                        "tool_use_id": "tooluse_j1",
                    }
                ],
                "metadata": {"status": "success"},
            },
        }

        ev = TelemetryEvent.from_dict(completed_dict)
        op = TelemetryCollector._ai_event_to_operation_record(None, ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.session_id, "sess-j")
        self.assertEqual(op.operation_type, "prompt")
        from decimal import Decimal
        self.assertEqual(op.credits_used, Decimal("0.0566367237"))
        self.assertEqual(op.deduplication_key, "kiro_ide:sess-j:exec-j1")

    def test_scenario_k_no_tool_run(self):
        """A completed run without tool calls retains empty tools list and None tool_name."""
        workspace_hash = "w-hash-k"
        session_id = "ide-sess-k"
        sess_dir = self.sessions_dir / workspace_hash / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)

        (sess_dir / "session.json").write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        messages = [
            {"type": "user", "timestamp": "2026-10-18T16:00:01Z"},
            {"type": "usage_summary", "payload": {"executionId": "exec-k1", "credits": 0.02}},
        ]
        (sess_dir / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in messages) + "\n", encoding="utf-8")

        events = self.source.collect()
        completed = next(e for e in events if e.get("event_type") == "ai_run_completed")
        self.assertIsNone(completed["payload"].get("tool_name"))
        self.assertEqual(completed["payload"].get("tools"), [])

    def test_scenario_session_summary_tool_deduplication(self):
        """Session aggregation deduplicates tool calls when both ai_tool_call and ai_run_completed are present."""
        from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore

        data_dir = Path(self.temp_dir.name) / "store_data"
        data_dir.mkdir(parents=True, exist_ok=True)
        store = JSONLEventStore(data_dir=data_dir)

        telemetry_file = data_dir / "telemetry-2026-10-18.jsonl"
        events = [
            {
                "event_id": "ev-tool-1",
                "event_type": "ai_tool_call",
                "timestamp": "2026-10-18T16:00:01Z",
                "context": {"session_id": "sess-sum"},
                "payload": {
                    "provider": "kiro_ide",
                    "session_id": "sess-sum",
                    "run_id": "tooluse_sum1",
                    "tool_name": "read_file",
                    "metadata": {"tool_call_id": "tooluse_sum1", "execution_id": "exec-sum"},
                },
            },
            {
                "event_id": "ev-comp-1",
                "event_type": "ai_run_completed",
                "timestamp": "2026-10-18T16:00:05Z",
                "context": {"session_id": "sess-sum"},
                "payload": {
                    "provider": "kiro_ide",
                    "session_id": "sess-sum",
                    "run_id": "exec-sum",
                    "tool_name": "read_file",
                    "tools": [
                        {
                            "tool_name": "read_file",
                            "status": "success",
                            "tool_use_id": "tooluse_sum1",
                        }
                    ],
                    "credits": 0.05,
                    "capture_method": "messages_jsonl",
                    "metadata": {},
                },
            },
        ]
        telemetry_file.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

        sessions = store.get_sessions(session_id="sess-sum")
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess["tool_calls_count"], 1)
        self.assertEqual(sess["tools_used"], ["read_file"])


if __name__ == "__main__":
    unittest.main()

