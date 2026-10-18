"""Deterministic Test Suite for AI Credit Accounting.

Verifies all 18 required accounting scenarios across Kiro CLI, Kiro IDE, Antigravity,
agent lifecycle hooks, subagents, retries, and Jira ticket attribution.
"""

from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

from ai_telemetry_agent.accounting.adapters import (
    AntigravityAccountingAdapter,
    KiroCLIAccountingAdapter,
    KiroIDEAccountingAdapter,
)
from ai_telemetry_agent.accounting.aggregator import AccountingAggregator
from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    ExecutionStatus,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
    quantize_credits,
    sum_credits,
    to_decimal,
)
from ai_telemetry_agent.accounting.registry import HookRegistry
from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.attribution.models import TicketAttribution
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.config.settings import Settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import EventType, TelemetryEvent
from ai_telemetry_agent.sources.kiro_cli_sessions import KiroCLISessionSource


class TestAICreditAccounting(unittest.TestCase):
    """Phase 7 comprehensive test suite verifying 18 deterministic accounting scenarios."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="telemetry_test_acc_"))
        self.data_dir = self.test_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = AccountingStore(self.data_dir)
        self.aggregator = AccountingAggregator(self.store)
        self.registry = HookRegistry(self.data_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # 1. Normal user prompt with authoritative credit delta
    def test_01_normal_prompt_authoritative_credit(self):
        rec = AIOperationRecord(
            operation_id="turn_001",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_100",
            credits_used=Decimal("0.05703900199004976"),
            credit_source="kiro_cli_session_json:metering_usage",
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            jira_key="SCRUM-1",
            deduplication_key="kiro_cli:sess_100:turn_001",
        )
        saved = self.store.save_operation(rec)
        self.assertTrue(saved)

        fetched = self.store.get_operation("turn_001")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.credits_used, Decimal("0.05703900199004976"))
        self.assertEqual(fetched.measurement_status, MeasurementStatus.MEASURED)

        summary = self.aggregator.get_summary()
        self.assertEqual(summary["total_operations_count"], 1)
        self.assertEqual(summary["total_measured_credits_exact"], "0.05703900199004976")

    # 2. Multiple user prompts within a single session
    def test_02_multiple_prompts_single_session(self):
        turn1 = AIOperationRecord(
            operation_id="sess_100_turn_1",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_100",
            credits_used=Decimal("0.050000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_cli:sess_100:turn_1",
        )
        turn2 = AIOperationRecord(
            operation_id="sess_100_turn_2",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_100",
            credits_used=Decimal("0.075000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_cli:sess_100:turn_2",
        )
        self.store.save_operation(turn1)
        self.store.save_operation(turn2)

        summary = self.aggregator.get_summary()
        self.assertEqual(summary["total_operations_count"], 2)
        self.assertEqual(summary["total_measured_credits_exact"], "0.125000")

    # 3. Manually triggered agent hook execution
    def test_03_manual_agent_hook(self):
        hook_exec = self.registry.record_execution(
            hook_id="hook_prompt_submit",
            hook_name="UserPromptSubmit",
            trigger_type=TriggerType.MANUAL,
            provider="kiro_ide",
            session_id="sess_hook_01",
            is_shell_only=False,
            initiates_ai=True,
            credits_used=None,
            measurement_status=MeasurementStatus.UNAVAILABLE,
        )
        self.assertEqual(hook_exec.trigger_type, TriggerType.MANUAL)
        self.assertTrue(hook_exec.initiates_ai)
        self.assertFalse(hook_exec.is_shell_only)
        self.assertEqual(hook_exec.measurement_status, MeasurementStatus.UNAVAILABLE)

    # 4. Automatically triggered agent hook execution
    def test_04_automatically_triggered_agent_hook(self):
        hook_exec = self.registry.record_execution(
            hook_id="hook_file_save_review",
            hook_name="PostFileSave",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_auto_01",
            is_shell_only=False,
            initiates_ai=True,
            credits_used=Decimal("0.035000"),
            measurement_status=MeasurementStatus.MEASURED,
            metadata={"event_type": "file_saved", "file_name": "app.py"},
        )
        self.assertEqual(hook_exec.trigger_type, TriggerType.EVENT_TRIGGERED)
        self.assertEqual(hook_exec.credits_used, Decimal("0.035000"))
        self.assertEqual(hook_exec.measurement_status, MeasurementStatus.MEASURED)

    # 5. Scheduled agent hook execution with observable schedule metadata
    def test_05_scheduled_agent_hook_with_metadata(self):
        reg = self.registry.register_hook(
            hook_id="hook_nightly_audit",
            hook_name="NightlyCodeAudit",
            trigger_type=TriggerType.HOOK_SCHEDULED,
            schedule_expression="0 2 * * *",
            is_ai_powered=True,
            provider="kiro_ide",
        )
        self.assertEqual(reg.schedule_expression, "0 2 * * *")
        self.assertTrue(reg.is_ai_powered)

        exec_rec = self.registry.record_execution(
            hook_id=reg.hook_id,
            hook_name=reg.hook_name,
            trigger_type=TriggerType.HOOK_SCHEDULED,
            provider="kiro_ide",
            schedule_expression="0 2 * * *",
            credits_used=Decimal("0.120000"),
            measurement_status=MeasurementStatus.MEASURED,
        )
        self.assertEqual(exec_rec.schedule_expression, "0 2 * * *")
        self.assertEqual(exec_rec.credits_used, Decimal("0.120000"))

    # 6. Shell-only hook execution consuming zero AI credits
    def test_06_shell_only_hook_zero_credits(self):
        hook_exec = self.registry.record_execution(
            hook_id="hook_git_precommit",
            hook_name="GitPreCommitLinter",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="system",
            is_shell_only=True,
            initiates_ai=False,
        )
        self.assertTrue(hook_exec.is_shell_only)
        self.assertFalse(hook_exec.initiates_ai)
        self.assertEqual(hook_exec.credits_used, Decimal("0"))
        self.assertEqual(hook_exec.measurement_status, MeasurementStatus.MEASURED)

    # 7. Agent hook execution whose credit consumption cannot be measured independently
    def test_07_unmeasurable_hook_credits(self):
        hook_exec = self.registry.record_execution(
            hook_id="hook_prompt_action",
            hook_name="OnPromptAction",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            is_shell_only=False,
            initiates_ai=True,
            credits_used=None,
            measurement_status=MeasurementStatus.UNAVAILABLE,
        )
        self.assertIsNone(hook_exec.credits_used)
        self.assertEqual(hook_exec.measurement_status, MeasurementStatus.UNAVAILABLE)

    # 8. Spawned agent or subagent execution correlated with its parent session/operation
    def test_08_subagent_parent_correlation(self):
        parent_op = AIOperationRecord(
            operation_id="parent_op_001",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="main_sess_01",
            credits_used=Decimal("0.040000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_ide:main_sess_01:parent_op_001",
        )
        sub_op = AIOperationRecord(
            operation_id="sub_op_001",
            parent_operation_id="parent_op_001",
            parent_session_id="main_sess_01",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.SUBAGENT,
            trigger_type=TriggerType.AGENT_SPAWNED,
            session_id="sub_sess_01",
            credits_used=Decimal("0.085000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_ide:sub_sess_01:sub_op_001",
        )
        self.store.save_operation(parent_op)
        self.store.save_operation(sub_op)

        fetched_sub = self.store.get_operation("sub_op_001")
        self.assertIsNotNone(fetched_sub)
        self.assertEqual(fetched_sub.parent_operation_id, "parent_op_001")
        self.assertEqual(fetched_sub.parent_session_id, "main_sess_01")
        self.assertEqual(fetched_sub.operation_type, OperationType.SUBAGENT)

        summary = self.aggregator.get_summary()
        self.assertEqual(summary["subagent_expenditure"]["credits_exact"], "0.085000")
        self.assertEqual(summary["subagent_expenditure"]["count"], 1)

    # 9. Failed and retried operations, verifying cost is captured despite failure
    def test_09_failed_and_retried_operations(self):
        failed_op = AIOperationRecord(
            operation_id="op_fail_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_retry_01",
            credits_used=Decimal("0.020000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            execution_status=ExecutionStatus.FAILURE,
            deduplication_key="kiro_cli:sess_retry_01:op_fail_01",
            metadata={"end_reason": "error"},
        )
        retry_op = AIOperationRecord(
            operation_id="op_retry_02",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.RETRY,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_retry_01",
            credits_used=Decimal("0.035000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            execution_status=ExecutionStatus.SUCCESS,
            deduplication_key="kiro_cli:sess_retry_01:op_retry_02",
            metadata={"is_retry": True, "request_attempts": 2},
        )
        self.store.save_operation(failed_op)
        self.store.save_operation(retry_op)

        summary = self.aggregator.get_summary()
        self.assertEqual(summary["failed_and_retried"]["failed"]["credits_exact"], "0.020000")
        self.assertEqual(summary["failed_and_retried"]["failed"]["count"], 1)
        self.assertEqual(summary["failed_and_retried"]["retried"]["credits_exact"], "0.035000")
        self.assertEqual(summary["failed_and_retried"]["retried"]["count"], 1)
        # Total expenditure reflects both attempts
        self.assertEqual(summary["total_measured_credits_exact"], "0.055000")

    # 10. Duplicate polling and repeated event ingestion (idempotent deduplication_key)
    def test_10_deduplication_and_idempotence(self):
        rec = AIOperationRecord(
            operation_id="op_dup_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_dup_01",
            credits_used=Decimal("0.050000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_cli:sess_dup_01:op_dup_01",
        )
        # First save
        first = self.store.save_operation(rec)
        self.assertTrue(first)

        # Re-polling same turn: deduplicated, returns False
        second = self.store.save_operation(rec)
        self.assertFalse(second)

        ops = self.store.get_operations(session_id="sess_dup_01")
        self.assertEqual(len(ops), 1)

        summary = self.aggregator.get_summary()
        self.assertEqual(summary["total_operations_count"], 1)
        self.assertEqual(summary["total_measured_credits_exact"], "0.050000")

    # 11. Session-level usage totals arriving after individual operation records
    def test_11_session_totals_do_not_double_count_operations(self):
        op1 = AIOperationRecord(
            operation_id="op_hier_01",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_hier_01",
            credits_used=Decimal("0.030000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_ide:sess_hier_01:op_hier_01",
        )
        op2 = AIOperationRecord(
            operation_id="op_hier_02",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_hier_01",
            credits_used=Decimal("0.070000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_ide:sess_hier_01:op_hier_02",
        )
        self.store.save_operation(op1)
        self.store.save_operation(op2)

        # Later, session-level summary arrives claiming 0.100000 total session credits
        sess_rec = AIOperationRecord(
            operation_id="sess_hier_01_summary",
            provider="kiro_ide",
            source="session_summary",
            operation_type=OperationType.SESSION_SUMMARY,
            trigger_type=TriggerType.AUTOMATED,
            session_id="sess_hier_01",
            credits_used=Decimal("0.100000"),
            measurement_scope=MeasurementScope.SESSION,  # Session scope!
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_ide:sess_hier_01:summary",
        )
        self.store.save_operation(sess_rec)

        summary = self.aggregator.get_summary()
        # Must only sum operation scope: 0.030000 + 0.070000 = 0.100000 (NOT 0.200000)
        self.assertEqual(summary["total_measured_credits_exact"], "0.100000")
        self.assertEqual(summary["total_session_scope_credits_exact"], "0.100000")

        # Session reconciliation verifies exact match
        recon = self.aggregator.reconcile_session("sess_hier_01")
        self.assertTrue(recon["reconciled"])
        self.assertEqual(recon["discrepancy_exact"], "0.000000")

    # 12. Account-level totals that do not reconcile with known operation measurements
    def test_12_account_level_reconciliation_discrepancy(self):
        op = AIOperationRecord(
            operation_id="op_acc_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_acc_01",
            credits_used=Decimal("0.050000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_cli:sess_acc_01:op_acc_01",
        )
        self.store.save_operation(op)

        # Record account checkpoint of 0.090000 (unobserved 0.040000 expenditure)
        self.store.record_account_checkpoint(
            provider="kiro_cli",
            reported_credits=Decimal("0.090000"),
            source="provider_billing_api",
        )

        summary = self.aggregator.get_summary()
        recon = summary["reconciliation"]
        self.assertEqual(recon["operation_sum_credits_exact"], "0.050000")
        self.assertEqual(recon["latest_account_checkpoint_credits_exact"], "0.090000")
        # Discrepancy is explicitly reported as 0.040000
        self.assertEqual(recon["reconciliation_discrepancy_exact"], "0.040000")

    # 13. Model/token/credit values unknown or missing (clean None representation without fabrication)
    def test_13_missing_and_unknown_fields_preserved_as_none(self):
        rec = AIOperationRecord(
            operation_id="op_unknown_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_unknown_01",
            credits_used=None,  # Not reported
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.UNAVAILABLE,
            model_requested="auto",
            model_resolved=None,  # Never label auto as resolved
            token_usage=None,  # Zero fabrication
            cost_estimate_usd=None,
            deduplication_key="kiro_cli:sess_unknown_01:op_unknown_01",
        )
        self.store.save_operation(rec)
        fetched = self.store.get_operation("op_unknown_01")

        self.assertIsNone(fetched.credits_used)
        self.assertIsNone(fetched.model_resolved)
        self.assertIsNone(fetched.token_usage)
        self.assertIsNone(fetched.cost_estimate_usd)
        self.assertEqual(fetched.measurement_status, MeasurementStatus.UNAVAILABLE)

        # Serialization preserves null without fabricating numbers
        d = fetched.to_dict()
        self.assertIsNone(d["credits_used"])
        self.assertIsNone(d["credits_used_exact"])
        self.assertIsNone(d["model_resolved"])
        self.assertIsNone(d["token_usage"])

    # 14. Fractional credit precision and decimal-safe aggregation (Decimal arithmetic)
    def test_14_decimal_safe_precision_and_no_float_drift(self):
        # 10 turns of 0.1 credits each
        for i in range(10):
            op = AIOperationRecord(
                operation_id=f"op_prec_{i}",
                provider="kiro_cli",
                source="session_json",
                operation_type=OperationType.PROMPT,
                trigger_type=TriggerType.USER_INITIATED,
                session_id="sess_prec",
                credits_used=Decimal("0.100000"),
                measurement_scope=MeasurementScope.OPERATION,
                measurement_status=MeasurementStatus.MEASURED,
                deduplication_key=f"kiro_cli:sess_prec:op_{i}",
            )
            self.store.save_operation(op)

        summary = self.aggregator.get_summary()
        # In float math 0.1 * 10 is often 0.9999999999999999. In Decimal it is exactly 1.000000.
        self.assertEqual(summary["total_measured_credits_exact"], "1.000000")
        self.assertEqual(summary["total_measured_credits"], 1.0)

    # 15. Missing, corrupted, and partially written session artifacts
    def test_15_corrupted_and_partial_writes_handling(self):
        sess_dir = self.test_dir / "sessions" / "cli"
        sess_dir.mkdir(parents=True, exist_ok=True)
        corrupted_file = sess_dir / "partial.json"

        # Incomplete write: trailing comma or unclosed dict
        corrupted_file.write_text('{"session_id": "sess_part", "user_turn_metadatas": [', encoding="utf-8")

        source = KiroCLISessionSource(sessions_dir=sess_dir, state_file=self.test_dir / "state.json")
        source.start()
        events = source.collect()
        # Should gracefully defer without emitting AI events from corrupted/partial file
        ai_events = [e for e in events if e.get("event_type") == EventType.AI_RUN_COMPLETED]
        self.assertEqual(ai_events, [])

        # File finishes writing
        complete_content = {
            "session_id": "sess_part",
            "session_state": {
                "id": "sess_part",
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "id": "turn_1",
                            "model": "claude-haiku-4.5",
                            "metering_usage": [{"value": 0.05, "unit": "credit"}],
                        }
                    ]
                }
            }
        }
        corrupted_file.write_text(json.dumps(complete_content), encoding="utf-8")

        # Next poll picks it up cleanly
        events2 = source.collect()
        ai_events = [e for e in events2 if e.get("event_type") == EventType.AI_RUN_COMPLETED]
        self.assertEqual(len(ai_events), 1)

    # 16. Collector restart and recovery after downtime
    def test_16_collector_restart_recovery(self):
        op1 = AIOperationRecord(
            operation_id="op_recov_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_recov",
            credits_used=Decimal("0.050000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            deduplication_key="kiro_cli:sess_recov:op_01",
        )
        self.store.save_operation(op1)

        # Reopen store (simulating collector restart)
        reopened_store = AccountingStore(self.data_dir)
        reopened_aggregator = AccountingAggregator(reopened_store)

        ops = reopened_store.get_operations(session_id="sess_recov")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].credits_used, Decimal("0.050000"))

        summary = reopened_aggregator.get_summary()
        self.assertEqual(summary["total_operations_count"], 1)
        self.assertEqual(summary["total_measured_credits_exact"], "0.050000")

    # 17. Jira ticket switching during an active session (half-open intervals and proper split attribution)
    def test_17_jira_ticket_switching_half_open_split(self):
        # Turn 1 happened under SCRUM-1
        op1 = AIOperationRecord(
            operation_id="turn_split_01",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_split",
            completed_at="2026-10-17T10:00:00Z",
            credits_used=Decimal("0.040000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            jira_key="SCRUM-1",
            deduplication_key="kiro_cli:sess_split:turn_1",
        )
        # Turn 2 happened after user switched ticket to SCRUM-2
        op2 = AIOperationRecord(
            operation_id="turn_split_02",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            session_id="sess_split",
            completed_at="2026-10-17T10:30:00Z",
            credits_used=Decimal("0.060000"),
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=MeasurementStatus.MEASURED,
            jira_key="SCRUM-2",
            deduplication_key="kiro_cli:sess_split:turn_2",
        )
        self.store.save_operation(op1)
        self.store.save_operation(op2)

        summary = self.aggregator.get_summary()
        tickets = summary["jira_attribution"]["attributed_tickets"]
        self.assertIn("SCRUM-1", tickets)
        self.assertIn("SCRUM-2", tickets)

        # Exact split attribution
        self.assertEqual(tickets["SCRUM-1"]["credits_exact"], "0.040000")
        self.assertEqual(tickets["SCRUM-2"]["credits_exact"], "0.060000")
        self.assertEqual(tickets["SCRUM-1"]["count"], 1)
        self.assertEqual(tickets["SCRUM-2"]["count"], 1)

    # 18. Preservation of existing Antigravity behavior and Kiro IDE/CLI functionality
    def test_18_preservation_of_antigravity_tokens_and_kiro_functionality(self):
        ag_op = AntigravityAccountingAdapter.generation_to_operation_record(
            conv_id="conv_ag_001",
            idx=1,
            timestamp="2026-10-17T11:00:00Z",
            model_id="gemini-2.5-pro",
            non_cached_input=1200,
            cached_input=400,
            total_output=650,
            thinking_tokens=150,
            jira_key="SCRUM-1",
        )
        self.assertEqual(ag_op.provider, "antigravity")
        self.assertEqual(ag_op.token_usage["total_tokens"], 2250)
        # Credits are unavailable on Antigravity; token metrics are authoritative
        self.assertIsNone(ag_op.credits_used)
        self.assertEqual(ag_op.measurement_status, MeasurementStatus.UNAVAILABLE)

        self.store.save_operation(ag_op)
        summary = self.aggregator.get_summary()
        ag_summary = summary["by_provider"]["antigravity"]
        self.assertEqual(ag_summary["count"], 1)
        self.assertEqual(ag_summary["unmeasured_count"], 1)
        self.assertEqual(ag_summary["credits_exact"], "0")

    # 19. Kiro IDE Subagent Extraction and Parent Linking (Invariant 8)
    def test_19_kiro_ide_subagent_extraction_and_parent_linking(self):
        from ai_telemetry_agent.sources.kiro_ide_sessions import KiroIDESessionSource
        import json
        
        # Create mock workspace and session directory structure
        ws_dir = self.test_dir / "mock_ws"
        sess_dir = ws_dir / "sess_parent_001"
        sess_dir.mkdir(parents=True, exist_ok=True)
        
        # Write mock session.json
        (sess_dir / "session.json").write_text(json.dumps({
            "sessionId": "sess_parent_001",
            "session_id": "sess_parent_001",
            "createdAt": "2026-10-17T12:00:00Z",
            "model": "claude-sonnet-4.5"
        }), encoding="utf-8")
        
        # Write mock messages.jsonl containing subagent start & complete
        messages = [
            {"id": "msg_01", "type": "sub_agent_start", "timestamp": "2026-10-17T12:01:00Z", "payload": {
                "subSessionId": "sub_sess_abc",
                "parentExecutionId": "exec_parent_01",
                "subAgentName": "code_refactor_agent"
            }},
            {"id": "msg_02", "type": "sub_agent_complete", "timestamp": "2026-10-17T12:02:00Z", "payload": {
                "subSessionId": "sub_sess_abc",
                "parentExecutionId": "exec_parent_01",
                "subAgentName": "code_refactor_agent"
            }}
        ]
        (sess_dir / "messages.jsonl").write_text(
            "\n".join(json.dumps(m) for m in messages) + "\n",
            encoding="utf-8"
        )
        
        state_file = self.test_dir / "ide_state.json"
        src = KiroIDESessionSource(sessions_dir=self.test_dir, state_file=state_file)
        src.start()
        events = src.collect()
        
        agent_events = [e for e in events if e.get("event_type") in ("agent_started", "agent_completed")]
        self.assertEqual(len(agent_events), 2)
        
        start_ev = agent_events[0]
        self.assertEqual(start_ev["event_type"], "agent_started")
        self.assertEqual(start_ev["payload"]["parent_session_id"], "sess_parent_001")
        self.assertEqual(start_ev["payload"]["metadata"]["parent_execution_id"], "exec_parent_01")
        self.assertEqual(start_ev["payload"]["metadata"]["sub_agent_name"], "code_refactor_agent")
        self.assertTrue(start_ev["payload"]["metadata"]["is_subagent"])
        self.assertIsNone(start_ev["payload"]["credits"])

    # 20. Historical Event Backfill and Deduplication (Root Cause Reproduction & Fix)
    def test_20_historical_event_backfill_and_deduplication(self):
        from ai_telemetry_agent.core.event import TelemetryEvent, EventType
        from ai_telemetry_agent.core.collector import TelemetryCollector

        # Write mock historical telemetry event
        event_dict = {
            "event_id": "ev_hist_001",
            "event_type": "ai_run_completed",
            "timestamp": "2026-10-15T10:00:00Z",
            "collector": {"version": "0.1.2", "device_id": "dev-01"},
            "identity": {"member_id": "mem-01"},
            "context": {"session_id": "sess_hist_01"},
            "payload": {
                "provider": "kiro_cli",
                "model": "deepseek-3.2",
                "session_id": "sess_hist_01",
                "run_id": "op_hist_01",
                "credits": 0.0854,
                "capture_method": "session_json",
                "metadata": {"request_attempts": 1, "total_request_count": 1}
            }
        }
        telem_file = self.data_dir / "telemetry-2026-10-15.jsonl"
        telem_file.write_text(json.dumps(event_dict) + "\n", encoding="utf-8")

        collector = TelemetryCollector()
        stats1 = self.store.backfill_from_event_store(collector._ai_event_to_operation_record)
        self.assertEqual(stats1["records_saved"], 1)
        self.assertEqual(stats1["records_skipped"], 0)

        # Verify operation record exists in accounting ledger
        op = self.store.get_operation("op_hist_01")
        self.assertIsNotNone(op)
        self.assertEqual(op.provider, "kiro_cli")
        self.assertEqual(op.credits_used, Decimal("0.0854"))
        self.assertEqual(op.operation_type, OperationType.PROMPT)

        # Run again to verify idempotency
        stats2 = self.store.backfill_from_event_store(collector._ai_event_to_operation_record)
        self.assertEqual(stats2["records_saved"], 0)
        self.assertEqual(stats2["records_skipped"], 1)

    # 21. Multi-request Prompt Turn is Not Labeled as Retry
    def test_21_kiro_cli_multi_request_not_labeled_retry(self):
        from ai_telemetry_agent.accounting.adapters import KiroCLIAccountingAdapter

        raw_turn = {
            "id": "turn_multi_req_01",
            "total_request_count": 15,
            "request_attempts": 15,
            "metering_usage": [{"value": 0.75, "unit": "credit"}],
            "end_reason": "UserTurnEnd",
            "model": "auto"
        }
        session_data = {"session_id": "sess_multi_01", "session_created_reason": "cli"}

        op = KiroCLIAccountingAdapter.turn_to_operation_record(
            session_id="sess_multi_01",
            session_data=session_data,
            raw_turn=raw_turn,
            turn_idx=0
        )
        self.assertEqual(op.operation_type, OperationType.PROMPT)
        self.assertFalse(op.metadata["has_retries"])
        self.assertEqual(op.metadata["retry_attempts"], 0)
        self.assertFalse(op.metadata["is_retry"])
        self.assertIsNone(op.metadata["retry_credits"])

        # Turn where request attempts exceed requests
        retry_turn = {
            "id": "turn_real_retry_01",
            "total_request_count": 2,
            "request_attempts": 4,
            "metering_usage": [{"value": 0.10, "unit": "credit"}],
            "end_reason": "UserTurnEnd",
            "model": "auto"
        }
        op_retry = KiroCLIAccountingAdapter.turn_to_operation_record(
            session_id="sess_multi_01",
            session_data=session_data,
            raw_turn=retry_turn,
            turn_idx=1
        )
        self.assertEqual(op_retry.operation_type, OperationType.PROMPT)
        self.assertTrue(op_retry.metadata["has_retries"])
        self.assertEqual(op_retry.metadata["retry_attempts"], 2)
        self.assertFalse(op_retry.metadata["is_retry"])
        self.assertIsNone(op_retry.metadata["retry_credits"])
        self.assertEqual(op_retry.credits_used, Decimal("0.10"))

    # 22. Exact Session Verification for Kiro CLI Retry Classification Defect
    def test_22_kiro_cli_retry_classification_exact_sessions(self):
        from ai_telemetry_agent.core.collector import TelemetryCollector
        from ai_telemetry_agent.core.event import TelemetryEvent, EventType

        collector = TelemetryCollector()

        # Three exact sessions confirmed in defect evidence
        session_evidence = [
            {
                "session_id": "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5",
                "run_id": "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5_turn_0",
                "total_request_count": 2,
                "request_attempts": 4,
                "credits": "0.03229535406301825",
                "expected_retry_attempts": 2,
            },
            {
                "session_id": "43514682-b30f-4cab-94f0-f793c6bda57e",
                "run_id": "43514682-b30f-4cab-94f0-f793c6bda57e_turn_0",
                "total_request_count": 3,
                "request_attempts": 7,
                "credits": "0.3065108489220564",
                "expected_retry_attempts": 4,
            },
            {
                "session_id": "857dc9f9-5475-41f1-a9f4-3b8d1e335ab9",
                "run_id": "857dc9f9-5475-41f1-a9f4-3b8d1e335ab9_turn_0",
                "total_request_count": 53,
                "request_attempts": 55,
                "credits": "10.538063953565505",
                "expected_retry_attempts": 2,
            },
        ]

        # 1. Verify collector._ai_event_to_operation_record classifies them as PROMPT
        records = []
        for item in session_evidence:
            ev = TelemetryEvent.from_dict({
                "event_id": f"ev_{item['run_id']}",
                "event_type": EventType.AI_RUN_COMPLETED,
                "timestamp": "2026-10-17T12:00:00Z",
                "collector": {"version": "0.1.2", "device_id": "DEV-001"},
                "identity": {"member_id": "M001"},
                "payload": {
                    "provider": "kiro_cli",
                    "model": "auto",
                    "session_id": item["session_id"],
                    "run_id": item["run_id"],
                    "credits": item["credits"],
                    "capture_method": "session_json",
                    "metadata": {
                        "turn_index": 0,
                        "total_request_count": item["total_request_count"],
                        "request_attempts": item["request_attempts"],
                        "end_reason": "UserTurnEnd",
                    }
                }
            })
            op = collector._ai_event_to_operation_record(ev)
            self.assertIsNotNone(op)
            self.assertEqual(op.operation_type, OperationType.PROMPT)
            self.assertTrue(op.metadata["has_retries"])
            self.assertEqual(op.metadata["retry_attempts"], item["expected_retry_attempts"])
            self.assertFalse(op.metadata["is_retry"])
            self.assertIsNone(op.metadata["retry_credits"])
            self.assertEqual(op.credits_used, Decimal(item["credits"]))
            self.assertEqual(op.deduplication_key, f"kiro_cli:{item['session_id']}:{item['run_id']}")

            # 2. Verify legacy serialization with operation_type="retry" is normalized by from_dict
            legacy_dict = op.to_dict()
            legacy_dict["operation_type"] = "retry"
            restored = AIOperationRecord.from_dict(legacy_dict)
            self.assertEqual(restored.operation_type, OperationType.PROMPT)
            self.assertTrue(restored.metadata["has_retries"])
            self.assertEqual(restored.metadata["retry_attempts"], item["expected_retry_attempts"])
            self.assertFalse(restored.metadata["is_retry"])
            self.assertEqual(restored.credits_used, Decimal(item["credits"]))
            records.append(restored)

        # 3. Verify aggregation across the 3 sessions
        test_store = AccountingStore(self.test_dir / "exact_sessions_data")
        test_agg = AccountingAggregator(test_store)
        for r in records:
            test_store.save_operation(r)

        summary = test_agg.get_summary()
        self.assertEqual(summary["total_measured_credits_exact"], "10.87687015655057965")
        self.assertEqual(summary["by_operation_type"]["prompt"]["count"], 3)
        self.assertEqual(summary["by_operation_type"]["prompt"]["credits_exact"], "10.87687015655057965")
        self.assertNotIn("retry", summary["by_operation_type"])
        self.assertEqual(summary["failed_and_retried"]["retried"]["credits_exact"], "0")
        self.assertEqual(summary["failed_and_retried"]["retried"]["count"], 0)
        self.assertEqual(summary["failed_and_retried"]["turns_with_retries_count"], 3)


    # 23. AccountingStore Query Filtering After Deserialization & Normalization
    def test_23_accounting_store_query_filtering_after_normalization(self):
        import hashlib

        filter_test_dir = self.test_dir / "filter_test_data"
        filter_test_dir.mkdir(parents=True, exist_ok=True)
        jsonl_file = filter_test_dir / "accounting-2026-10-16.jsonl"

        # Synthetic records representing:
        # - 3 legacy Kiro CLI turn records (persisted on disk with operation_type="retry")
        # - 1 genuine standalone retry record (persisted on disk with operation_type="retry")
        # - 1 normal prompt record (persisted with operation_type="prompt")
        # - 1 tool-triggered subagent record
        # - 1 unmeasured query record
        # - 1 duplicate line to verify deduplication
        raw_lines = [
            # Legacy turn 1
            {
                "operation_id": "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5_turn_0",
                "session_id": "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5",
                "provider": "kiro_cli",
                "source": "session_json",
                "operation_type": "retry",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-2",
                "credits_used": 0.03229535406301825,
                "credits_used_exact": "0.03229535406301825",
                "measurement_status": "measured",
                "metadata": {
                    "turn_index": 0,
                    "total_request_count": 2,
                    "request_attempts": 4,
                    "end_reason": "UserTurnEnd",
                }
            },
            # Legacy turn 2
            {
                "operation_id": "43514682-b30f-4cab-94f0-f793c6bda57e_turn_0",
                "session_id": "43514682-b30f-4cab-94f0-f793c6bda57e",
                "provider": "kiro_cli",
                "source": "session_json",
                "operation_type": "retry",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-2",
                "credits_used": 0.3065108489220564,
                "credits_used_exact": "0.3065108489220564",
                "measurement_status": "measured",
                "metadata": {
                    "turn_index": 0,
                    "total_request_count": 3,
                    "request_attempts": 7,
                    "end_reason": "UserTurnEnd",
                }
            },
            # Legacy turn 3
            {
                "operation_id": "857dc9f9-5475-41f1-a9f4-3b8d1e335ab9_turn_0",
                "session_id": "857dc9f9-5475-41f1-a9f4-3b8d1e335ab9",
                "provider": "kiro_cli",
                "source": "session_json",
                "operation_type": "retry",
                "trigger_type": "user_initiated",
                "jira_key": None,
                "credits_used": 10.538063953565505,
                "credits_used_exact": "10.538063953565505",
                "measurement_status": "measured",
                "metadata": {
                    "turn_index": 0,
                    "total_request_count": 53,
                    "request_attempts": 55,
                    "end_reason": "UserTurnEnd",
                }
            },
            # Genuine standalone retry operation
            {
                "operation_id": "op_standalone_retry_01",
                "session_id": "sess_standalone_retry",
                "provider": "kiro_cli",
                "source": "session_json",
                "operation_type": "retry",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-5",
                "credits_used": 0.05,
                "credits_used_exact": "0.050000",
                "measurement_status": "measured",
                "metadata": {
                    "is_retry": True,
                    "request_attempts": 2,
                }
            },
            # Normal prompt operation
            {
                "operation_id": "op_normal_prompt_01",
                "session_id": "sess_normal_prompt",
                "provider": "kiro_ide",
                "source": "messages_jsonl",
                "operation_type": "prompt",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-3",
                "credits_used": 0.02,
                "credits_used_exact": "0.020000",
                "measurement_status": "measured",
                "metadata": {
                    "total_request_count": 1,
                    "request_attempts": 1,
                }
            },
            # Subagent operation
            {
                "operation_id": "op_sub_01",
                "session_id": "sess_sub_01",
                "provider": "kiro_ide",
                "source": "messages_jsonl",
                "operation_type": "subagent",
                "trigger_type": "agent_spawned",
                "jira_key": "SCRUM-3",
                "credits_used": 0.04,
                "credits_used_exact": "0.040000",
                "measurement_status": "measured",
                "metadata": {
                    "is_subagent": True,
                }
            },
            # Unmeasured query operation
            {
                "operation_id": "op_unmeas_01",
                "session_id": "sess_unmeas_01",
                "provider": "antigravity",
                "source": "sqlite",
                "operation_type": "query",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-9",
                "credits_used": None,
                "credits_used_exact": None,
                "measurement_status": "unavailable",
                "metadata": {}
            },
            # Duplicate entry of op_normal_prompt_01 (should be deduplicated)
            {
                "operation_id": "op_normal_prompt_01",
                "session_id": "sess_normal_prompt",
                "provider": "kiro_ide",
                "source": "messages_jsonl",
                "operation_type": "prompt",
                "trigger_type": "user_initiated",
                "jira_key": "SCRUM-3",
                "credits_used": 0.02,
                "credits_used_exact": "0.020000",
                "measurement_status": "measured",
                "metadata": {
                    "total_request_count": 1,
                    "request_attempts": 1,
                }
            },
        ]

        # Write file directly to test dir
        file_content_initial = "\n".join(json.dumps(r) for r in raw_lines) + "\n"
        jsonl_file.write_text(file_content_initial, encoding="utf-8")
        hash_before = hashlib.sha256(jsonl_file.read_bytes()).hexdigest()

        test_store = AccountingStore(filter_test_dir)

        # 1. Prompt filter returns all three legacy records + normal prompt (4 records)
        prompt_ops = test_store.get_operations(operation_type="prompt")
        self.assertEqual(len(prompt_ops), 4)
        prompt_ids = {op.operation_id for op in prompt_ops}
        self.assertIn("418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5_turn_0", prompt_ids)
        self.assertIn("43514682-b30f-4cab-94f0-f793c6bda57e_turn_0", prompt_ids)
        self.assertIn("857dc9f9-5475-41f1-a9f4-3b8d1e335ab9_turn_0", prompt_ids)
        self.assertIn("op_normal_prompt_01", prompt_ids)
        for op in prompt_ops:
            self.assertEqual(op.operation_type, OperationType.PROMPT)

        # 2. Retry filter excludes the three legacy records and returns only genuine standalone retries
        retry_ops = test_store.get_operations(operation_type="retry")
        self.assertEqual(len(retry_ops), 1)
        self.assertEqual(retry_ops[0].operation_id, "op_standalone_retry_01")
        self.assertEqual(retry_ops[0].operation_type, OperationType.RETRY)

        # 3. Provider filter
        kiro_cli_ops = test_store.get_operations(provider="kiro_cli")
        self.assertEqual(len(kiro_cli_ops), 4)  # 3 legacy + 1 standalone retry
        kiro_ide_ops = test_store.get_operations(provider="kiro_ide")
        self.assertEqual(len(kiro_ide_ops), 2)  # 1 prompt + 1 subagent
        ag_ops = test_store.get_operations(provider="antigravity")
        self.assertEqual(len(ag_ops), 1)

        # 4. Session filter
        sess_ops = test_store.get_operations(session_id="418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5")
        self.assertEqual(len(sess_ops), 1)
        self.assertEqual(sess_ops[0].session_id, "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5")

        # 5. Jira key filter
        jira_ops = test_store.get_operations(jira_key="SCRUM-2")
        self.assertEqual(len(jira_ops), 2)
        jira_ids = {op.operation_id for op in jira_ops}
        self.assertEqual(jira_ids, {
            "418a7b9d-1e81-4d8c-a1b8-ac87beb90cc5_turn_0",
            "43514682-b30f-4cab-94f0-f793c6bda57e_turn_0",
        })

        # 6. Trigger type filter
        trigger_ops = test_store.get_operations(trigger_type="agent_spawned")
        self.assertEqual(len(trigger_ops), 1)
        self.assertEqual(trigger_ops[0].operation_id, "op_sub_01")

        # 7. Measurement status filter
        unmeas_ops = test_store.get_operations(measurement_status="unavailable")
        self.assertEqual(len(unmeas_ops), 1)
        self.assertEqual(unmeas_ops[0].operation_id, "op_unmeas_01")

        # 8. Deduplication and Pagination
        all_ops = test_store.get_operations()
        self.assertEqual(len(all_ops), 7)  # 7 unique operations, duplicate op_normal_prompt_01 ignored
        paginated_ops = test_store.get_operations(limit=2)
        self.assertEqual(len(paginated_ops), 2)

        # 9. Verify underlying JSONL file is not modified
        hash_after = hashlib.sha256(jsonl_file.read_bytes()).hexdigest()
        self.assertEqual(hash_before, hash_after)
        self.assertEqual(jsonl_file.read_text(encoding="utf-8"), file_content_initial)

    # 24. New JSON fields and exact dimensional totals across every summary dimension
    def test_24_accounting_summary_new_json_fields_and_exact_dimensional_totals(self):
        summary_dir = self.test_dir / "summary_test_data"
        summary_dir.mkdir(parents=True, exist_ok=True)
        store = AccountingStore(summary_dir)
        aggregator = AccountingAggregator(store)

        # Verify empty store first
        empty_summary = aggregator.get_summary()
        self.assertEqual(empty_summary["total_operations_count"], 0)
        self.assertEqual(empty_summary["measured_operations_count"], 0)
        self.assertEqual(empty_summary["unmeasured_operations_count"], 0)
        self.assertEqual(empty_summary["total_measured_credits_exact"], "0")
        self.assertIn("cross_provider_coverage_note", empty_summary)

        # Populate synthetic multi-provider dataset:
        # kiro_cli: 2 measured prompt operations
        # kiro_ide: 1 measured prompt, 1 measured subagent, 1 unmeasured tool_triggered
        # antigravity: 1 unmeasured tool_triggered, 1 unmeasured query
        ops = [
            AIOperationRecord(
                operation_id="syn_cli_1",
                session_id="sess_cli",
                provider="kiro_cli",
                source="session_json",
                operation_type=OperationType.PROMPT,
                trigger_type=TriggerType.USER_INITIATED,
                jira_key="PROJ-101",
                model_resolved="claude-sonnet-4.5",
                credits_used=Decimal("1.250000"),
                measurement_status=MeasurementStatus.MEASURED,
                started_at="2026-10-17T10:00:00Z",
                completed_at="2026-10-17T10:00:05Z",
            ),
            AIOperationRecord(
                operation_id="syn_cli_2",
                session_id="sess_cli",
                provider="kiro_cli",
                source="session_json",
                operation_type=OperationType.PROMPT,
                trigger_type=TriggerType.USER_INITIATED,
                jira_key="PROJ-101",
                model_resolved="claude-haiku-4.5",
                credits_used=Decimal("0.500000"),
                measurement_status=MeasurementStatus.MEASURED,
                started_at="2026-10-17T10:01:00Z",
                completed_at="2026-10-17T10:01:05Z",
            ),
            AIOperationRecord(
                operation_id="syn_ide_1",
                session_id="sess_ide",
                provider="kiro_ide",
                source="messages_jsonl",
                operation_type=OperationType.PROMPT,
                trigger_type=TriggerType.USER_INITIATED,
                jira_key="PROJ-202",
                model_resolved="auto",
                credits_used=Decimal("0.750000"),
                measurement_status=MeasurementStatus.MEASURED,
                started_at="2026-10-17T10:02:00Z",
                completed_at="2026-10-17T10:02:05Z",
            ),
            AIOperationRecord(
                operation_id="syn_ide_sub",
                session_id="sess_ide",
                provider="kiro_ide",
                source="messages_jsonl",
                operation_type=OperationType.SUBAGENT,
                trigger_type=TriggerType.AGENT_SPAWNED,
                jira_key="PROJ-202",
                model_resolved="auto",
                credits_used=Decimal("0.200000"),
                measurement_status=MeasurementStatus.MEASURED,
                started_at="2026-10-17T10:03:00Z",
                completed_at="2026-10-17T10:03:05Z",
                metadata={"is_subagent": True},
            ),
            AIOperationRecord(
                operation_id="syn_ide_unmeas",
                session_id="sess_ide_bg",
                provider="kiro_ide",
                source="messages_jsonl",
                operation_type=OperationType.TOOL_TRIGGERED,
                trigger_type=TriggerType.AGENT_SPAWNED,
                jira_key=None,
                model_resolved="claude-sonnet-4.5",
                credits_used=None,
                measurement_status=MeasurementStatus.UNAVAILABLE,
                started_at="2026-10-17T10:04:00Z",
                completed_at="2026-10-17T10:04:05Z",
            ),
            AIOperationRecord(
                operation_id="syn_ag_tool",
                session_id="sess_ag",
                provider="antigravity",
                source="sqlite",
                operation_type=OperationType.TOOL_TRIGGERED,
                trigger_type=TriggerType.AGENT_SPAWNED,
                jira_key="UNASSIGNED",
                model_resolved="gemini-flash",
                credits_used=None,
                measurement_status=MeasurementStatus.UNAVAILABLE,
                started_at="2026-10-17T10:05:00Z",
                completed_at="2026-10-17T10:05:05Z",
            ),
            AIOperationRecord(
                operation_id="syn_ag_query",
                session_id="sess_ag",
                provider="antigravity",
                source="sqlite",
                operation_type=OperationType.QUERY,
                trigger_type=TriggerType.USER_INITIATED,
                jira_key="UNASSIGNED",
                model_resolved="gemini-flash",
                credits_used=None,
                measurement_status=MeasurementStatus.UNAVAILABLE,
                started_at="2026-10-17T10:06:00Z",
                completed_at="2026-10-17T10:06:05Z",
            ),
        ]

        for op in ops:
            store.save_operation(op)

        summary = aggregator.get_summary()

        # 1. New top-level JSON fields
        self.assertEqual(summary["total_operations_count"], 7)
        self.assertEqual(summary["measured_operations_count"], 4)
        self.assertEqual(summary["unmeasured_operations_count"], 3)
        self.assertEqual(
            summary["measured_operations_count"] + summary["unmeasured_operations_count"],
            summary["total_operations_count"],
        )
        self.assertEqual(summary["total_measured_credits_exact"], "2.700000")
        self.assertAlmostEqual(summary["total_measured_credits"], 2.7, places=6)

        note = summary["cross_provider_coverage_note"]
        self.assertIn("Total measured credits reflects observable expenditure for Kiro CLI and Kiro IDE only.", note)
        self.assertIn("Antigravity operations (2 ops) remain unmeasured under credit accounting.", note)

        # 2. Dimension: Provider breakdown
        by_prov = summary["by_provider"]
        self.assertEqual(set(by_prov.keys()), {"kiro_cli", "kiro_ide", "antigravity"})
        self.assertEqual(sum(v["count"] for v in by_prov.values()), 7)
        self.assertEqual(sum(v["unmeasured_count"] for v in by_prov.values()), 3)
        self.assertEqual(sum(v["count"] - v["unmeasured_count"] for v in by_prov.values()), 4)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in by_prov.values()),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(by_prov["kiro_cli"]["count"], 2)
        self.assertEqual(by_prov["kiro_cli"]["unmeasured_count"], 0)
        self.assertEqual(by_prov["kiro_cli"]["credits_exact"], "1.750000")
        self.assertEqual(by_prov["kiro_ide"]["count"], 3)
        self.assertEqual(by_prov["kiro_ide"]["unmeasured_count"], 1)
        self.assertEqual(by_prov["kiro_ide"]["credits_exact"], "0.950000")
        self.assertEqual(by_prov["antigravity"]["count"], 2)
        self.assertEqual(by_prov["antigravity"]["unmeasured_count"], 2)
        self.assertEqual(by_prov["antigravity"]["credits_exact"], "0")

        # 3. Dimension: Operation Type breakdown
        by_op = summary["by_operation_type"]
        self.assertEqual(sum(v["count"] for v in by_op.values()), 7)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in by_op.values()),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(by_op["prompt"]["count"], 3)
        self.assertEqual(by_op["prompt"]["credits_exact"], "2.500000")
        self.assertEqual(by_op["subagent"]["count"], 1)
        self.assertEqual(by_op["subagent"]["credits_exact"], "0.200000")
        self.assertEqual(by_op["tool_triggered"]["count"], 2)
        self.assertEqual(by_op["tool_triggered"]["credits_exact"], "0")
        self.assertEqual(by_op["query"]["count"], 1)
        self.assertEqual(by_op["query"]["credits_exact"], "0")

        # 4. Dimension: Trigger Type breakdown
        by_trig = summary["by_trigger_type"]
        self.assertEqual(sum(v["count"] for v in by_trig.values()), 7)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in by_trig.values()),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(by_trig["user_initiated"]["count"], 4)
        self.assertEqual(by_trig["user_initiated"]["credits_exact"], "2.500000")
        self.assertEqual(by_trig["agent_spawned"]["count"], 3)
        self.assertEqual(by_trig["agent_spawned"]["credits_exact"], "0.200000")

        # 5. Dimension: Interactive vs Automated
        ia = summary["interactive_vs_automated"]
        self.assertEqual(ia["interactive"]["count"] + ia["automated"]["count"], 7)
        self.assertEqual(ia["interactive"]["count"], 4)
        self.assertEqual(ia["automated"]["count"], 3)
        self.assertEqual(ia["interactive"]["credits_exact"], "2.500000")
        self.assertEqual(ia["automated"]["credits_exact"], "0.200000")
        self.assertEqual(
            Decimal(ia["interactive"]["credits_exact"]) + Decimal(ia["automated"]["credits_exact"]),
            Decimal(summary["total_measured_credits_exact"]),
        )

        # 6. Dimension: Subagent expenditure (includes subagent operation types and agent_spawned triggers)
        self.assertEqual(summary["subagent_expenditure"]["count"], 3)
        self.assertEqual(summary["subagent_expenditure"]["credits_exact"], "0.200000")

        # 7. Dimension: Measurement status breakdown
        by_status = summary["by_measurement_status"]
        self.assertEqual(sum(v["count"] for v in by_status.values()), 7)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in by_status.values()),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(by_status["measured"]["count"], 4)
        self.assertEqual(by_status["measured"]["credits_exact"], "2.700000")
        self.assertEqual(by_status["unavailable"]["count"], 3)
        self.assertEqual(by_status["unavailable"]["credits_exact"], "0")

        # 8. Dimension: Jira attribution
        jira = summary["jira_attribution"]
        tickets = jira["attributed_tickets"]
        unassigned = jira["unassigned"]
        self.assertEqual(sum(v["count"] for v in tickets.values()) + unassigned["count"], 7)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in tickets.values()) + Decimal(unassigned["credits_exact"]),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(tickets["PROJ-101"]["count"], 2)
        self.assertEqual(tickets["PROJ-101"]["credits_exact"], "1.750000")
        self.assertEqual(tickets["PROJ-202"]["count"], 2)
        self.assertEqual(tickets["PROJ-202"]["credits_exact"], "0.950000")
        self.assertEqual(unassigned["count"], 3)
        self.assertEqual(unassigned["credits_exact"], "0")

        # 9. Dimension: Model breakdown
        by_model = summary["by_model"]
        self.assertEqual(sum(v["count"] for v in by_model.values()), 7)
        self.assertEqual(
            sum(Decimal(v["credits_exact"]) for v in by_model.values()),
            Decimal(summary["total_measured_credits_exact"]),
        )
        self.assertEqual(by_model["claude-sonnet-4.5"]["count"], 2)
        self.assertEqual(by_model["claude-sonnet-4.5"]["credits_exact"], "1.250000")
        self.assertEqual(by_model["claude-haiku-4.5"]["count"], 1)
        self.assertEqual(by_model["claude-haiku-4.5"]["credits_exact"], "0.500000")
        self.assertEqual(by_model["auto"]["count"], 2)
        self.assertEqual(by_model["auto"]["credits_exact"], "0.950000")
        self.assertEqual(by_model["gemini-flash"]["count"], 2)
        self.assertEqual(by_model["gemini-flash"]["credits_exact"], "0")

    # 25. CLI accounting summary coverage labels and formatting
    def test_25_cli_accounting_summary_coverage_labels_and_formatting(self):
        import argparse
        import io
        from unittest.mock import patch
        from ai_telemetry_agent.cli import _handle_accounting_summary

        cli_test_dir = self.test_dir / "cli_summary_data"
        cli_test_dir.mkdir(parents=True, exist_ok=True)
        store = AccountingStore(cli_test_dir)
        aggregator = AccountingAggregator(store)

        # Store 2 measured operations (1 kiro_cli, 1 kiro_ide) and 1 unmeasured antigravity operation
        store.save_operation(AIOperationRecord(
            operation_id="cli_op_1",
            session_id="sess_c1",
            provider="kiro_cli",
            source="session_json",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            credits_used=Decimal("0.030000"),
            measurement_status=MeasurementStatus.MEASURED,
        ))
        store.save_operation(AIOperationRecord(
            operation_id="ide_op_1",
            session_id="sess_i1",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            credits_used=Decimal("0.020000"),
            measurement_status=MeasurementStatus.MEASURED,
        ))
        store.save_operation(AIOperationRecord(
            operation_id="ag_op_1",
            session_id="sess_a1",
            provider="antigravity",
            source="sqlite",
            operation_type=OperationType.TOOL_TRIGGERED,
            trigger_type=TriggerType.AGENT_SPAWNED,
            credits_used=None,
            measurement_status=MeasurementStatus.UNAVAILABLE,
        ))

        # 1. Text output formatting
        args_text = argparse.Namespace(
            date_from=None,
            date_to=None,
            ticket=None,
            repo=None,
            provider=None,
            json=False,
            by_provider=False,
            by_trigger=False,
            by_hook=False,
            by_ticket=False,
            by_model=False,
        )

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            _handle_accounting_summary(args_text, aggregator)
            output = mock_stdout.getvalue()

        # Check coverage labels
        self.assertIn("Operational Coverage      : 2 measured ops, 1 unmeasured ops", output)
        self.assertIn("Total Measured Credits    : 0.050000 credits (Kiro CLI & IDE only)", output)
        self.assertIn("Interactive Expenditure   : 0.050000 credits (2 ops: user prompts & queries)", output)
        self.assertIn("Automated Expenditure     : 0 credits (1 ops: tool calls & subagents)", output)
        self.assertIn(
            "[Coverage Note] Total measured credits reflects observable expenditure for Kiro CLI and Kiro IDE only. Antigravity operations (1 ops) remain unmeasured under credit accounting.",
            output,
        )
        self.assertIn("antigravity     : 0 credits (1 ops, unmeasured: 1)", output)
        self.assertIn("kiro_cli        : 0.030000 credits (1 ops)", output)
        self.assertIn("kiro_ide        : 0.020000 credits (1 ops)", output)

        # 2. JSON output formatting
        args_json = argparse.Namespace(
            date_from=None,
            date_to=None,
            ticket=None,
            repo=None,
            provider=None,
            json=True,
        )

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout_json:
            _handle_accounting_summary(args_json, aggregator)
            json_output = mock_stdout_json.getvalue()

        parsed = json.loads(json_output)
        self.assertEqual(parsed["total_operations_count"], 3)
        self.assertEqual(parsed["measured_operations_count"], 2)
        self.assertEqual(parsed["unmeasured_operations_count"], 1)
        self.assertEqual(parsed["total_measured_credits_exact"], "0.050000")
        self.assertIn("cross_provider_coverage_note", parsed)
        self.assertEqual(parsed["by_provider"]["antigravity"]["unmeasured_count"], 1)


if __name__ == "__main__":
    unittest.main()


