from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ai_telemetry_agent.accounting.aggregator import AccountingAggregator
from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
)
from ai_telemetry_agent.accounting.registry import HookExecutionRecord, HookRegistry
from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.attribution.aggregator import TicketAggregator
from ai_telemetry_agent.attribution.store import AttributionStore
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

    def test_hook_to_operation_id_correlation(self):
        """When collector saves an operation record from a hook event, pass operation_id to record_execution."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        payload = {"session_id": "sess_corr_123", "cwd": self.temp_dir.name}
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                process_kiro_hook("SessionEnd", collector=collector)
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        ops = collector.accounting_store.get_operations(session_id="sess_corr_123")
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op.operation_id, "sess_corr_123_completed")

        execs = collector.hook_registry.get_executions(session_id="sess_corr_123")
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].resulting_operation_id, op.operation_id)

    def test_jira_ticket_propagation_confirmed_attribution(self):
        """Confirmed Jira ticket in attribution store propagates to accounting operation and hook execution."""
        subprocess.run(["git", "init"], cwd=self.temp_dir.name, capture_output=True, check=True)

        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        # Select confirmed ticket for this repo
        collector.attribution_store.select_ticket(
            issue_key="SCRUM-42",
            repo_path=self.temp_dir.name,
            source="manual",
            allow_unverified=True,
            confirmation_state="confirmed",
        )

        payload = {"session_id": "sess_jira_prop", "cwd": self.temp_dir.name}
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                process_kiro_hook("SessionEnd", collector=collector)
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        ops = collector.accounting_store.get_operations(session_id="sess_jira_prop")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].jira_key, "SCRUM-42")

        execs = collector.hook_registry.get_executions(session_id="sess_jira_prop")
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].jira_key, "SCRUM-42")

    def test_unassigned_events_when_no_confirmed_ticket_exists(self):
        """Events without repository matching or without active confirmed ticket remain UNASSIGNED."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        non_repo = Path(self.temp_dir.name) / "non_repo_dir"
        non_repo.mkdir()

        payload = {"session_id": "sess_unassigned", "cwd": str(non_repo)}
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit):
                process_kiro_hook("SessionEnd", collector=collector)
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        ops = collector.accounting_store.get_operations(session_id="sess_unassigned")
        self.assertEqual(len(ops), 1)
        self.assertIsNone(ops[0].jira_key)

        execs = collector.hook_registry.get_executions(session_id="sess_unassigned")
        self.assertEqual(len(execs), 1)
        self.assertIsNone(execs[0].jira_key)

    def test_backward_compatible_registry_json_loading(self):
        """Older hook_registry.json without jira_key, estimated_credits, or allocation_method loads cleanly."""
        legacy_data = {
            "version": 1,
            "hooks": {
                "kiro_posttooluse": {
                    "hook_id": "kiro_posttooluse",
                    "hook_name": "PostToolUse",
                    "trigger_type": "event_driven",
                    "event_pattern": None,
                    "schedule_expression": None,
                    "command_or_script": None,
                    "is_ai_powered": False,
                    "provider": "kiro_ide",
                    "metadata": {},
                }
            },
            "executions": [
                {
                    "execution_id": "legacy-exec-1",
                    "hook_id": "kiro_posttooluse",
                    "hook_name": "PostToolUse",
                    "executed_at": "2026-10-18T10:00:00Z",
                    "trigger_type": "event_driven",
                    "provider": "kiro_ide",
                    "session_id": "sess-legacy-1",
                    "outcome": "success",
                    "is_shell_only": False,
                    "initiates_ai": False,
                    "resulting_operation_id": "op-legacy-1",
                    "credits_used": None,
                    "credits_used_exact": None,
                    "credit_source": None,
                    "measurement_status": "unavailable",
                    "schedule_expression": None,
                    "error_message": None,
                    "metadata": {},
                }
            ],
        }
        reg_file = self.data_dir / "hook_registry.json"
        reg_file.parent.mkdir(parents=True, exist_ok=True)
        reg_file.write_text(json.dumps(legacy_data), encoding="utf-8")

        registry = HookRegistry(data_dir=self.data_dir)
        execs = registry.get_executions()
        self.assertEqual(len(execs), 1)
        rec = execs[0]
        self.assertEqual(rec.execution_id, "legacy-exec-1")
        self.assertIsNone(rec.jira_key)
        self.assertIsNone(rec.estimated_credits)
        self.assertIsNone(rec.allocation_method)

        dict_repr = rec.to_dict()
        self.assertIn("jira_key", dict_repr)
        self.assertIsNone(dict_repr["jira_key"])
        self.assertIn("estimated_credits", dict_repr)
        self.assertIsNone(dict_repr["estimated_credits"])

    def test_duplicate_event_prevention(self):
        """Recording same hook execution with identical deduplication_key or resulting_operation_id does not duplicate."""
        registry = HookRegistry(data_dir=self.data_dir)

        # 1. Deduplication key
        rec1 = registry.record_execution(
            hook_id="kiro_save",
            hook_name="PostFileSave",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-dup-1",
            deduplication_key="dedup-save-101",
        )
        rec2 = registry.record_execution(
            hook_id="kiro_save",
            hook_name="PostFileSave",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-dup-1",
            deduplication_key="dedup-save-101",
        )
        self.assertEqual(rec1.execution_id, rec2.execution_id)
        self.assertEqual(len(registry.get_executions(hook_id="kiro_save")), 1)

        # 2. Resulting operation ID
        rec3 = registry.record_execution(
            hook_id="kiro_stop",
            hook_name="Stop",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-dup-2",
            resulting_operation_id="op-run-777",
        )
        rec4 = registry.record_execution(
            hook_id="kiro_stop",
            hook_name="Stop",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-dup-2",
            resulting_operation_id="op-run-777",
        )
        self.assertEqual(rec3.execution_id, rec4.execution_id)
        self.assertEqual(len(registry.get_executions(hook_id="kiro_stop")), 1)

    def test_actual_credit_totals_remaining_unchanged_when_estimates_present(self):
        """Aggregator total_measured_credits strictly reflects measured credits and is never inflated by estimates."""
        store = AccountingStore(data_dir=self.data_dir)
        registry = HookRegistry(data_dir=self.data_dir)

        op_measured = AIOperationRecord(
            operation_id="op_meas_1",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            credits_used=Decimal("0.50"),
            measurement_status=MeasurementStatus.MEASURED,
            jira_key="PROJ-10",
        )
        store.save_operation(op_measured)

        registry.record_execution(
            hook_id="kiro_pretooluse",
            hook_name="PreToolUse",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-est-1",
            resulting_operation_id="op_meas_1",
            jira_key="PROJ-10",
            credits_used=None,
            estimated_credits=Decimal("0.25"),
            allocation_method="even_split_by_run",
        )

        aggregator = AccountingAggregator(store, hook_registry=registry)
        summary = aggregator.get_summary()

        self.assertEqual(summary["total_measured_credits_exact"], "0.50")
        self.assertEqual(summary["total_measured_credits"], 0.50)

        h_data = summary["by_hook"]["PreToolUse"]
        self.assertEqual(h_data["credits_exact"], "0")
        self.assertEqual(h_data["estimated_credits_exact"], "0.25")

        t_data = summary["jira_attribution"]["attributed_tickets"]["PROJ-10"]
        self.assertEqual(t_data["credits_exact"], "0.50")
        self.assertEqual(t_data["hook_estimated_credits_exact"], "0.25")

    def test_parent_operations_with_missing_credits_not_producing_fabricated_estimates(self):
        """Parent operations with missing (None) credits do not produce fabricated estimates."""
        store = AccountingStore(data_dir=self.data_dir)
        registry = HookRegistry(data_dir=self.data_dir)

        op_unmeasured = AIOperationRecord(
            operation_id="op_unmeas_2",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            credits_used=None,
            measurement_status=MeasurementStatus.UNAVAILABLE,
            jira_key="PROJ-20",
        )
        store.save_operation(op_unmeasured)

        registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type="event_driven",
            provider="kiro_ide",
            session_id="sess-unmeas-2",
            resulting_operation_id="op_unmeas_2",
            jira_key="PROJ-20",
            credits_used=None,
        )

        allocated = registry.allocate_estimated_credits(store)
        self.assertEqual(allocated, 0)

        refreshed = registry.get_executions(hook_id="kiro_posttooluse")[0]
        self.assertIsNone(refreshed.estimated_credits)
        self.assertIsNone(refreshed.allocation_method)

    def test_multiple_hooks_on_one_run_not_multiplying_actual_credits(self):
        """Multiple hooks correlated to a parent run divide the parent credits and never multiply credits."""
        store = AccountingStore(data_dir=self.data_dir)
        registry = HookRegistry(data_dir=self.data_dir)

        parent_op = AIOperationRecord(
            operation_id="op_parent_run",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            trigger_type=TriggerType.USER_INITIATED,
            credits_used=Decimal("0.600000"),
            measurement_status=MeasurementStatus.MEASURED,
            jira_key="PROJ-30",
        )
        store.save_operation(parent_op)

        for h_name in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
            registry.record_execution(
                hook_id=f"kiro_{h_name.lower()}",
                hook_name=h_name,
                trigger_type="event_driven",
                provider="kiro_ide",
                session_id="sess-multi-hook",
                resulting_operation_id="op_parent_run",
                jira_key="PROJ-30",
                credits_used=None,
            )

        allocated = registry.allocate_estimated_credits(store)
        self.assertEqual(allocated, 3)

        execs = registry.get_executions(session_id="sess-multi-hook")
        self.assertEqual(len(execs), 3)

        total_est = sum([e.estimated_credits for e in execs if e.estimated_credits is not None])
        for e in execs:
            self.assertEqual(e.estimated_credits, Decimal("0.200000"))
            self.assertEqual(e.allocation_method, "even_split_by_run")
            self.assertIsNone(e.credits_used)

        self.assertEqual(total_est, Decimal("0.600000"))

        aggregator = AccountingAggregator(store, hook_registry=registry)
        summary = aggregator.get_summary()

        self.assertEqual(summary["total_measured_credits_exact"], "0.600000")
        t_data = summary["jira_attribution"]["attributed_tickets"]["PROJ-30"]
        self.assertEqual(t_data["credits_exact"], "0.600000")
        self.assertEqual(t_data["hook_count"], 3)
        self.assertEqual(t_data["hook_estimated_credits_exact"], "0.600000")


    def test_posttooluse_correlation_resulting_operation_id(self):
        """PostToolUse hook execution must produce a valid resulting_operation_id matching the accounting store."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess_posttool_101",
            "tool_name": "readFile",
            "cwd": self.temp_dir.name,
        }
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                process_kiro_hook("PostToolUse", collector=collector)
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        # Verify operation was saved in accounting store
        ops = collector.accounting_store.get_operations(session_id="sess_posttool_101")
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertIsNotNone(op.operation_id)
        self.assertEqual(op.operation_type, OperationType.TOOL_TRIGGERED)
        self.assertEqual(op.trigger_type, TriggerType.HOOK_AUTOMATED)
        self.assertIsNone(op.credits_used)
        self.assertEqual(op.measurement_status, MeasurementStatus.UNAVAILABLE)

        # Verify hook registry has matching resulting_operation_id
        execs = collector.hook_registry.get_executions(session_id="sess_posttool_101")
        self.assertEqual(len(execs), 1)
        h_exec = execs[0]
        self.assertEqual(h_exec.resulting_operation_id, op.operation_id)
        self.assertEqual(h_exec.hook_name, "PostToolUse")

        # Verify dynamic registration in registry
        hooks = collector.hook_registry.get_registered_hooks()
        hook_ids = [h.hook_id for h in hooks]
        self.assertIn("kiro_posttooluse", hook_ids)
        target_hook = next(h for h in hooks if h.hook_id == "kiro_posttooluse")
        self.assertTrue(target_hook.metadata.get("dynamically_registered"))

    def test_posttooluse_with_jira_attribution_and_root_isolation(self):
        """PostToolUse with git context resolves active Jira ticket on first write and respects root boundaries."""
        repo_dir = Path(self.temp_dir.name) / "my_project"
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True, check=True)

        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            f"MEMBER_ID=M_TEST\nDEVICE_ID=DEV_TEST\nTELEMETRY_DATA_DIR={self.data_dir}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        settings = load_settings(env_file=str(env_file))
        collector = TelemetryCollector(settings)

        # Active ticket for my_project
        collector.attribution_store.select_ticket(
            issue_key="TEST-88",
            repo_path=str(repo_dir),
            source="manual",
            allow_unverified=True,
            confirmation_state="confirmed",
        )

        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess_posttool_jira",
            "tool_name": "executeBash",
            "cwd": str(repo_dir),
        }
        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            sys.stdin = io.StringIO(json.dumps(payload))
            sys.stdout = io.StringIO()
            with self.assertRaises(SystemExit) as cm:
                process_kiro_hook("PostToolUse", collector=collector)
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        ops = collector.accounting_store.get_operations(session_id="sess_posttool_jira")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].jira_key, "TEST-88")

        execs = collector.hook_registry.get_executions(session_id="sess_posttool_jira")
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].jira_key, "TEST-88")
        self.assertEqual(execs[0].resulting_operation_id, ops[0].operation_id)

    def test_kiro_hook_status_detects_custom_path_and_env_var(self):
        """get_kiro_hook_status finds configuration in custom locations and env vars."""
        custom_hook_file = Path(self.temp_dir.name) / "custom_hooks.json"
        install_kiro_hooks(hooks_file=custom_hook_file)

        # 1. Via explicit path
        status1 = get_kiro_hook_status(hooks_file=custom_hook_file)
        self.assertTrue(status1["config_exists"])
        self.assertTrue(status1["telemetry_hook_installed"])

        # 2. Via KIRO_HOOKS_PATH env var
        import os
        old_env = os.environ.get("KIRO_HOOKS_PATH")
        os.environ["KIRO_HOOKS_PATH"] = str(custom_hook_file)
        try:
            status2 = get_kiro_hook_status()
            self.assertTrue(status2["config_exists"])
            self.assertTrue(status2["telemetry_hook_installed"])
            self.assertEqual(Path(status2["config_path"]).resolve(), custom_hook_file.resolve())
        finally:
            if old_env is not None:
                os.environ["KIRO_HOOKS_PATH"] = old_env
            else:
                os.environ.pop("KIRO_HOOKS_PATH", None)

    def test_custom_data_dir_resolution_parity(self):
        """HookRegistry, AccountingStore, and AttributionStore read/write identical files under custom data_dir."""
        custom_dir = Path(self.temp_dir.name) / "custom_data"
        custom_dir.mkdir(parents=True, exist_ok=True)

        # Set env var
        import os
        old_val = os.environ.get("TELEMETRY_DATA_DIR")
        os.environ["TELEMETRY_DATA_DIR"] = str(custom_dir)
        try:
            store = AccountingStore()
            self.assertEqual(store.data_dir.resolve(), custom_dir.resolve())
            registry = HookRegistry()
            self.assertEqual(registry.data_dir.resolve(), custom_dir.resolve())
            attr_store = AttributionStore()
            self.assertEqual(attr_store.data_dir.resolve(), custom_dir.resolve())
            event_store = JSONLEventStore()
            self.assertEqual(event_store.data_dir.resolve(), custom_dir.resolve())
        finally:
            if old_val is not None:
                os.environ["TELEMETRY_DATA_DIR"] = old_val
            else:
                os.environ.pop("TELEMETRY_DATA_DIR", None)

    def test_source_and_installed_package_parity(self):
        """The installed ai_telemetry_agent package in .venv matches the repository source."""
        import ai_telemetry_agent
        pkg_file = Path(ai_telemetry_agent.__file__).resolve()
        repo_src = Path(__file__).resolve().parent.parent / "src" / "ai_telemetry_agent" / "__init__.py"
        self.assertEqual(pkg_file, repo_src.resolve(), "Virtualenv must use editable or identical source package")


class TestExplicitParentCorrelationAndAccounting(unittest.TestCase):
    """Regression test suite for explicit parent AI-run correlation and credit estimation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.store = AccountingStore(self.data_dir)
        self.registry = HookRegistry(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_positive_explicit_parent_correlation(self):
        """Hook explicitly correlated to measured parent AI-run receives estimated credits and retains parent integrity."""
        parent_op = AIOperationRecord(
            operation_id="parent_run_123",
            provider="kiro_ide",
            source="messages_jsonl",
            operation_type=OperationType.PROMPT,
            session_id="sess_abc",
            credits_used=Decimal("1.250000"),
            jira_key="PROJ-100",
            repository={"name": "telemetry-agent", "root": "/workspace"},
        )
        self.store.save_operation(parent_op)

        hook = self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id="parent_run_123",
            jira_key="PROJ-100",
            metadata={"repository": {"name": "telemetry-agent"}},
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 1)

        execs = self.registry.get_executions()
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0].estimated_credits, Decimal("1.250000"))
        self.assertEqual(execs[0].correlation_status, "correlated")
        self.assertEqual(execs[0].correlation_reason, "verified_parent")
        self.assertEqual(execs[0].allocation_method, "even_split_by_run")

        # Measured credits on parent must NEVER be altered
        refreshed_parent = self.store.get_operation_by_id("parent_run_123")
        self.assertEqual(refreshed_parent.credits_used, Decimal("1.250000"))

    def test_ineligible_missing_parent(self):
        """Hook without parent_operation_id must remain unallocated with missing_parent_operation reason."""
        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id=None,
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ineligible")
        self.assertEqual(execs[0].correlation_reason, "missing_parent_operation")

    def test_ineligible_parent_not_in_store(self):
        """Hook with non-existent parent_operation_id remains unallocated with missing_parent_operation reason."""
        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id="non_existent_op_404",
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ineligible")
        self.assertEqual(execs[0].correlation_reason, "missing_parent_operation")

    def test_ineligible_parent_unmeasured(self):
        """Hook whose parent has no measured credits (None or 0) remains unallocated with parent_unmeasured reason."""
        parent_op = AIOperationRecord(
            operation_id="parent_unmeas_1",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_abc",
            credits_used=None,
            measurement_status=MeasurementStatus.UNAVAILABLE,
        )
        self.store.save_operation(parent_op)

        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id="parent_unmeas_1",
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ineligible")
        self.assertEqual(execs[0].correlation_reason, "parent_unmeasured")

    def test_ineligible_session_mismatch(self):
        """Hook with conflicting session_id against parent run remains unallocated with incompatible_context reason."""
        parent_op = AIOperationRecord(
            operation_id="parent_run_sess",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_REAL",
            credits_used=Decimal("2.000000"),
        )
        self.store.save_operation(parent_op)

        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_OTHER",
            parent_operation_id="parent_run_sess",
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ineligible")
        self.assertEqual(execs[0].correlation_reason, "incompatible_context")

    def test_ineligible_ticket_mismatch(self):
        """Hook attributed to PROJ-148 must not receive allocation from parent attributed to SCRUM-1."""
        parent_op = AIOperationRecord(
            operation_id="parent_scrum_1",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_abc",
            credits_used=Decimal("3.000000"),
            jira_key="SCRUM-1",
        )
        self.store.save_operation(parent_op)

        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id="parent_scrum_1",
            jira_key="PROJ-148",
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ineligible")
        self.assertEqual(execs[0].correlation_reason, "ticket_mismatch")

    def test_ambiguous_correlation(self):
        """Hook flagged with ambiguous correlation remains unallocated with ambiguous_correlation reason."""
        parent_op = AIOperationRecord(
            operation_id="parent_op_ambig",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_abc",
            credits_used=Decimal("1.000000"),
        )
        self.store.save_operation(parent_op)

        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_abc",
            parent_operation_id="parent_op_ambig",
            correlation_status="ambiguous",
            correlation_reason="ambiguous_correlation",
        )

        allocated = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated, 0)

        execs = self.registry.get_executions()
        self.assertIsNone(execs[0].estimated_credits)
        self.assertEqual(execs[0].correlation_status, "ambiguous")
        self.assertEqual(execs[0].correlation_reason, "ambiguous_correlation")

    def test_multi_hook_even_split_and_idempotency(self):
        """Multiple hooks correlated to one parent evenly divide the parent's credits idempotently without inflation."""
        parent_op = AIOperationRecord(
            operation_id="parent_run_multi",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_split",
            credits_used=Decimal("1.000000"),
            jira_key="PROJ-200",
        )
        self.store.save_operation(parent_op)

        self.registry.record_execution(
            hook_id="kiro_pretooluse",
            hook_name="PreToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_split",
            parent_operation_id="parent_run_multi",
            jira_key="PROJ-200",
        )
        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_split",
            parent_operation_id="parent_run_multi",
            jira_key="PROJ-200",
        )

        # First run
        allocated_1 = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated_1, 2)

        execs_1 = self.registry.get_executions()
        self.assertEqual(execs_1[0].estimated_credits, Decimal("0.500000"))
        self.assertEqual(execs_1[1].estimated_credits, Decimal("0.500000"))
        self.assertEqual(execs_1[0].estimated_credits + execs_1[1].estimated_credits, Decimal("1.000000"))

        # Second run (idempotency check)
        allocated_2 = self.registry.allocate_estimated_credits(self.store)
        self.assertEqual(allocated_2, 2)

        execs_2 = self.registry.get_executions()
        self.assertEqual(execs_2[0].estimated_credits, Decimal("0.500000"))
        self.assertEqual(execs_2[1].estimated_credits, Decimal("0.500000"))
        self.assertEqual(execs_2[0].estimated_credits + execs_2[1].estimated_credits, Decimal("1.000000"))

        # Parent credits unchanged
        parent_check = self.store.get_operation_by_id("parent_run_multi")
        self.assertEqual(parent_check.credits_used, Decimal("1.000000"))

    def test_summary_and_cli_diagnostics(self):
        """Aggregator get_summary exposes hook_diagnostics with counts and unallocated reasons."""
        parent_op = AIOperationRecord(
            operation_id="parent_run_diag",
            provider="kiro_ide",
            source="messages_jsonl",
            session_id="sess_diag",
            credits_used=Decimal("2.500000"),
        )
        self.store.save_operation(parent_op)

        # 1 eligible hook
        self.registry.record_execution(
            hook_id="kiro_posttooluse",
            hook_name="PostToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_diag",
            parent_operation_id="parent_run_diag",
        )
        # 1 ineligible hook
        self.registry.record_execution(
            hook_id="kiro_pretooluse",
            hook_name="PreToolUse",
            trigger_type=TriggerType.EVENT_TRIGGERED,
            provider="kiro_ide",
            session_id="sess_diag",
            parent_operation_id=None,
        )

        self.registry.allocate_estimated_credits(self.store)

        aggregator = AccountingAggregator(self.store, hook_registry=self.registry)
        summary = aggregator.get_summary()

        self.assertIn("hook_diagnostics", summary)
        diag = summary["hook_diagnostics"]
        self.assertEqual(diag["eligible_count"], 1)
        self.assertEqual(diag["ineligible_count"], 1)
        self.assertEqual(diag["ambiguous_count"], 0)
        self.assertEqual(diag["total_estimated_credits_exact"], "2.500000")
        self.assertIn("missing_parent_operation", diag["by_reason"])
        self.assertEqual(diag["parent_operation_ids"], ["parent_run_diag"])

    def test_backward_compatibility_deserialization(self):
        """Older serialized records without correlation fields deserialize with None defaults."""
        legacy_data = {
            "execution_id": "legacy_exec_1",
            "hook_id": "legacy_hook",
            "hook_name": "LegacyHook",
            "executed_at": "2026-10-18T10:00:00Z",
            "trigger_type": "event_driven",
            "provider": "kiro_ide",
        }
        record = HookExecutionRecord.from_dict(legacy_data)
        self.assertIsNone(record.parent_operation_id)
        self.assertIsNone(record.correlation_status)
        self.assertIsNone(record.correlation_reason)
        self.assertIsNone(record.estimated_credits)

        dict_out = record.to_dict()
        self.assertIsNone(dict_out["parent_operation_id"])
        self.assertIsNone(dict_out["correlation_status"])
        self.assertIsNone(dict_out["correlation_reason"])




if __name__ == "__main__":
    unittest.main()

