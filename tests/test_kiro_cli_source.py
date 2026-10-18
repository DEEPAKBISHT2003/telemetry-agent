"""Unit tests for Kiro CLI session parsing, normalization, credits, and token handling."""

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import TelemetryEvent
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

    # A. Successful tool call
    def test_tool_call_successful(self):
        """A valid tool invocation and matching result produce one sanitized tool record."""
        session_id = "test-session-tool-success"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-tool-1",
                                    "user_turn_metadata": {
                                        "turn_duration_ms": 1500,
                                        "metering_usage": 0.045,
                                        "builtin_tool_uses": 1,
                                    }
                                }
                            },
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1", "msg-a-2"],
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1", "content": [{"kind": "text", "data": "Read config"}]}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-1",
                    "content": [
                        {"kind": "text", "data": "Reading file..."},
                        {
                            "kind": "toolUse",
                            "data": {
                                "toolUseId": "call-read-01",
                                "name": "read",
                                "input": {"path": "config.yaml"}
                            }
                        }
                    ]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-1",
                    "content": [
                        {
                            "kind": "toolResult",
                            "data": {
                                "toolUseId": "call-read-01",
                                "status": "success",
                                "content": [{"kind": "text", "data": "key: val"}]
                            }
                        }
                    ],
                    "results": {
                        "call-read-01": {"result": {"Success": {"items": [{"Text": "key: val"}]}}}
                    }
                }
            }),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-2", "content": [{"kind": "text", "data": "Done."}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        all_events = self.source.collect()
        events = [e for e in all_events if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)

        ev = events[0]
        payload = ev.get("payload", {})
        self.assertEqual(payload.get("tool_name"), "read")
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "read")
        self.assertEqual(tools[0]["status"], "success")
        self.assertEqual(tools[0]["tool_use_id"], "call-read-01")

    # B. Multiple tool calls
    def test_tool_calls_multiple(self):
        """A single turn with several distinct tool invocations records all of them without collapsing."""
        session_id = "test-session-multi-tool"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-multi-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.08,
                            "builtin_tool_uses": 2,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-1",
                    "content": [
                        {"kind": "toolUse", "data": {"toolUseId": "call-1", "name": "read"}},
                        {"kind": "toolUse", "data": {"toolUseId": "call-2", "name": "write"}},
                    ]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-1",
                    "content": [
                        {"kind": "toolResult", "data": {"toolUseId": "call-1", "status": "success"}},
                        {"kind": "toolResult", "data": {"toolUseId": "call-2", "status": "success"}},
                    ]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        payload = events[0].get("payload", {})
        # For multiple tools, tool_name must be None rather than picking an arbitrary tool
        self.assertIsNone(payload.get("tool_name"))
        tools = payload.get("tools", [])
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["tool_name"], "read")
        self.assertEqual(tools[1]["tool_name"], "write")

    # C. Same tool invoked more than once
    def test_same_tool_invoked_more_than_once(self):
        """Two invocations using the same tool name remain distinguishable by invocation ID."""
        session_id = "test-session-same-tool-twice"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-same-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.06,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-1",
                    "content": [
                        {"kind": "toolUse", "data": {"toolUseId": "call-read-A", "name": "read"}},
                        {"kind": "toolUse", "data": {"toolUseId": "call-read-B", "name": "read"}},
                    ]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-1",
                    "content": [
                        {"kind": "toolResult", "data": {"toolUseId": "call-read-A", "status": "success"}},
                        {"kind": "toolResult", "data": {"toolUseId": "call-read-B", "status": "success"}},
                    ]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        tools = events[0].get("payload", {}).get("tools", [])
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["tool_use_id"], "call-read-A")
        self.assertEqual(tools[1]["tool_use_id"], "call-read-B")
        self.assertEqual(tools[0]["tool_name"], "read")
        self.assertEqual(tools[1]["tool_name"], "read")

    # D. Missing result
    def test_missing_tool_result_represented_as_unknown(self):
        """A recognized invocation with no result is represented as unknown, not as a failure."""
        session_id = "test-session-missing-result"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-no-res-1",
                            "message_ids": ["msg-p-1", "msg-a-1"],
                            "metering_usage": 0.03,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-1",
                    "content": [
                        {"kind": "toolUse", "data": {"toolUseId": "call-no-result", "name": "bash"}},
                    ]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        tools = events[0].get("payload", {}).get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "bash")
        self.assertEqual(tools[0]["status"], "unknown")
        self.assertNotEqual(tools[0]["status"], "failure")

    # E. Result without invocation
    def test_result_without_invocation_not_fabricated(self):
        """An orphaned result is not fabricated into a new tool call."""
        session_id = "test-session-orphan-result"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-orphan-1",
                            "message_ids": ["msg-p-1", "msg-tr-orphan"],
                            "metering_usage": 0.02,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-orphan",
                    "content": [
                        {"kind": "toolResult", "data": {"toolUseId": "nonexistent-call", "status": "success"}}
                    ]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        payload = events[0].get("payload", {})
        self.assertEqual(payload.get("tools"), [])
        self.assertIsNone(payload.get("tool_name"))

    # F. Malformed JSONL
    def test_malformed_jsonl_handled_resiliently(self):
        """A malformed line does not crash the source or prevent other valid lines from being parsed."""
        session_id = "test-session-malformed-jsonl"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-mal-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.035,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        raw_content = (
            '{"kind": "Prompt", "data": {"message_id": "msg-p-1"}}\n'
            '{"corrupt line without valid json\n'
            '{"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-1", "name": "grep"}}]}}\n'
            '{"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-1", "status": "success"}}]}}\n'
        )
        jsonl_file.write_text(raw_content, encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        tools = events[0].get("payload", {}).get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "grep")
        self.assertEqual(tools[0]["status"], "success")

    # G. Partial writes
    def test_partial_write_jsonl_handled_safely(self):
        """An incomplete final line is handled safely; valid parent-turn credits are not lost."""
        session_id = "test-session-partial-write"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-pw-1",
                            "message_ids": ["msg-p-1", "msg-a-1"],
                            "metering_usage": 0.05,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        raw_content = (
            '{"kind": "Prompt", "data": {"message_id": "msg-p-1"}}\n'
            '{"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-pw", "name": "glob"}}]}}\n'
            '{"kind": "ToolResults", "data": {"message_id": "msg-tr-incomplete", "cont'
        )
        jsonl_file.write_text(raw_content, encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        ev = events[0]
        # Parent turn credit must NOT be lost
        self.assertEqual(ev.get("payload", {}).get("credits"), 0.05)
        tools = ev.get("payload", {}).get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "glob")
        self.assertEqual(tools[0]["status"], "unknown")

    # H. Privacy
    def test_privacy_sensitive_data_strictly_excluded(self):
        """Assert that synthetic secrets, file contents, file paths, tool arguments, and tool outputs are absent in serialized JSON."""
        session_id = "test-session-privacy"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        secret_key = "SECRET_API_KEY_xyz_123456789"
        sensitive_path = "C:\\sensitive\\user\\id_rsa"
        sensitive_output = "PRIVATE_KEY_BYTES_CONTENT_TOP_SECRET"
        prompt_secret = "USER_SUPER_SECRET_PASSWORD"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-priv-1",
                            "message_ids": ["msg-p-priv", "msg-a-priv", "msg-tr-priv"],
                            "metering_usage": 0.02,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-priv", "content": [{"kind": "text", "data": prompt_secret}]}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-priv",
                    "content": [
                        {
                            "kind": "toolUse",
                            "data": {
                                "toolUseId": "call-priv-01",
                                "name": "read_secret",
                                "input": {
                                    "path": sensitive_path,
                                    "api_key": secret_key,
                                }
                            }
                        }
                    ]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-priv",
                    "content": [
                        {
                            "kind": "toolResult",
                            "data": {
                                "toolUseId": "call-priv-01",
                                "status": "success",
                                "content": [{"kind": "text", "data": sensitive_output}]
                            }
                        }
                    ]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        ev_serialized = json.dumps(events[0])

        self.assertNotIn(secret_key, ev_serialized)
        self.assertNotIn(sensitive_path, ev_serialized)
        self.assertNotIn(sensitive_output, ev_serialized)
        self.assertNotIn(prompt_secret, ev_serialized)

        # Check permitted fields are present
        tools = events[0].get("payload", {}).get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool_name"], "read_secret")
        self.assertEqual(tools[0]["status"], "success")

    # I. Turn association
    def test_turn_association_scoped_to_correct_turn(self):
        """Tools from one turn are not attached to another turn or session."""
        session_id = "test-session-turn-assoc"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-0",
                            "message_ids": ["msg-p-0", "msg-a-0", "msg-tr-0"],
                            "metering_usage": 0.01,
                            "selected_model": "claude-haiku-4.5",
                        },
                        {
                            "id": "turn-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.02,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-0"}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-0",
                    "content": [{"kind": "toolUse", "data": {"toolUseId": "call-t0", "name": "tool_turn_zero"}}]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-0",
                    "content": [{"kind": "toolResult", "data": {"toolUseId": "call-t0", "status": "success"}}]
                }
            }),
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({
                "kind": "AssistantMessage",
                "data": {
                    "message_id": "msg-a-1",
                    "content": [{"kind": "toolUse", "data": {"toolUseId": "call-t1", "name": "tool_turn_one"}}]
                }
            }),
            json.dumps({
                "kind": "ToolResults",
                "data": {
                    "message_id": "msg-tr-1",
                    "content": [{"kind": "toolResult", "data": {"toolUseId": "call-t1", "status": "success"}}]
                }
            }),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 2)

        ev0 = [e for e in events if e.get("payload", {}).get("run_id") == "turn-0"][0]
        ev1 = [e for e in events if e.get("payload", {}).get("run_id") == "turn-1"][0]

        tools0 = ev0.get("payload", {}).get("tools", [])
        tools1 = ev1.get("payload", {}).get("tools", [])

        self.assertEqual(len(tools0), 1)
        self.assertEqual(tools0[0]["tool_name"], "tool_turn_zero")

        self.assertEqual(len(tools1), 1)
        self.assertEqual(tools1[0]["tool_name"], "tool_turn_one")

    # J. Idempotency
    def test_idempotency_repeated_polling(self):
        """Repeated polling does not create duplicate completed-run events."""
        session_id = "test-session-idempotency"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-idem-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.05,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-1", "name": "read"}}]}}),
            json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-1", "status": "success"}}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events1 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events1), 1)

        # Poll 2: unchanged
        events2 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events2), 0)

        # JSONL file updated with an additional result line for an old call
        jsonl_lines.append(json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-late", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-1", "status": "success"}}]}}))
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        # Poll 3: should still be 0 (no duplicate parent-turn event)
        events3 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events3), 0)

    # K. Credits remain unchanged
    def test_credits_remain_unchanged_with_tool_metadata(self):
        """For a fixture with known metering usage, adding JSONL tool metadata does not alter parent-turn credits."""
        session_id = "test-session-credits-check"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-cred-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.05703900199004976,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-c1", "name": "read"}}]}}),
            json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-c1", "status": "success"}}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("payload", {}).get("credits"), 0.05703900199004976)

    # L. Backward compatibility
    def test_backward_compatibility_no_jsonl(self):
        """Existing .json fixtures without a corresponding JSONL file still produce valid completed-run events with empty tool list."""
        session_id = "test-session-no-jsonl"
        session_file = self.sessions_dir / f"{session_id}.json"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-no-jsonl-1",
                            "metering_usage": 0.012,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.get("payload", {}).get("tools"), [])
        self.assertIsNone(ev.get("payload", {}).get("tool_name"))
        self.assertEqual(ev.get("payload", {}).get("credits"), 0.012)

    # M. Event serialization
    def test_event_serialization_round_trip(self):
        """The event remains serializable and round-trips through TelemetryEvent without losing tool metadata."""
        session_id = "test-session-round-trip"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-rt-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.02,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-rt-1", "name": "read"}}]}}),
            json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-rt-1", "status": "success"}}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        raw_events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(raw_events), 1)

        raw_dict = raw_events[0]
        telemetry_event = TelemetryEvent.from_dict(raw_dict)
        self.assertEqual(telemetry_event.payload.get("tool_name"), "read")
        self.assertEqual(len(telemetry_event.payload.get("tools", [])), 1)

        json_str = telemetry_event.to_json()
        restored_dict = json.loads(json_str)
        self.assertEqual(restored_dict["payload"]["tool_name"], "read")
        self.assertEqual(restored_dict["payload"]["tools"][0]["tool_name"], "read")
        self.assertEqual(restored_dict["payload"]["tools"][0]["status"], "success")

    # Delayed JSONL recovery test
    def test_delayed_jsonl_recovery_on_subsequent_poll(self):
        """When JSONL records are delayed, the source defers the turn and recovers tool metadata on subsequent poll."""
        session_id = "test-session-delayed-jsonl"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-delayed-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.04,
                            "builtin_tool_uses": 1,
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        # Initially JSONL is empty (delayed flush)
        jsonl_file.write_text("", encoding="utf-8")

        # Poll 1: deferred because builtin_tool_uses == 1 but JSONL has 0 tools
        events1 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events1), 0)

        # Now JSONL receives records
        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-delayed-1", "name": "read"}}]}}),
            json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-delayed-1", "status": "success"}}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        # Poll 2: recovered! Turn emitted with tool metadata
        events2 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events2), 1)
        self.assertEqual(events2[0].get("payload", {}).get("tool_name"), "read")
        self.assertEqual(len(events2[0].get("payload", {}).get("tools", [])), 1)

        # Poll 3: unchanged, no duplicate
        events3 = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events3), 0)

    # Accounting integration test
    def test_accounting_integration_one_operation_preserved_credits(self):
        """Turn with tool metadata produces exactly one accounting operation with unchanged credits and deduplication."""
        session_id = "test-session-acc-integ"
        session_file = self.sessions_dir / f"{session_id}.json"
        jsonl_file = self.sessions_dir / f"{session_id}.jsonl"

        session_data = {
            "session_id": session_id,
            "session_state": {
                "id": session_id,
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn-acc-1",
                            "message_ids": ["msg-p-1", "msg-a-1", "msg-tr-1"],
                            "metering_usage": 0.05703900199004976,
                            "builtin_tool_uses": 1,
                            "selected_model": "claude-sonnet-4.5",
                        }
                    ]
                }
            }
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        jsonl_lines = [
            json.dumps({"kind": "Prompt", "data": {"message_id": "msg-p-1"}}),
            json.dumps({"kind": "AssistantMessage", "data": {"message_id": "msg-a-1", "content": [{"kind": "toolUse", "data": {"toolUseId": "call-acc-1", "name": "read"}}]}}),
            json.dumps({"kind": "ToolResults", "data": {"message_id": "msg-tr-1", "content": [{"kind": "toolResult", "data": {"toolUseId": "call-acc-1", "status": "success"}}]}}),
        ]
        jsonl_file.write_text("\n".join(jsonl_lines) + "\n", encoding="utf-8")

        events = [e for e in self.source.collect() if e.get("event_type") == "ai_run_completed"]
        self.assertEqual(len(events), 1)
        raw_event = events[0]
        telemetry_event = TelemetryEvent.from_dict(raw_event)

        # Process through collector accounting conversion
        op_rec = TelemetryCollector._ai_event_to_operation_record(None, telemetry_event)
        self.assertIsNotNone(op_rec)

        # Exactly 1 parent turn operation
        self.assertEqual(op_rec.operation_id, "turn-acc-1")
        self.assertEqual(op_rec.provider, "kiro_cli")
        self.assertEqual(op_rec.credits_used, Decimal("0.05703900199004976"))
        self.assertEqual(op_rec.deduplication_key, f"kiro_cli:{session_id}:turn-acc-1")

        # Save into accounting store and assert deduplication
        acc_store = AccountingStore(Path(self.temp_dir.name) / "acc_data")
        saved_first = acc_store.save_operation(op_rec)
        self.assertTrue(saved_first)

        # Duplicate save returns False
        saved_second = acc_store.save_operation(op_rec)
        self.assertFalse(saved_second)

        # Store contains exactly 1 operation
        ops = acc_store.get_operations(session_id=session_id)
        self.assertEqual(len(ops), 1)


if __name__ == "__main__":
    unittest.main()

