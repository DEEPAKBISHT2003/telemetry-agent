"""Regression tests for Kiro IDE telemetry accounting and session-duration remediation.

Covers the four remediation areas:
1. IDE session duration is distinct from session activity span.
2. IDE credit accounting coverage: non-billable lifecycle/metadata events are not
   counted as billable operations; measured credits are preserved without
   double-counting; missing credits/tokens remain explicitly unmeasured.
3. Model identification: routing aliases (auto / simple-task / qdev::*) are kept
   as the selection but never reported as the resolved model.
4. Source/provider attribution: Kiro IDE/CLI events display as "kiro", not the
   "antigravity" catch-all, with a working --source filter.

Plus regression protection for Kiro CLI duration/credits, Antigravity, and Jira
attribution engagement. All tests use isolated temporary storage and never touch
the user's real telemetry files.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
import unittest

from ai_telemetry_agent.accounting.aggregator import AccountingAggregator
from ai_telemetry_agent.accounting.models import (
    ExecutionStatus,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
)
from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.attribution.aggregator import TicketAggregator
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.cli import _event_source_label, cmd_events
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import EventType, TelemetryEvent, is_kiro_routing_alias
from ai_telemetry_agent.sources.kiro_cli_sessions import KiroCLISessionSource
from ai_telemetry_agent.sources.kiro_ide_sessions import KiroIDESessionSource
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore


# ---------------------------------------------------------------------------
# Event construction helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_id: str,
    event_type: str,
    timestamp: str,
    session_id: str,
    payload: dict,
    member_id: str = "M001",
    device_id: str = "DEV-1",
    repo_name: str = None,
) -> TelemetryEvent:
    ctx = {"session_id": session_id}
    if repo_name is not None:
        ctx["repository"] = {"name": repo_name}
    return TelemetryEvent.from_dict({
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "collector": {"version": "0.1.2", "device_id": device_id},
        "identity": {"member_id": member_id},
        "context": ctx,
        "payload": payload,
    })


def _ide_completed_payload(
    session_id: str,
    run_id: str,
    completed_at: str,
    duration_ms,
    credits=None,
    model: str = "auto",
    selected_model: str = "auto",
    resolved_model=None,
) -> dict:
    return {
        "provider": "kiro_ide",
        "model": model,
        "session_id": session_id,
        "run_id": run_id,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
        "turns": 1,
        "agent_name": "kiro_ide",
        "capture_method": "messages_jsonl",
        "credits": credits,
        "credit_unit": "credit" if credits is not None else None,
        "context_usage_percentage": None,
        "selected_model": selected_model,
        "resolved_model": resolved_model,
        "tools": [],
        "agents": [],
        "metadata": {"status": "success", "token_status": "unreported_by_backend"},
    }


def _ide_tool_call_payload(session_id: str, tool_call_id: str, tool_name: str = "readFile") -> dict:
    return {
        "provider": "kiro_ide",
        "session_id": session_id,
        "run_id": tool_call_id,
        "tool_name": tool_name,
        "capture_method": "messages_jsonl",
        "metadata": {"tool_call_id": tool_call_id, "status": "success"},
    }


def _ide_context_usage_payload(session_id: str, usage_pct: float = 0.12) -> dict:
    return {
        "provider": "kiro_ide",
        "session_id": session_id,
        "context_usage_percentage": usage_pct,
        "capture_method": "messages_jsonl",
        "metadata": {"key": "contextUsage"},
    }


def _ide_subagent_payload(session_id: str, sub_id: str, is_start: bool = True) -> dict:
    return {
        "provider": "kiro_ide",
        "session_id": sub_id,
        "parent_session_id": session_id,
        "run_id": f"{session_id}_sub_{sub_id}",
        "agent_name": "sub_agent",
        "capture_method": "messages_jsonl",
        "credits": None,
        "metadata": {
            "sub_agent_id": sub_id,
            "parent_execution_id": None,
            "sub_agent_name": "sub_agent",
            "is_subagent": True,
            "lifecycle_action": "sub_agent_start" if is_start else "sub_agent_complete",
        },
    }


def _cli_completed_payload(
    session_id: str,
    run_id: str,
    completed_at: str,
    duration_ms,
    turn_duration_ms=None,
    credits=None,
    model: str = "auto",
    selected_model: str = "auto",
    resolved_model=None,
) -> dict:
    metadata = {
        "turn_index": 0,
        "end_reason": "end_turn",
        "total_request_count": 1,
        "request_attempts": 1,
        "turn_duration_ms": turn_duration_ms,
    }
    return {
        "provider": "kiro_cli",
        "model": model,
        "session_id": session_id,
        "run_id": run_id,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
        "turns": 1,
        "agent_name": "kiro_cli",
        "capture_method": "session_json",
        "credits": credits,
        "credit_unit": "credit" if credits is not None else None,
        "context_usage_percentage": None,
        "selected_model": selected_model,
        "resolved_model": resolved_model,
        "metadata": metadata,
    }


def _antigravity_run_payload(session_id: str, run_id: str, timestamp: str, model_id: str = "claude-sonnet-4.5") -> dict:
    return {
        "provider": "antigravity",
        "model": model_id,
        "session_id": session_id,
        "run_id": run_id,
        "completed_at": timestamp,
        "duration_ms": None,
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_tokens": 500,
        "total_tokens": 2000,
        "cost_usd": None,
        "capture_method": "sqlite_gen_metadata",
        "selected_model": None,
        "resolved_model": None,
        "metadata": {"gen_idx": 0, "reconciled": True},
    }


# ---------------------------------------------------------------------------
# 1. Session duration vs activity span
# ---------------------------------------------------------------------------

class TestSessionDurationSeparation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="telemetry_dur_"))
        self.store = JSONLEventStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, *events):
        for ev in events:
            self.store.save_event(ev)

    def test_long_lived_session_short_operation_duration_not_activity_span(self):
        """A long-lived IDE session with a short completed operation must report
        the operation duration, not the ~99 minute wall-clock activity span."""
        sid = "sess_0b57f9c5-165b-4f9c-81f7-e4b87b128efb"
        # First event at 09:24:00, last at 11:03:24.372 -> span ~5964.37s (~99 min)
        start = _make_event(
            "e_start", EventType.AI_RUN_STARTED, "2026-10-18T09:24:00.000Z", sid,
            {
                "provider": "kiro_ide", "model": "auto", "session_id": sid,
                "capture_method": "ide_session_files",
                "selected_model": "auto", "resolved_model": None,
                "metadata": {"title": "long session"},
            },
        )
        # Short completed operation: duration_ms = 13219.0 ms (~13.22s)
        completed = _make_event(
            "e_completed", EventType.AI_RUN_COMPLETED, "2026-10-18T11:03:24.372Z", sid,
            _ide_completed_payload(sid, "exec-1", "2026-10-18T11:03:24.372Z", 13219.0, credits=0.14591080248756222),
        )
        self._save(start, completed)

        sessions = self.store.get_sessions(session_id=sid, limit=10)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]

        # Activity span is large (~99 minutes) and reported separately.
        self.assertAlmostEqual(s["session_activity_span_seconds"], 5964.37, places=1)
        # Duration reflects the validated operation duration, not the span.
        self.assertEqual(s["duration_seconds"], 13.22)
        self.assertNotEqual(s["duration_seconds"], s["session_activity_span_seconds"])
        # Credits preserved exactly.
        self.assertTrue(s["has_credit_data"])
        self.assertAlmostEqual(s["total_credits"], 0.1459, places=4)

    def test_completed_operation_with_authoritative_duration(self):
        """recorded_turn_duration_seconds must capture the IDE operation duration."""
        sid = "sess_dur_auth"
        completed = _make_event(
            "e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05.000Z", sid,
            _ide_completed_payload(sid, "exec-a", "2026-10-18T10:00:05.000Z", 4783.0, credits=0.01),
        )
        self._save(completed)
        s = self.store.get_sessions(session_id=sid, limit=1)[0]
        self.assertEqual(s["recorded_turn_duration_seconds"], 4.783)
        self.assertEqual(s["duration_seconds"], 4.78)

    def test_operation_with_unknown_duration(self):
        """When duration_ms is unavailable, duration_seconds must be None (unknown),
        not a fabricated value nor an unsupported zero; the activity span is still reported."""
        sid = "sess_dur_unknown"
        start = _make_event(
            "e_start", EventType.AI_RUN_STARTED, "2026-10-18T10:00:00.000Z", sid,
            {"provider": "kiro_ide", "model": "auto", "session_id": sid,
             "capture_method": "ide_session_files", "metadata": {}},
        )
        # Completion with no duration_ms (None)
        completed = _make_event(
            "e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:02:00.000Z", sid,
            _ide_completed_payload(sid, "exec-u", "2026-10-18T10:02:00.000Z", None, credits=0.02),
        )
        self._save(start, completed)
        s = self.store.get_sessions(session_id=sid, limit=1)[0]
        self.assertIsNone(s["duration_seconds"])
        self.assertGreater(s["session_activity_span_seconds"], 0)

    def test_multiple_completed_operations_sum_durations(self):
        """Multiple completed operations in one session sum their validated durations."""
        sid = "sess_multi"
        c1 = _make_event("e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05.000Z", sid,
                         _ide_completed_payload(sid, "exec-1", "2026-10-18T10:00:05.000Z", 13219.0, credits=0.01))
        c2 = _make_event("e2", EventType.AI_RUN_COMPLETED, "2026-10-18T10:05:00.000Z", sid,
                         _ide_completed_payload(sid, "exec-2", "2026-10-18T10:05:00.000Z", 5000.0, credits=0.02))
        self._save(c1, c2)
        s = self.store.get_sessions(session_id=sid, limit=1)[0]
        self.assertEqual(s["recorded_turn_duration_seconds"], 18.219)
        self.assertEqual(s["duration_seconds"], 18.22)
        self.assertEqual(s["completed_runs_count"], 2)

    def test_duplicate_completion_events_do_not_double_count(self):
        """Duplicate ai_run_completed records (same run_id) must not inflate
        accumulated duration, completed-run counts, or session credits."""
        sid = "sess_dup"
        c1 = _make_event("e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05.000Z", sid,
                         _ide_completed_payload(sid, "exec-dup", "2026-10-18T10:00:05.000Z", 13219.0, credits=0.1459))
        # Same run_id, duplicated (e.g. after a cursor reset)
        c2 = _make_event("e2", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:06.000Z", sid,
                         _ide_completed_payload(sid, "exec-dup", "2026-10-18T10:00:06.000Z", 13219.0, credits=0.1459))
        self._save(c1, c2)
        s = self.store.get_sessions(session_id=sid, limit=1)[0]
        self.assertEqual(s["recorded_turn_duration_seconds"], 13.219)
        self.assertEqual(s["duration_seconds"], 13.22)
        self.assertEqual(s["completed_runs_count"], 1)
        # Credits counted once, not doubled.
        self.assertAlmostEqual(s["total_credits"], 0.1459, places=4)


# ---------------------------------------------------------------------------
# 2. Credit accounting coverage
# ---------------------------------------------------------------------------

class TestAccountingCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="telemetry_acc_"))
        self.store = AccountingStore(self.tmp)
        self.aggregator = AccountingAggregator(self.store)
        self.collector = TelemetryCollector()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _convert_and_save(self, ev) -> None:
        op = self.collector._ai_event_to_operation_record(ev)
        if op is not None:
            self.store.save_operation(op)

    def test_ide_turn_yields_two_operations_not_four(self):
        """A single IDE turn emits tool_call, contextUsage, completion, and
        subagent events. Only the completion (measured) and subagent (unmeasured)
        must become accounting operations; tool_call and contextUsage must not."""
        sid = "sess_ide_turn"
        events = [
            _make_event("e_tool", EventType.AI_TOOL_CALL, "2026-10-18T10:00:01Z", sid,
                        _ide_tool_call_payload(sid, "tc-1")),
            _make_event("e_ctx", EventType.AI_TURN, "2026-10-18T10:00:02Z", sid,
                        _ide_context_usage_payload(sid, 0.12)),
            _make_event("e_done", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid,
                        _ide_completed_payload(sid, "exec-1", "2026-10-18T10:00:05Z", 13219.0, credits=0.14591080248756222)),
            _make_event("e_sub", EventType.AGENT_STARTED, "2026-10-18T10:00:03Z", "sub-1",
                        _ide_subagent_payload(sid, "sub-1", is_start=True)),
        ]
        for ev in events:
            self._convert_and_save(ev)

        summary = self.aggregator.get_summary()
        # Only completion + subagent become operations (not tool_call, not contextUsage).
        self.assertEqual(summary["total_operations_count"], 2)
        self.assertEqual(summary["measured_operations_count"], 1)
        self.assertEqual(summary["unmeasured_operations_count"], 1)
        # Measured credits preserved exactly, not double-counted.
        self.assertEqual(summary["total_measured_credits_exact"], "0.14591080248756222")

    def test_ide_tool_call_not_an_accounting_operation(self):
        """An IDE tool call with missing credit information must not become a
        billable accounting operation (it is a component of the turn)."""
        sid = "sess_tool_only"
        ev = _make_event("e_tool", EventType.AI_TOOL_CALL, "2026-10-18T10:00:01Z", sid,
                         _ide_tool_call_payload(sid, "tc-1"))
        op = self.collector._ai_event_to_operation_record(ev)
        self.assertIsNone(op)

    def test_ide_context_usage_not_an_accounting_operation(self):
        """An IDE context-usage metadata update must not be misclassified as an
        interactive prompt operation."""
        sid = "sess_ctx_only"
        ev = _make_event("e_ctx", EventType.AI_TURN, "2026-10-18T10:00:02Z", sid,
                         _ide_context_usage_payload(sid, 0.12))
        op = self.collector._ai_event_to_operation_record(ev)
        self.assertIsNone(op)

    def test_ide_completion_measured_credits_unreported_tokens(self):
        """A completion with measured credits and unreported token counts must be
        MEASURED with credits preserved exactly and token_usage None."""
        sid = "sess_done"
        ev = _make_event("e_done", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid,
                         _ide_completed_payload(sid, "exec-1", "2026-10-18T10:00:05Z", 13219.0, credits=0.14591080248756222))
        op = self.collector._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.provider, "kiro_ide")
        self.assertEqual(op.operation_type, OperationType.PROMPT)
        self.assertEqual(op.trigger_type, TriggerType.USER_INITIATED)
        self.assertEqual(op.measurement_status, MeasurementStatus.MEASURED)
        self.assertEqual(op.credits_used, Decimal("0.14591080248756222"))
        # Tokens explicitly unmeasured (unreported by backend), not fabricated zeros.
        self.assertIsNone(op.token_usage)

    def test_ide_subagent_unmeasured_preserved(self):
        """Subagent lifecycle events are retained as legitimate unmeasured
        operations (credits bundled in the parent turn), preserving the
        interactive/automated/subagent distinction."""
        sid = "sess_sub"
        ev = _make_event("e_sub", EventType.AGENT_STARTED, "2026-10-18T10:00:03Z", "sub-1",
                        _ide_subagent_payload(sid, "sub-1", is_start=True))
        op = self.collector._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.operation_type, OperationType.SUBAGENT)
        self.assertEqual(op.trigger_type, TriggerType.AGENT_SPAWNED)
        self.assertIsNone(op.credits_used)
        self.assertEqual(op.measurement_status, MeasurementStatus.UNAVAILABLE)

    def test_completion_credits_not_double_counted_across_event_types(self):
        """The completion's credits must be counted exactly once even when the
        turn also emits tool_call and contextUsage events."""
        sid = "sess_no_double"
        events = [
            _make_event("e_tool", EventType.AI_TOOL_CALL, "2026-10-18T10:00:01Z", sid, _ide_tool_call_payload(sid, "tc-1")),
            _make_event("e_ctx", EventType.AI_TURN, "2026-10-18T10:00:02Z", sid, _ide_context_usage_payload(sid, 0.12)),
            _make_event("e_done", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid,
                        _ide_completed_payload(sid, "exec-1", "2026-10-18T10:00:05Z", 13219.0, credits=0.5)),
        ]
        for ev in events:
            self._convert_and_save(ev)
        summary = self.aggregator.get_summary()
        self.assertEqual(summary["total_operations_count"], 1)
        self.assertEqual(summary["total_measured_credits_exact"], "0.5")

    def test_ide_prompt_missing_credits_is_unmeasured(self):
        """An IDE completion with no credit value remains explicitly unmeasured
        (credits None), not a fabricated zero."""
        sid = "sess_no_credit"
        ev = _make_event("e_done", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid,
                         _ide_completed_payload(sid, "exec-1", "2026-10-18T10:00:05Z", 1000.0, credits=None))
        op = self.collector._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertIsNone(op.credits_used)
        self.assertEqual(op.measurement_status, MeasurementStatus.UNAVAILABLE)


# ---------------------------------------------------------------------------
# 3. Model identification
# ---------------------------------------------------------------------------

class TestModelIdentification(unittest.TestCase):
    def test_routing_alias_detection(self):
        self.assertTrue(is_kiro_routing_alias("auto"))
        self.assertTrue(is_kiro_routing_alias("AUTO"))
        self.assertTrue(is_kiro_routing_alias("simple-task"))
        self.assertTrue(is_kiro_routing_alias("qdev::auto"))
        self.assertTrue(is_kiro_routing_alias("qdev::something"))
        self.assertFalse(is_kiro_routing_alias("anthropic.claude-sonnet-4-5"))
        self.assertFalse(is_kiro_routing_alias("claude-haiku-4.5"))
        self.assertFalse(is_kiro_routing_alias(None))
        self.assertFalse(is_kiro_routing_alias(""))

    def test_ide_source_auto_model_unresolved(self):
        """IDE session.json with modelId='auto' yields selected_model='auto',
        resolved_model=None across the start and completion events."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_model_ide_"))
        try:
            sessions_dir = tmp / "ide"
            state_file = tmp / "state.json"
            src = KiroIDESessionSource(sessions_dir=sessions_dir, state_file=state_file)
            src.start()
            sid = "ide-auto"
            sd = sessions_dir / "wh" / sid
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "session.json").write_text(json.dumps({
                "id": sid, "createdAt": "2026-10-18T09:24:00Z",
                "modelId": "auto", "workspacePaths": [],
            }), encoding="utf-8")
            (sd / "messages.jsonl").write_text(json.dumps({
                "type": "turn_completed", "timestamp": "2026-10-18T09:24:10Z",
                "credits": 0.01, "elapsedTime": 1000,
            }) + "\n", encoding="utf-8")
            events = src.collect()
            src.stop()

            for ev in events:
                p = ev.get("payload", {})
                if p.get("capture_method") in ("ide_session_files", "messages_jsonl"):
                    self.assertEqual(p.get("selected_model"), "auto")
                    self.assertIsNone(p.get("resolved_model"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ide_source_routing_alias_not_resolved(self):
        """A non-'auto' routing alias (simple-task) must also yield resolved_model=None
        on the session-started event while preserving the alias as selected_model."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_model_ide_alias_"))
        try:
            sessions_dir = tmp / "ide"
            state_file = tmp / "state.json"
            src = KiroIDESessionSource(sessions_dir=sessions_dir, state_file=state_file)
            src.start()
            sid = "ide-alias"
            sd = sessions_dir / "wh" / sid
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "session.json").write_text(json.dumps({
                "id": sid, "createdAt": "2026-10-18T09:24:00Z",
                "modelId": "simple-task", "workspacePaths": [],
            }), encoding="utf-8")
            events = src.collect()
            src.stop()
            start = [e for e in events if e.get("event_type") == EventType.AI_RUN_STARTED][0]
            self.assertEqual(start["payload"]["selected_model"], "simple-task")
            self.assertIsNone(start["payload"]["resolved_model"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ide_source_concrete_model_resolved(self):
        """When modelId is a concrete model identifier, it is preserved as resolved_model."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_model_ide_concrete_"))
        try:
            sessions_dir = tmp / "ide"
            state_file = tmp / "state.json"
            src = KiroIDESessionSource(sessions_dir=sessions_dir, state_file=state_file)
            src.start()
            sid = "ide-concrete"
            sd = sessions_dir / "wh" / sid
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "session.json").write_text(json.dumps({
                "id": sid, "createdAt": "2026-10-18T09:24:00Z",
                "modelId": "anthropic.claude-sonnet-4-5", "workspacePaths": [],
            }), encoding="utf-8")
            events = src.collect()
            src.stop()
            start = [e for e in events if e.get("event_type") == EventType.AI_RUN_STARTED][0]
            self.assertEqual(start["payload"]["selected_model"], "anthropic.claude-sonnet-4-5")
            self.assertEqual(start["payload"]["resolved_model"], "anthropic.claude-sonnet-4-5")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_source_auto_model_unresolved(self):
        """CLI turn with selected_model='auto' yields selected_model='auto', resolved_model=None."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_model_cli_auto_"))
        try:
            sessions_dir = tmp / "cli"
            state_file = tmp / "state.json"
            src = KiroCLISessionSource(sessions_dir=sessions_dir, state_file=state_file)
            src.start()
            sid = "cli-auto"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            (sessions_dir / f"{sid}.json").write_text(json.dumps({
                "session_state": {
                    "id": sid, "created_at": "2026-10-14T05:10:00Z",
                    "conversation_metadata": {"user_turn_metadatas": [
                        {"result": {"Ok": {"id": "turn-auto", "user_turn_metadata": {
                            "turn_duration_ms": 900, "input_token_count": 0,
                            "output_token_count": 0, "metering_usage": 0.002,
                        }}}, "timestamp": "2026-10-14T05:10:01Z", "selected_model": "auto"},
                    ]},
                },
            }), encoding="utf-8")
            events = [e for e in src.collect() if e.get("event_type") == EventType.AI_RUN_COMPLETED]
            src.stop()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["selected_model"], "auto")
            self.assertIsNone(events[0]["payload"]["resolved_model"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_source_routing_alias_not_resolved(self):
        """CLI turn with a non-'auto' routing alias (simple-task) yields resolved_model=None."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_model_cli_alias_"))
        try:
            sessions_dir = tmp / "cli"
            state_file = tmp / "state.json"
            src = KiroCLISessionSource(sessions_dir=sessions_dir, state_file=state_file)
            src.start()
            sid = "cli-alias"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            (sessions_dir / f"{sid}.json").write_text(json.dumps({
                "session_state": {
                    "id": sid, "created_at": "2026-10-14T05:10:00Z",
                    "conversation_metadata": {"user_turn_metadatas": [
                        {"result": {"Ok": {"id": "turn-alias", "user_turn_metadata": {
                            "turn_duration_ms": 900, "input_token_count": 0,
                            "output_token_count": 0, "metering_usage": 0.002,
                        }}}, "timestamp": "2026-10-14T05:10:01Z", "selected_model": "simple-task"},
                    ]},
                },
            }), encoding="utf-8")
            events = [e for e in src.collect() if e.get("event_type") == EventType.AI_RUN_COMPLETED]
            src.stop()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["selected_model"], "simple-task")
            self.assertIsNone(events[0]["payload"]["resolved_model"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_converter_normalizes_historical_alias_resolved_model(self):
        """A historical record carrying resolved_model='simple-task' is normalized
        to None by the converter on read (no historical rewrite required)."""
        sid = "sess_hist_model"
        ev = _make_event("e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid, {
            "provider": "kiro_ide", "model": "simple-task", "session_id": sid,
            "run_id": "exec-1", "capture_method": "messages_jsonl",
            "credits": 0.01, "selected_model": "simple-task",
            "resolved_model": "simple-task",  # stale/legacy value
            "metadata": {},
        })
        op = TelemetryCollector()._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.model_requested, "simple-task")
        self.assertIsNone(op.model_resolved)


# ---------------------------------------------------------------------------
# 4. Source / provider attribution
# ---------------------------------------------------------------------------

class TestSourceAttribution(unittest.TestCase):
    def test_kiro_ide_session_file_events_labeled_kiro(self):
        """IDE session-file events (capture_method=messages_jsonl) must display as
        'kiro', not 'antigravity', despite their ai_* event types."""
        sid = "sess_src_ide"
        cases = [
            (EventType.AI_RUN_STARTED, "ide_session_files"),
            (EventType.AI_RUN_COMPLETED, "messages_jsonl"),
            (EventType.AI_TOOL_CALL, "messages_jsonl"),
            (EventType.AI_TURN, "messages_jsonl"),
        ]
        for etype, cm in cases:
            ev = _make_event(f"e_{etype}", etype, "2026-10-18T10:00:00Z", sid, {
                "provider": "kiro_ide", "session_id": sid, "capture_method": cm,
                "model": "auto", "metadata": {},
            })
            self.assertEqual(_event_source_label(ev), "kiro", f"{etype}/{cm} should be kiro")

    def test_kiro_cli_session_file_events_labeled_kiro(self):
        """CLI session-file events (capture_method=session_json) must display as 'kiro'."""
        sid = "sess_src_cli"
        ev = _make_event("e_cli", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:00Z", sid, {
            "provider": "kiro_cli", "session_id": sid, "capture_method": "session_json",
            "model": "auto", "metadata": {},
        })
        self.assertEqual(_event_source_label(ev), "kiro")

    def test_antigravity_events_labeled_antigravity(self):
        """Antigravity SQLite generation events must remain labeled 'antigravity'."""
        sid = "sess_src_ag"
        ev = _make_event("e_ag", EventType.AI_RUN, "2026-10-18T10:00:00Z", sid,
                         _antigravity_run_payload(sid, "ag-1", "2026-10-18T10:00:00Z"))
        self.assertEqual(_event_source_label(ev), "antigravity")

    def test_git_and_system_labels_preserved(self):
        ev_git = _make_event("e_g", "git_commit_detected", "2026-10-18T10:00:00Z", "s", {
            "repository": "repo", "branch": "main", "capture_method": None,
        })
        self.assertEqual(_event_source_label(ev_git), "git")
        ev_sys = _make_event("e_sys", EventType.COLLECTOR_HEARTBEAT, "2026-10-18T10:00:00Z", "s", {
            "status": "ok", "uptime_seconds": 5,
        })
        self.assertEqual(_event_source_label(ev_sys), "system")

    def test_events_command_source_filter(self):
        """The events CLI --source filter must correctly partition Kiro IDE vs
        Antigravity events, and Kiro IDE events must render with SOURCE=kiro."""
        tmp = Path(tempfile.mkdtemp(prefix="telemetry_cli_"))
        old_data_dir = os.environ.get("TELEMETRY_DATA_DIR")
        try:
            os.environ["TELEMETRY_DATA_DIR"] = str(tmp)
            store = JSONLEventStore(tmp)
            sid_ide = "sess_cli_ide"
            sid_ag = "sess_cli_ag"
            store.save_event(_make_event("e_ide", EventType.AI_RUN_COMPLETED,
                                          "2026-10-18T10:00:05Z", sid_ide,
                                          _ide_completed_payload(sid_ide, "exec-1", "2026-10-18T10:00:05Z", 1000.0, credits=0.03)))
            store.save_event(_make_event("e_ag", EventType.AI_RUN,
                                          "2026-10-18T10:00:06Z", sid_ag,
                                          _antigravity_run_payload(sid_ag, "ag-1", "2026-10-18T10:00:06Z")))

            args = argparse.Namespace(
                env_file=None, last=50, type=None, member=None, repo=None,
                session=None, source="kiro", asc=False, json=False,
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_events(args)
            out = buf.getvalue()
            self.assertIn("kiro", out)
            self.assertNotIn("antigravity", out)

            args.source = "antigravity"
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                cmd_events(args)
            out2 = buf2.getvalue()
            self.assertIn("antigravity", out2)
            self.assertNotIn("sess_cli_ide", out2)
        finally:
            if old_data_dir is None:
                os.environ.pop("TELEMETRY_DATA_DIR", None)
            else:
                os.environ["TELEMETRY_DATA_DIR"] = old_data_dir
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Historical JSONL compatibility + regression protection
# ---------------------------------------------------------------------------

class TestHistoricalAndRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="telemetry_hist_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_historical_record_missing_duration_fields(self):
        """A historical completion with no duration_ms must yield duration_seconds=None
        while still reporting the activity span, and must not crash."""
        store = JSONLEventStore(self.tmp)
        sid = "sess_hist"
        # Minimal historical record: no duration_ms, no metadata.turn_duration_ms.
        store.save_event(_make_event("e1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid, {
            "provider": "kiro_ide", "session_id": sid, "run_id": "exec-1",
            "capture_method": "messages_jsonl", "credits": None,
            "selected_model": None, "resolved_model": None, "metadata": {},
        }))
        s = store.get_sessions(session_id=sid, limit=1)[0]
        self.assertIsNone(s["duration_seconds"])
        self.assertGreaterEqual(s["session_activity_span_seconds"], 0)

    def test_historical_record_partial_fields_no_crash(self):
        """A historical record with only a subset of fields must not crash get_sessions."""
        store = JSONLEventStore(self.tmp)
        # Raw JSONL line with minimal/partial fields (no model, no credits, no run_id).
        fpath = self.tmp / "telemetry-2026-10-18.jsonl"
        fpath.write_text(json.dumps({
            "event_id": "raw-1", "event_type": "ai_run_completed",
            "timestamp": "2026-10-18T10:00:05Z",
            "collector": {"version": "0.1.2", "device_id": "DEV-1"},
            "identity": {"member_id": "M1"},
            "context": {"session_id": "sess_partial"},
            "payload": {"provider": "kiro_ide", "capture_method": "messages_jsonl"},
        }) + "\n", encoding="utf-8")
        sessions = store.get_sessions(limit=50)
        self.assertTrue(any(s["session_id"] == "sess_partial" for s in sessions))
        s = [s for s in sessions if s["session_id"] == "sess_partial"][0]
        self.assertIsNone(s["duration_seconds"])

    def test_kiro_cli_duration_metadata_fallback_preserved(self):
        """Regression: CLI duration must use metadata.turn_duration_ms then fall back
        to payload.duration_ms. Both paths must accumulate correctly."""
        store = JSONLEventStore(self.tmp)
        sid = "sess_cli_dur"
        # metadata.turn_duration_ms present (primary path)
        c1 = _make_event("c1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:02Z", sid,
                         _cli_completed_payload(sid, "t1", "2026-10-18T10:00:02Z", 20000.0,
                                                 turn_duration_ms=20000.0, credits=0.01))
        # metadata.turn_duration_ms absent -> fall back to payload.duration_ms
        c2 = _make_event("c2", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:05Z", sid,
                         _cli_completed_payload(sid, "t2", "2026-10-18T10:00:05Z", 15000.0,
                                                 turn_duration_ms=None, credits=0.02))
        store.save_event(c1)
        store.save_event(c2)
        s = store.get_sessions(session_id=sid, limit=1)[0]
        self.assertEqual(s["recorded_turn_duration_seconds"], 35.0)
        self.assertEqual(s["duration_seconds"], 35.0)
        self.assertAlmostEqual(s["total_credits"], 0.03, places=4)

    def test_kiro_cli_credit_accounting_preserved(self):
        """Regression: CLI metering_usage credits remain MEASURED in the accounting ledger."""
        acc_store = AccountingStore(self.tmp / "acc")
        agg = AccountingAggregator(acc_store)
        collector = TelemetryCollector()
        sid = "sess_cli_credit"
        ev = _make_event("c1", EventType.AI_RUN_COMPLETED, "2026-10-18T10:00:02Z", sid,
                         _cli_completed_payload(sid, "t1", "2026-10-18T10:00:02Z", 20000.0,
                                                 turn_duration_ms=20000.0, credits=0.05703900199004976,
                                                 model="claude-haiku-4.5", selected_model="claude-haiku-4.5",
                                                 resolved_model="claude-haiku-4.5"))
        op = collector._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.provider, "kiro_cli")
        self.assertEqual(op.credits_used, Decimal("0.05703900199004976"))
        self.assertEqual(op.measurement_status, MeasurementStatus.MEASURED)
        self.assertEqual(op.model_requested, "claude-haiku-4.5")
        self.assertEqual(op.model_resolved, "claude-haiku-4.5")
        acc_store.save_operation(op)
        summary = agg.get_summary()
        self.assertEqual(summary["total_measured_credits_exact"], "0.05703900199004976")

    def test_antigravity_accounting_preserved(self):
        """Regression: Antigravity SQLite generation events still produce a token-metered
        QUERY operation (credits None / unavailable), unaffected by the IDE-only skip."""
        acc_store = AccountingStore(self.tmp / "acc_ag")
        collector = TelemetryCollector()
        sid = "sess_ag_reg"
        ev = _make_event("ag1", EventType.AI_RUN, "2026-10-18T10:00:00Z", sid,
                         _antigravity_run_payload(sid, "ag-1", "2026-10-18T10:00:00Z"))
        op = collector._ai_event_to_operation_record(ev)
        self.assertIsNotNone(op)
        self.assertEqual(op.provider, "antigravity")
        self.assertEqual(op.operation_type, OperationType.QUERY)
        self.assertIsNone(op.credits_used)
        self.assertEqual(op.measurement_status, MeasurementStatus.UNAVAILABLE)
        # Tokens are authoritative for Antigravity (not fabricated, not None).
        self.assertIsNotNone(op.token_usage)
        self.assertEqual(op.token_usage["input_tokens"], 1200)

    def test_jira_attribution_engagement_uses_activity_span(self):
        """Regression: Jira ticket engagement must use the session activity span, not
        the short validated AI operation duration; credits remain preserved."""
        store = JSONLEventStore(self.tmp / "data")
        attr_store = AttributionStore(self.tmp / "attr")
        sid = "sess_jira_eng"
        start = _make_event("e_start", EventType.AI_RUN_STARTED, "2026-10-18T09:24:00.000Z", sid, {
            "provider": "kiro_ide", "model": "auto", "session_id": sid,
            "capture_method": "ide_session_files", "metadata": {},
        })
        completed = _make_event("e_done", EventType.AI_RUN_COMPLETED, "2026-10-18T11:03:24.372Z", sid,
                                _ide_completed_payload(sid, "exec-1", "2026-10-18T11:03:24.372Z", 13219.0, credits=0.1459))
        store.save_event(start)
        store.save_event(completed)

        agg = TicketAggregator(store, attr_store)
        summaries = agg.get_ticket_summaries()
        unassigned = summaries.get("UNASSIGNED")
        self.assertIsNotNone(unassigned)
        # Engagement reflects the ~99 minute activity span, not the 13.22s operation.
        self.assertGreater(unassigned.duration_seconds, 5000)
        self.assertNotAlmostEqual(unassigned.duration_seconds, 13.22, places=1)
        # Credits preserved.
        self.assertAlmostEqual(unassigned.total_credits, 0.1459, places=4)


if __name__ == "__main__":
    unittest.main()
