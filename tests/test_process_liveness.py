"""Unit tests for process liveness verification, stale PID handling, and source selection."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_telemetry_agent.cli import build_parser, cmd_status, cmd_stop
from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.collector import (
    TelemetryCollector,
    check_process_liveness,
    get_collector_status,
)


class TestProcessLiveness(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.data_dir / "collector.pid"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_check_process_liveness_current_process(self):
        """Current process PID must be detected as alive."""
        self.assertTrue(check_process_liveness(os.getpid()))

    def test_check_process_liveness_invalid_pids(self):
        """Negative, zero, and impossibly high PIDs must return False."""
        self.assertFalse(check_process_liveness(-1))
        self.assertFalse(check_process_liveness(0))
        # An absurdly high PID that definitely does not exist
        self.assertFalse(check_process_liveness(99999999))

    def test_get_collector_status_no_file(self):
        """When no PID file exists, status is (False, None)."""
        is_running, pid = get_collector_status(self.data_dir)
        self.assertFalse(is_running)
        self.assertIsNone(pid)

    def test_get_collector_status_live_process(self):
        """When PID file has current process PID, status is (True, pid) and file is kept."""
        self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        is_running, pid = get_collector_status(self.data_dir, cleanup_stale=True)
        self.assertTrue(is_running)
        self.assertEqual(pid, os.getpid())
        self.assertTrue(self.pid_file.exists())

    def test_get_collector_status_stale_pid_cleanup(self):
        """When PID file has dead PID, status is (False, None) and stale file is deleted."""
        # 99999999 does not exist
        self.pid_file.write_text("99999999", encoding="utf-8")
        is_running, pid = get_collector_status(self.data_dir, cleanup_stale=True)
        self.assertFalse(is_running)
        self.assertIsNone(pid)
        self.assertFalse(self.pid_file.exists())

    def test_get_collector_status_corrupted_pid_file(self):
        """When PID file contains non-integer garbage, it is cleaned up safely."""
        self.pid_file.write_text("invalid_pid_text", encoding="utf-8")
        is_running, pid = get_collector_status(self.data_dir, cleanup_stale=True)
        self.assertFalse(is_running)
        self.assertIsNone(pid)
        self.assertFalse(self.pid_file.exists())

    def test_cmd_stop_handles_stale_pid_safely(self):
        """cmd_stop must remove stale PID file without throwing or terminating unrelated processes."""
        self.pid_file.write_text("99999999", encoding="utf-8")
        parser = build_parser()
        args = parser.parse_args(["stop"])
        # Mock load_settings to point to our test data_dir
        dummy_settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(self.data_dir),
        )
        with patch("ai_telemetry_agent.cli.load_settings", return_value=dummy_settings):
            cmd_stop(args)

        self.assertFalse(self.pid_file.exists())


class TestSourceSelection(unittest.TestCase):
    def test_cli_parser_start_source_argument(self):
        """The start CLI command must accept --source {antigravity,kiro_cli,kiro_ide,auto}."""
        parser = build_parser()
        for src in ["antigravity", "kiro_cli", "kiro_ide", "auto"]:
            args = parser.parse_args(["start", "--source", src])
            self.assertEqual(args.source, src)

    def test_collector_registers_kiro_cli_when_configured(self):
        """When telemetry_source is kiro_cli, KiroCLISessionSource is in active sources."""
        temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(temp_dir.name) / "data"
        settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="kiro_cli",
        )
        collector = TelemetryCollector(settings)
        source_names = [s.name for s in collector.sources]
        self.assertIn("kiro_cli", source_names)
        self.assertNotIn("antigravity", source_names)
        self.assertTrue(collector.kiro_cli_source.enabled)
        temp_dir.cleanup()

    def test_collector_registers_antigravity_when_default(self):
        """When telemetry_source is antigravity, AntigravitySource is in active sources."""
        temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(temp_dir.name) / "data"
        settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="antigravity",
        )
        collector = TelemetryCollector(settings)
        source_names = [s.name for s in collector.sources]
        self.assertIn("antigravity", source_names)
        self.assertNotIn("kiro_cli", source_names)
        self.assertTrue(collector.antigravity_source.enabled)
        self.assertFalse(collector.kiro_cli_source.enabled)
        temp_dir.cleanup()

    def test_kiro_cli_jira_attribution_e2e(self):
        """End-to-end verification: Kiro CLI session is ingested, attributed to SCRUM-1, and summarized accurately."""
        import json
        from ai_telemetry_agent.attribution.aggregator import TicketAggregator
        from ai_telemetry_agent.attribution.store import AttributionStore
        from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore

        temp_dir = tempfile.TemporaryDirectory()
        base_path = Path(temp_dir.name)
        data_dir = base_path / "data"
        kiro_cli_dir = base_path / "sessions" / "cli"
        kiro_cli_dir.mkdir(parents=True, exist_ok=True)
        repo_dir = base_path / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        import subprocess
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)

        # 1. Initialize attribution store and assign SCRUM-1 to repository
        attr_store = AttributionStore(data_dir=data_dir)
        attr = attr_store.select_ticket("SCRUM-1", repo_path=repo_dir, source="manual")
        attr.start_time = "2020-01-01T00:00:00Z"
        attr_store._save()
        self.assertEqual(attr.issue_key, "SCRUM-1")
        self.assertEqual(attr.confirmation_state, "confirmed")

        # 2. Write real Kiro CLI session JSON with 2 completed turns
        session_id = "kiro-demo-session-scrum1"
        session_file = kiro_cli_dir / f"{session_id}.json"
        raw_session_data = {
            "session_id": session_id,
            "cwd": str(repo_dir),
            "updated_at": "2026-10-16T08:00:00Z",
            "session_state": {
                "id": session_id,
                "cwd": str(repo_dir),
                "updated_at": "2026-10-16T08:00:00Z",
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-scrum1-001",
                                    "user_turn_metadata": {
                                        "turn_duration": {"secs": 2, "nanos": 500000000},
                                        "metering_usage": [{"value": 0.05, "unit": "credit"}],
                                        "context_usage_percentage": 5.2,
                                        "input_token_count": 0,
                                        "output_token_count": 0,
                                    }
                                }
                            },
                            "timestamp": "2026-10-16T08:00:05Z",
                            "selected_model": "claude-haiku-4.5",
                        },
                        {
                            "result": {
                                "Ok": {
                                    "id": "turn-scrum1-002",
                                    "user_turn_metadata": {
                                        "turn_duration": {"secs": 1, "nanos": 200000000},
                                        "metering_usage": [{"value": 0.03, "unit": "credit"}],
                                        "context_usage_percentage": 5.8,
                                        "input_token_count": 0,
                                        "output_token_count": 0,
                                    }
                                }
                            },
                            "timestamp": "2026-10-16T08:00:10Z",
                            "selected_model": "claude-haiku-4.5",
                        }
                    ]
                }
            }
        }
        # 3. Configure collector with kiro_cli mode and point to test directories
        settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="kiro_cli",
            kiro_sessions_dir=str(kiro_cli_dir),
            git_monitor_paths=[str(repo_dir)],
        )
        collector = TelemetryCollector(settings)
        collector.start()

        # Write session file while collector is running
        session_file.write_text(json.dumps(raw_session_data), encoding="utf-8")

        # Ingest session
        new_count = collector.poll_and_process()
        self.assertGreaterEqual(new_count, 2)

        # 4. Verify stored events have jira_task_id = SCRUM-1
        event_store = JSONLEventStore(data_dir)
        events = event_store.get_events(limit=50)
        kiro_runs = [e for e in events if e.event_type == "ai_run_completed" and e.payload.get("provider") == "kiro_cli"]
        self.assertEqual(len(kiro_runs), 2)
        for r in kiro_runs:
            self.assertEqual(r.context.jira_task_id, "SCRUM-1")
            self.assertIsNone(r.payload.get("input_tokens"))
            self.assertIsNone(r.payload.get("output_tokens"))

        # 5. Verify TicketAggregator aggregates accurately under SCRUM-1
        aggregator = TicketAggregator(event_store, attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("SCRUM-1", summaries)
        s_scrum1 = summaries["SCRUM-1"]
        self.assertEqual(s_scrum1.issue_key, "SCRUM-1")
        self.assertEqual(s_scrum1.sessions_count, 1)
        self.assertEqual(s_scrum1.ai_runs_count, 2)
        self.assertAlmostEqual(s_scrum1.total_credits, 0.08, places=3)
        self.assertTrue(s_scrum1.has_credit_data)
        self.assertEqual(s_scrum1.session_ids, [session_id])

        # 6. Verify deduplication on subsequent poll
        second_poll = collector.poll_and_process()
        self.assertEqual(second_poll, 0)

        collector.stop()
        temp_dir.cleanup()

    def test_extract_telemetry_source_from_cmdline(self):
        """extract_telemetry_source_from_cmdline correctly extracts all supported source values."""
        from ai_telemetry_agent.core.collector import extract_telemetry_source_from_cmdline

        self.assertEqual(
            extract_telemetry_source_from_cmdline('"python.exe" -m ai_telemetry_agent start --source kiro_cli'),
            "kiro_cli",
        )
        self.assertEqual(
            extract_telemetry_source_from_cmdline('python -m ai_telemetry_agent start --source=kiro_ide'),
            "kiro_ide",
        )
        self.assertEqual(
            extract_telemetry_source_from_cmdline('python -m ai_telemetry_agent start --telemetry-source auto'),
            "auto",
        )
        self.assertEqual(
            extract_telemetry_source_from_cmdline('python -m ai_telemetry_agent start --source antigravity'),
            "antigravity",
        )
        self.assertIsNone(
            extract_telemetry_source_from_cmdline('python -m ai_telemetry_agent start'),
        )
        self.assertIsNone(
            extract_telemetry_source_from_cmdline(''),
        )

    def test_status_reports_effective_source_from_running_collector(self):
        """cmd_status reports the running collector's effective source (e.g. kiro_cli) even when global config defaults to antigravity."""
        from io import StringIO
        import json

        temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(temp_dir.name) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        # 1. Global config settings default to antigravity
        default_settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="antigravity",
        )

        # 2. Start a collector with kiro_cli override
        kiro_settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="kiro_cli",
        )
        collector = TelemetryCollector(kiro_settings)
        collector.start()

        self.assertTrue((data_dir / "collector.pid").exists())
        self.assertTrue((data_dir / "collector_meta.json").exists())

        # Verify collector_meta.json has kiro_cli
        with open(data_dir / "collector_meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta.get("telemetry_source"), "kiro_cli")

        # 3. Invoke cmd_status using default_settings (which has telemetry_source="antigravity")
        parser = build_parser()
        status_args = parser.parse_args(["status"])

        with patch("ai_telemetry_agent.cli.load_settings", return_value=default_settings):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                cmd_status(status_args)
                output = mock_stdout.getvalue()

        # Must report kiro_cli, not antigravity!
        self.assertIn("Process Status     : RUNNING", output)
        self.assertIn("Telemetry Source   : kiro_cli", output)
        self.assertNotIn("Telemetry Source   : antigravity", output)

        # 4. Stop collector and verify status now falls back to default_settings when stopped
        collector.stop()
        self.assertFalse((data_dir / "collector.pid").exists())
        self.assertFalse((data_dir / "collector_meta.json").exists())

        with patch("ai_telemetry_agent.cli.load_settings", return_value=default_settings):
            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                cmd_status(status_args)
                output_stopped = mock_stdout.getvalue()

        self.assertIn("Process Status     : STOPPED", output_stopped)
        self.assertIn("Telemetry Source   : antigravity", output_stopped)

        temp_dir.cleanup()

    def test_status_resolves_effective_source_from_process_cmdline_fallback(self):
        """When collector_meta.json is missing but process is running with --source kiro_cli, cmd_status extracts source from cmdline."""
        from io import StringIO

        temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(temp_dir.name) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        pid_file = data_dir / "collector.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        default_settings = Settings(
            member_id="M_TEST",
            device_id="DEV_TEST",
            telemetry_data_dir=str(data_dir),
            telemetry_source="antigravity",
        )

        parser = build_parser()
        status_args = parser.parse_args(["status"])

        fake_cmdline = f'"python.exe" -m ai_telemetry_agent start --source kiro_cli'

        with patch("ai_telemetry_agent.cli.load_settings", return_value=default_settings):
            with patch("ai_telemetry_agent.cli.get_running_process_cmdline", return_value=fake_cmdline):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    cmd_status(status_args)
                    output = mock_stdout.getvalue()

        self.assertIn("Telemetry Source   : kiro_cli", output)

        temp_dir.cleanup()

