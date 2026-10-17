"""Comprehensive automated tests for Jira ticket attribution subsystem."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional, Tuple, Union
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from ai_telemetry_agent.attribution.aggregator import TicketAggregator
from ai_telemetry_agent.attribution.detector import (
    DEFAULT_JIRA_KEY_PATTERN,
    detect_branch_ticket,
    extract_issue_keys,
    validate_ticket_key,
)
from ai_telemetry_agent.attribution.jira_adapter import (
    GenericJiraRestAdapter,
    JiraIssueInfo,
    NullJiraAdapter,
    get_jira_adapter_from_env,
    normalize_jira_base_url,
)
from ai_telemetry_agent.attribution.models import (
    BranchDetectionResult,
    TicketAttribution,
    TicketSummary,
)
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import (
    CollectorInfo,
    ContextInfo,
    EventType,
    IdentityInfo,
    RepositoryInfo,
    TelemetryEvent,
)
from ai_telemetry_agent.core.identity import LocalIdentityProvider
from ai_telemetry_agent.core.processor import EventProcessor
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore


class TestBranchDetection(unittest.TestCase):
    """Requirements 1, 2, and 10: Issue key extraction, ambiguous keys, and graceful git error handling."""

    def test_req1_extraction_from_common_branch_naming(self):
        """Extract PROJ-148 from various branch naming conventions."""
        conventions = [
            ("feature/PROJ-148-login", ["PROJ-148"]),
            ("bugfix/PROJ-162-timeout", ["PROJ-162"]),
            ("PROJ-148", ["PROJ-148"]),
            ("users/developer/PROJ-148-fix", ["PROJ-148"]),
            ("PROJ-148-feat-something", ["PROJ-148"]),
            ("release/PROJ-148_patch", ["PROJ-148"]),
        ]
        for branch_name, expected in conventions:
            keys = extract_issue_keys(branch_name)
            self.assertEqual(keys, expected, f"Failed extracting from '{branch_name}'")

    def test_req2_invalid_or_ambiguous_branch_keys(self):
        """Ambiguous (conflicting) or invalid branch names must be detected appropriately."""
        # Ambiguous / conflicting: multiple distinct keys
        conflicting = extract_issue_keys("feature/PROJ-148-and-PROJ-162")
        self.assertEqual(len(conflicting), 2)
        self.assertIn("PROJ-148", conflicting)
        self.assertIn("PROJ-162", conflicting)

        # No keys in main/master/misc
        self.assertEqual(extract_issue_keys("main"), [])
        self.assertEqual(extract_issue_keys("master"), [])
        self.assertEqual(extract_issue_keys("develop"), [])
        self.assertEqual(extract_issue_keys("fix-broken-tests"), [])
        self.assertEqual(extract_issue_keys("v1.2.3"), [])

        # Duplicate keys in branch are deduplicated
        self.assertEqual(extract_issue_keys("PROJ-148/fix-PROJ-148"), ["PROJ-148"])

        # Ticket syntax validation
        valid, key = validate_ticket_key("PROJ-148")
        self.assertTrue(valid)
        self.assertEqual(key, "PROJ-148")

        valid_lower, key_lower = validate_ticket_key("proj-148")
        self.assertTrue(valid_lower)
        self.assertEqual(key_lower, "PROJ-148")

        self.assertFalse(validate_ticket_key("")[0])
        self.assertFalse(validate_ticket_key("148")[0])
        self.assertFalse(validate_ticket_key("PROJ")[0])
        self.assertFalse(validate_ticket_key("PROJ-")[0])

    def test_req10_no_git_or_subprocess_failure(self):
        """Degrade gracefully when git is unavailable, target is not a repo, or subprocess fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. Non-git directory
            res = detect_branch_ticket(temp_dir)
            self.assertEqual(res.status, "none")
            self.assertIsNone(res.suggested_key)
            self.assertEqual(res.keys, [])

            # 2. Simulated git error
            with patch("ai_telemetry_agent.attribution.detector.detect_repository_context") as mock_det:
                mock_det.side_effect = RuntimeError("git command failed: fatal error")
                res_err = detect_branch_ticket(temp_dir)
                self.assertEqual(res_err.status, "error")
                self.assertIn("git command failed", res_err.error)


class TestAttributionStoreAndBranchRules(unittest.TestCase):
    """Requirements 3, 4, 5, 6, 7, 9, 12: Working context, branch detection rules, persistence, and repo isolation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.store = AttributionStore(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_req3_working_on_main_with_existing_association(self):
        """Working on main with an existing PROJ-148 association must preserve it."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        # Select ticket PROJ-148 while on main
        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            with patch("ai_telemetry_agent.attribution.store.detect_branch_ticket") as mock_dt:
                mock_dt.return_value = BranchDetectionResult(keys=[], suggested_key=None, status="none", branch="main")

                attr = self.store.select_ticket("PROJ-148", repo_path=repo_dir, source="manual")
                self.assertEqual(attr.issue_key, "PROJ-148")
                self.assertEqual(attr.confirmation_state, "confirmed")

                # Sync branch while still on main
                active, proposed = self.store.sync_branch(repo_dir)
                self.assertIsNotNone(active)
                self.assertEqual(active.issue_key, "PROJ-148")
                self.assertIsNone(proposed)

    def test_req4_selecting_on_main_then_creating_branch(self):
        """Selecting ticket on main, then creating feature/PROJ-148-login must keep the association."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        # 1. On main, select PROJ-148
        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            self.store.select_ticket("PROJ-148", repo_path=repo_dir, source="manual")

        # 2. Branch switches to feature/PROJ-148-login (key agrees)
        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "feature/PROJ-148-login")):
            with patch("ai_telemetry_agent.attribution.store.detect_branch_ticket") as mock_dt:
                mock_dt.return_value = BranchDetectionResult(
                    keys=["PROJ-148"], suggested_key="PROJ-148", status="single", branch="feature/PROJ-148-login"
                )
                active, proposed = self.store.sync_branch(repo_dir)
                self.assertIsNotNone(active)
                self.assertEqual(active.issue_key, "PROJ-148")
                self.assertEqual(active.confirmation_state, "confirmed")
                self.assertIsNone(proposed)
                self.assertEqual(active.branch, "feature/PROJ-148-login")

    def test_req5_switching_from_proj_148_to_proj_162(self):
        """Explicitly switching from PROJ-148 to PROJ-162 closes previous and starts new association."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr1 = self.store.select_ticket("PROJ-148", repo_path=repo_dir, source="manual")
            self.assertEqual(attr1.issue_key, "PROJ-148")
            self.assertIsNone(attr1.end_time)

            attr2 = self.store.select_ticket("PROJ-162", repo_path=repo_dir, source="manual")
            self.assertEqual(attr2.issue_key, "PROJ-162")
            self.assertIsNone(attr2.end_time)

            # Check that attr1 is now closed
            all_attrs = self.store.get_all_attributions()
            a1_record = [a for a in all_attrs if a.id == attr1.id][0]
            self.assertIsNotNone(a1_record.end_time)

    def test_req6_conflicting_branch_keys_require_confirmation(self):
        """When active association is PROJ-148 and branch has PROJ-162, propose switch without silent overwrite."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        # Active is PROJ-148
        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            self.store.select_ticket("PROJ-148", repo_path=repo_dir)

        # Developer switches branch to bugfix/PROJ-162-timeout
        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "bugfix/PROJ-162-timeout")):
            with patch("ai_telemetry_agent.attribution.store.detect_branch_ticket") as mock_dt:
                mock_dt.return_value = BranchDetectionResult(
                    keys=["PROJ-162"], suggested_key="PROJ-162", status="single", branch="bugfix/PROJ-162-timeout"
                )
                active, proposed = self.store.sync_branch(repo_dir)
                # Active must remain PROJ-148
                self.assertEqual(active.issue_key, "PROJ-148")
                # Proposed switch must be PROJ-162
                self.assertEqual(proposed, "PROJ-162")

                # Verify pending proposed switch is queryable
                self.assertEqual(self.store.get_proposed_switch(repo_dir), "PROJ-162")

                # Confirm switch
                confirmed = self.store.confirm_switch(repo_dir)
                self.assertIsNotNone(confirmed)
                self.assertEqual(confirmed.issue_key, "PROJ-162")
                self.assertIsNone(self.store.get_proposed_switch(repo_dir))

    def test_req7_persistence_across_collector_restart(self):
        """Ticket association persists across collector restarts in isolated file."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            self.store.select_ticket("PROJ-148", repo_path=repo_dir)

        # Instantiate a completely new AttributionStore from the same data_dir (simulating restart)
        new_store = AttributionStore(self.data_dir)
        with patch.object(new_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            active = new_store.get_active_attribution(repo_dir)
            self.assertIsNotNone(active)
            self.assertEqual(active.issue_key, "PROJ-148")
            self.assertEqual(active.confirmation_state, "confirmed")

    def test_req9_separate_repositories_with_different_tickets(self):
        """Repo A and Repo B maintain separate, independent active tickets."""
        repo_a = self.data_dir / "repo_a"
        repo_b = self.data_dir / "repo_b"
        repo_a.mkdir()
        repo_b.mkdir()

        with patch.object(self.store, "_resolve_repo", side_effect=lambda p: (str(p), Path(p).name, "main")):
            self.store.select_ticket("PROJ-148", repo_path=repo_a)
            self.store.select_ticket("PROJ-162", repo_path=repo_b)

            active_a = self.store.get_active_attribution(repo_a)
            active_b = self.store.get_active_attribution(repo_b)

            self.assertEqual(active_a.issue_key, "PROJ-148")
            self.assertEqual(active_b.issue_key, "PROJ-162")

    def test_req12_explicit_unassigned_state(self):
        """Clearing the ticket explicitly marks work as UNASSIGNED."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        with patch.object(self.store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            self.store.select_ticket("PROJ-148", repo_path=repo_dir)
            self.assertIsNotNone(self.store.get_active_attribution(repo_dir))

            self.store.clear_ticket(repo_dir)
            self.assertIsNone(self.store.get_active_attribution(repo_dir))


class TestUsageAttributionAndAggregation(unittest.TestCase):
    """Requirements 8, 13, 14, 15, 16: Usage attribution boundaries, no double counting, unknown tokens, and historical reading."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.event_store = JSONLEventStore(self.data_dir)
        self.attr_store = AttributionStore(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_event(
        self,
        event_id: str,
        timestamp: str,
        session_id: str,
        repo_name: str,
        credits: Optional[float] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> TelemetryEvent:
        return TelemetryEvent(
            event_id=event_id,
            event_type="ai_run_completed",
            timestamp=timestamp,
            collector=CollectorInfo(version="0.1.2", device_id="dev-123"),
            identity=IdentityInfo(member_id="dev1"),
            context=ContextInfo(
                session_id=session_id,
                repository=RepositoryInfo(name=repo_name, branch="main", root=f"/repos/{repo_name}"),
            ),
            payload={
                "model": "claude-3-sonnet",
                "credits": credits,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": (input_tokens + output_tokens) if (input_tokens is not None and output_tokens is not None) else None,
            },
        )

    def test_req8_and_14_multiple_kiro_sessions_and_no_double_counting(self):
        """Multiple sessions in same repository and working context attributed without double counting."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        # Associate PROJ-148 with my_repo
        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-148", repo_path=repo_dir)
            # Ensure start_time covers sessions
            attr.start_time = "2026-10-14T09:00:00Z"
            self.attr_store._save()

        # Session 1: 2.5 credits
        ev1 = self._create_event("ev-1", "2026-10-14T10:00:00Z", "sess-1", "my_repo", credits=2.5)
        self.event_store.save_event(ev1)

        # Session 2: 1.5 credits
        ev2 = self._create_event("ev-2", "2026-10-14T11:00:00Z", "sess-2", "my_repo", credits=1.5)
        self.event_store.save_event(ev2)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("PROJ-148", summaries)
        s = summaries["PROJ-148"]
        self.assertEqual(s.sessions_count, 2)
        self.assertAlmostEqual(s.total_credits, 4.0, places=3)
        self.assertIn("sess-1", s.session_ids)
        self.assertIn("sess-2", s.session_ids)

        # Strict credit reconciliation
        total_session_credits = sum(x["total_credits"] for x in self.event_store.get_sessions())
        total_summary_credits = sum(x.total_credits for x in summaries.values())
        self.assertAlmostEqual(total_session_credits, total_summary_credits, places=3)

    def test_req13_attribution_boundaries_no_retroactive_reassignment(self):
        """Historical usage prior to ticket selection is not retroactively reassigned."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir()

        # Usage at T0 (08:00:00Z) before any ticket was selected
        ev_old = self._create_event("ev-old", "2026-10-14T08:00:00Z", "sess-old", "my_repo", credits=1.0)
        self.event_store.save_event(ev_old)

        # User selects PROJ-148 at T1 (10:00:00Z) to T3 (12:00:00Z)
        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr1 = self.attr_store.select_ticket("PROJ-148", repo_path=repo_dir)
            attr1.start_time = "2026-10-14T10:00:00Z"
            attr1.end_time = "2026-10-14T12:00:00Z"

            # User selects PROJ-162 at T3 (12:00:00Z)
            attr2 = self.attr_store.select_ticket("PROJ-162", repo_path=repo_dir)
            attr2.start_time = "2026-10-14T12:00:00Z"
            attr2.end_time = None
            self.attr_store._save()

        # Usage at T2 (11:00:00Z) during PROJ-148
        ev_proj148 = self._create_event("ev-148", "2026-10-14T11:00:00Z", "sess-148", "my_repo", credits=2.0)
        self.event_store.save_event(ev_proj148)

        # Usage at T4 (13:00:00Z) during PROJ-162
        ev_proj162 = self._create_event("ev-162", "2026-10-14T13:00:00Z", "sess-162", "my_repo", credits=3.0)
        self.event_store.save_event(ev_proj162)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        # ev-old must be in UNASSIGNED
        self.assertIn("UNASSIGNED", summaries)
        self.assertEqual(summaries["UNASSIGNED"].sessions_count, 1)
        self.assertAlmostEqual(summaries["UNASSIGNED"].total_credits, 1.0)

        # ev-148 must be in PROJ-148
        self.assertIn("PROJ-148", summaries)
        self.assertEqual(summaries["PROJ-148"].sessions_count, 1)
        self.assertAlmostEqual(summaries["PROJ-148"].total_credits, 2.0)

        # ev-162 must be in PROJ-162
        self.assertIn("PROJ-162", summaries)
        self.assertEqual(summaries["PROJ-162"].sessions_count, 1)
        self.assertAlmostEqual(summaries["PROJ-162"].total_credits, 3.0)

    def test_req15_unknown_token_counts_remain_unknown(self):
        """When tokens are unreported (None), token fields in summary remain None (not 0)."""
        ev = self._create_event("ev-notok", "2026-10-14T10:00:00Z", "sess-tok", "my_repo", credits=1.0)
        # Explicitly no tokens
        ev.payload["input_tokens"] = None
        ev.payload["output_tokens"] = None
        ev.payload["total_tokens"] = None
        self.event_store.save_event(ev)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()
        u = summaries["UNASSIGNED"]
        self.assertFalse(u.has_token_data)
        self.assertIsNone(u.input_tokens)
        self.assertIsNone(u.output_tokens)
        self.assertIsNone(u.total_tokens)

    def test_req16_historical_telemetry_remains_readable(self):
        """Historical events recorded without jira_task_id or attribution files load cleanly."""
        historical_file = self.data_dir / "telemetry-2026-09-01.jsonl"
        raw_event = {
            "event_id": "hist-1",
            "event_type": "ai_run_completed",
            "timestamp": "2026-09-01T12:00:00Z",
            "collector": {"version": "0.1.0", "device_id": "old-device"},
            "identity": {"member_id": "dev1"},
            "context": {"session_id": "hist-sess", "repository": {"name": "legacy-repo"}},
            "payload": {"model": "claude-3-opus", "credits": 5.0},
        }
        historical_file.write_text(json.dumps(raw_event) + "\n", encoding="utf-8")

        # Read back via store
        sessions = self.event_store.get_sessions()
        self.assertTrue(any(s["session_id"] == "hist-sess" for s in sessions))

        # Summaries still compute cleanly without crash
        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()
        self.assertIn("UNASSIGNED", summaries)

    def test_session_spanning_attribution_boundary_exact_split(self):
        """1. A session spanning multiple attribution intervals:
        Event at T1 with 0.04 credits belongs to PROJ-10.
        Event at T2 with 0.06 credits belongs to PROJ-20.
        Total attributed credits = 0.10.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        # PROJ-10 active from 10:00 to 11:00
        # PROJ-20 active from 11:00 onward
        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr1 = self.attr_store.select_ticket("PROJ-10", repo_path=repo_dir)
            attr1.start_time = "2026-10-14T10:00:00Z"
            attr1.end_time = "2026-10-14T11:00:00Z"

            attr2 = self.attr_store.select_ticket("PROJ-20", repo_path=repo_dir)
            attr2.start_time = "2026-10-14T11:00:00Z"
            attr2.end_time = None
            self.attr_store._save()

        # Session 'spanning-sess' has two events
        ev1 = self._create_event("ev-span-1", "2026-10-14T10:30:00Z", "spanning-sess", "my_repo", credits=0.04)
        ev2 = self._create_event("ev-span-2", "2026-10-14T11:30:00Z", "spanning-sess", "my_repo", credits=0.06)
        self.event_store.save_event(ev1)
        self.event_store.save_event(ev2)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("PROJ-10", summaries)
        self.assertIn("PROJ-20", summaries)
        self.assertAlmostEqual(summaries["PROJ-10"].total_credits, 0.04, places=4)
        self.assertAlmostEqual(summaries["PROJ-20"].total_credits, 0.06, places=4)

        # Total attributed credits must equal 0.10 exactly
        tot_creds = round(summaries["PROJ-10"].total_credits + summaries["PROJ-20"].total_credits, 4)
        self.assertEqual(tot_creds, 0.10)

        # Both summaries reflect engagement with spanning-sess
        self.assertIn("spanning-sess", summaries["PROJ-10"].session_ids)
        self.assertIn("spanning-sess", summaries["PROJ-20"].session_ids)
        self.assertEqual(summaries["PROJ-10"].sessions_count, 1)
        self.assertEqual(summaries["PROJ-20"].sessions_count, 1)

    def test_session_spanning_before_ticket_selection_remains_unassigned(self):
        """2. A session spanning a boundary where events before ticket selection
        remain UNASSIGNED and subsequent events are attributed to the active ticket.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-50", repo_path=repo_dir)
            attr.start_time = "2026-10-14T12:00:00Z"
            attr.end_time = None
            self.attr_store._save()

        # Event before selection at 11:45
        ev_pre = self._create_event("ev-pre", "2026-10-14T11:45:00Z", "sess-before-after", "my_repo", credits=1.5)
        # Event after selection at 12:15
        ev_post = self._create_event("ev-post", "2026-10-14T12:15:00Z", "sess-before-after", "my_repo", credits=2.5)
        self.event_store.save_event(ev_pre)
        self.event_store.save_event(ev_post)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("UNASSIGNED", summaries)
        self.assertIn("PROJ-50", summaries)
        self.assertAlmostEqual(summaries["UNASSIGNED"].total_credits, 1.5, places=4)
        self.assertAlmostEqual(summaries["PROJ-50"].total_credits, 2.5, places=4)
        self.assertIn("sess-before-after", summaries["UNASSIGNED"].session_ids)
        self.assertIn("sess-before-after", summaries["PROJ-50"].session_ids)

    def test_exact_switch_timestamp_belongs_to_new_ticket(self):
        """3. Half-open intervals: An event with timestamp exactly at ticket switch
        belongs to the new ticket.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        switch_time = "2026-10-14T12:00:00Z"
        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr1 = self.attr_store.select_ticket("PROJ-101", repo_path=repo_dir)
            attr1.start_time = "2026-10-14T10:00:00Z"
            attr1.end_time = switch_time

            attr2 = self.attr_store.select_ticket("PROJ-102", repo_path=repo_dir)
            attr2.start_time = switch_time
            attr2.end_time = None
            self.attr_store._save()

        # Event exactly at switch_time
        ev_boundary = self._create_event("ev-boundary", switch_time, "sess-boundary", "my_repo", credits=5.0)
        self.event_store.save_event(ev_boundary)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertNotIn("PROJ-101", summaries)
        self.assertIn("PROJ-102", summaries)
        self.assertAlmostEqual(summaries["PROJ-102"].total_credits, 5.0, places=4)

    def test_run_counting_no_double_counting_start_and_completion(self):
        """4. Sessions with both ai_run_started and ai_run_completed:
        Run count is NOT doubled.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-103", repo_path=repo_dir)
            attr.start_time = "2026-10-14T09:00:00Z"
            attr.end_time = None
            self.attr_store._save()

        ev_start = TelemetryEvent(
            event_id="ev-run-start",
            event_type="ai_run_started",
            timestamp="2026-10-14T10:00:00Z",
            collector=CollectorInfo(version="0.1.2", device_id="dev-123"),
            identity=IdentityInfo(member_id="dev1"),
            context=ContextInfo(session_id="sess-run", repository=RepositoryInfo(name="my_repo")),
            payload={"model": "claude-3-sonnet", "run_id": "run-001"},
        )
        ev_comp = TelemetryEvent(
            event_id="ev-run-comp",
            event_type="ai_run_completed",
            timestamp="2026-10-14T10:01:00Z",
            collector=CollectorInfo(version="0.1.2", device_id="dev-123"),
            identity=IdentityInfo(member_id="dev1"),
            context=ContextInfo(session_id="sess-run", repository=RepositoryInfo(name="my_repo")),
            payload={"model": "claude-3-sonnet", "run_id": "run-001", "credits": 0.5},
        )
        self.event_store.save_event(ev_start)
        self.event_store.save_event(ev_comp)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("PROJ-103", summaries)
        s = summaries["PROJ-103"]
        self.assertEqual(s.ai_runs_count, 1)  # Exactly 1 run, not 2
        self.assertAlmostEqual(s.total_credits, 0.5, places=4)

    def test_antigravity_token_events_without_credits(self):
        """5. Antigravity events that record tokens but no credits:
        Credits remain reported as unavailable (has_credit_data=False),
        while token counts are preserved.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-104", repo_path=repo_dir)
            attr.start_time = "2026-10-14T09:00:00Z"
            attr.end_time = None
            self.attr_store._save()

        ev = self._create_event(
            "ev-agy",
            "2026-10-14T10:00:00Z",
            "sess-agy",
            "my_repo",
            credits=None,
            input_tokens=1000,
            output_tokens=250,
        )
        self.event_store.save_event(ev)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertIn("PROJ-104", summaries)
        s = summaries["PROJ-104"]
        self.assertFalse(s.has_credit_data)
        self.assertTrue(s.has_token_data)
        self.assertEqual(s.input_tokens, 1000)
        self.assertEqual(s.output_tokens, 250)
        self.assertEqual(s.total_tokens, 1250)

    def test_multi_session_multi_repository_exact_reconciliation(self):
        """6. Multiple sessions across multiple repositories reconciling exactly
        with source events.
        """
        repo_a = self.data_dir / "repo_a"
        repo_b = self.data_dir / "repo_b"
        repo_a.mkdir(exist_ok=True)
        repo_b.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", side_effect=[
            (str(repo_a), "repo_a", "main"),
            (str(repo_b), "repo_b", "main"),
        ]):
            a1 = self.attr_store.select_ticket("PROJ-105", repo_path=repo_a)
            a1.start_time = "2026-10-14T09:00:00Z"
            a2 = self.attr_store.select_ticket("PROJ-106", repo_path=repo_b)
            a2.start_time = "2026-10-14T09:00:00Z"
            self.attr_store._save()

        # 4 sessions across 2 repos
        evs = [
            self._create_event("ev-a1", "2026-10-14T10:00:00Z", "sess-a1", "repo_a", credits=1.2345),
            self._create_event("ev-a2", "2026-10-14T11:00:00Z", "sess-a2", "repo_a", credits=2.3456),
            self._create_event("ev-b1", "2026-10-14T10:30:00Z", "sess-b1", "repo_b", credits=3.4567),
            self._create_event("ev-b2", "2026-10-14T11:30:00Z", "sess-b2", "repo_b", credits=4.5678),
        ]
        for e in evs:
            self.event_store.save_event(e)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        expected_a = round(1.2345 + 2.3456, 4)
        expected_b = round(3.4567 + 4.5678, 4)
        expected_total = round(expected_a + expected_b, 4)

        self.assertAlmostEqual(summaries["PROJ-105"].total_credits, expected_a, places=4)
        self.assertAlmostEqual(summaries["PROJ-106"].total_credits, expected_b, places=4)

        source_total = round(sum(e.payload["credits"] for e in evs), 4)
        summary_total = round(sum(s.total_credits for s in summaries.values()), 4)
        self.assertEqual(source_total, expected_total)
        self.assertEqual(summary_total, expected_total)

    def test_duplicate_event_delivery_does_not_double_count_or_drop_runs(self):
        """7. Duplicate event delivery handling without discarding legitimate runs."""
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-107", repo_path=repo_dir)
            attr.start_time = "2026-10-14T09:00:00Z"
            self.attr_store._save()

        # Create two distinct events with same run_id (e.g. repeated hook callback)
        ev1 = self._create_event("ev-orig", "2026-10-14T10:00:00Z", "sess-d", "my_repo", credits=1.0)
        ev1.payload["run_id"] = "run-same"
        ev2 = self._create_event("ev-dup-run", "2026-10-14T10:01:00Z", "sess-d", "my_repo", credits=0.0)
        ev2.payload["run_id"] = "run-same"

        # Legitimate second run
        ev3 = self._create_event("ev-distinct-run", "2026-10-14T10:02:00Z", "sess-d", "my_repo", credits=2.0)
        ev3.payload["run_id"] = "run-different"

        self.event_store.save_event(ev1)
        self.event_store.save_event(ev2)
        self.event_store.save_event(ev3)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        s = summaries["PROJ-107"]
        # run-same (ev1 and ev2) is counted once; run-different is counted once -> total 2 runs
        self.assertEqual(s.ai_runs_count, 2)
        self.assertAlmostEqual(s.total_credits, 3.0, places=4)

    def test_missing_and_malformed_timestamps_handled_safely(self):
        """8. Missing timestamps and malformed timestamps:
        Falls back to session start_time; if that is also missing/invalid, attributes to UNASSIGNED.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr = self.attr_store.select_ticket("PROJ-108", repo_path=repo_dir)
            attr.start_time = "2026-10-14T10:00:00Z"
            self.attr_store._save()

        # Session with valid first event at 10:30 (establishes session start_time)
        ev_valid = self._create_event("ev-valid", "2026-10-14T10:30:00Z", "sess-fallback", "my_repo", credits=1.0)
        # Event in same session with malformed timestamp
        ev_malformed = self._create_event("ev-bad-ts", "NOT_A_TIMESTAMP", "sess-fallback", "my_repo", credits=2.0)
        ev_malformed.timestamp = "NOT_A_TIMESTAMP"

        # Session with only invalid timestamps
        ev_all_bad = self._create_event("ev-all-bad", "INVALID", "sess-all-bad", "my_repo", credits=3.0)
        ev_all_bad.timestamp = "INVALID"

        self.event_store.save_event(ev_valid)
        self.event_store.save_event(ev_malformed)
        self.event_store.save_event(ev_all_bad)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        # ev-valid + ev-malformed fall back to sess-fallback started_at (10:30:00Z) -> PROJ-108
        self.assertIn("PROJ-108", summaries)
        self.assertAlmostEqual(summaries["PROJ-108"].total_credits, 3.0, places=4)

        # ev-all-bad has no valid started_at fallback -> UNASSIGNED
        self.assertIn("UNASSIGNED", summaries)
        self.assertAlmostEqual(summaries["UNASSIGNED"].total_credits, 3.0, places=4)

    def test_missing_credits_distinguished_from_measured_zero(self):
        """9. Missing credits vs. measured zero credits:
        None does not add fabricated credit; 0.0 is preserved as measured zero.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            attr1 = self.attr_store.select_ticket("PROJ-109", repo_path=repo_dir)
            attr1.start_time = "2026-10-14T09:00:00Z"
            attr1.end_time = "2026-10-14T10:00:00Z"

            attr2 = self.attr_store.select_ticket("PROJ-110", repo_path=repo_dir)
            attr2.start_time = "2026-10-14T10:00:00Z"
            attr2.end_time = None
            self.attr_store._save()

        # PROJ-109 has measured 0.0 credit event
        ev_zero = self._create_event("ev-zero", "2026-10-14T09:30:00Z", "sess-zero", "my_repo", credits=0.0)
        # PROJ-110 has credits=None event
        ev_none = self._create_event("ev-none", "2026-10-14T10:30:00Z", "sess-none", "my_repo", credits=None)

        self.event_store.save_event(ev_zero)
        self.event_store.save_event(ev_none)

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertTrue(summaries["PROJ-109"].has_credit_data)
        self.assertEqual(summaries["PROJ-109"].total_credits, 0.0)

        self.assertFalse(summaries["PROJ-110"].has_credit_data)
        self.assertEqual(summaries["PROJ-110"].total_credits, 0.0)

    def test_one_thousand_synthetic_events_exact_reconciliation(self):
        """10. 1,000 synthetic events spanning three attribution intervals
        reconcile exactly with the source events.
        """
        repo_dir = self.data_dir / "my_repo"
        repo_dir.mkdir(exist_ok=True)

        # 3 tickets across the day
        # T0: 00:00 to 08:00 -> PROJ-100
        # T1: 08:00 to 16:00 -> PROJ-200
        # T2: 16:00 onward   -> PROJ-300
        with patch.object(self.attr_store, "_resolve_repo", return_value=(str(repo_dir), "my_repo", "main")):
            a1 = self.attr_store.select_ticket("PROJ-100", repo_path=repo_dir)
            a1.start_time = "2026-10-14T00:00:00Z"
            a1.end_time = "2026-10-14T08:00:00Z"

            a2 = self.attr_store.select_ticket("PROJ-200", repo_path=repo_dir)
            a2.start_time = "2026-10-14T08:00:00Z"
            a2.end_time = "2026-10-14T16:00:00Z"

            a3 = self.attr_store.select_ticket("PROJ-300", repo_path=repo_dir)
            a3.start_time = "2026-10-14T16:00:00Z"
            a3.end_time = None
            self.attr_store._save()

        expected_totals = {"PROJ-100": 0.0, "PROJ-200": 0.0, "PROJ-300": 0.0}
        total_source_credits = 0.0

        daily_file = self.data_dir / "telemetry-2026-10-14.jsonl"
        lines = []

        for i in range(1000):
            hour = i % 24
            minute = (i * 7) % 60
            second = (i * 13) % 60
            ts = f"2026-10-14T{hour:02d}:{minute:02d}:{second:02d}Z"
            credit_val = round((i % 10) * 0.1234, 4)
            sid = f"sess-{i % 20}"

            # Determine expected ticket by timestamp
            if hour < 8:
                expected_totals["PROJ-100"] = round(expected_totals["PROJ-100"] + credit_val, 4)
            elif hour < 16:
                expected_totals["PROJ-200"] = round(expected_totals["PROJ-200"] + credit_val, 4)
            else:
                expected_totals["PROJ-300"] = round(expected_totals["PROJ-300"] + credit_val, 4)

            total_source_credits = round(total_source_credits + credit_val, 4)

            raw = {
                "event_id": f"syn-ev-{i}",
                "event_type": "ai_run_completed",
                "timestamp": ts,
                "collector": {"version": "0.1.2", "device_id": "syn-dev"},
                "identity": {"member_id": "tester"},
                "context": {"session_id": sid, "repository": {"name": "my_repo"}},
                "payload": {"model": "claude-3-haiku", "credits": credit_val, "run_id": f"run-{i}"},
            }
            lines.append(json.dumps(raw))

        daily_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        aggregator = TicketAggregator(self.event_store, self.attr_store)
        summaries = aggregator.get_ticket_summaries()

        self.assertAlmostEqual(summaries["PROJ-100"].total_credits, expected_totals["PROJ-100"], places=4)
        self.assertAlmostEqual(summaries["PROJ-200"].total_credits, expected_totals["PROJ-200"], places=4)
        self.assertAlmostEqual(summaries["PROJ-300"].total_credits, expected_totals["PROJ-300"], places=4)

        summary_total = round(sum(s.total_credits for s in summaries.values()), 4)
        self.assertEqual(summary_total, total_source_credits)


class TestJiraApiAdapter(unittest.TestCase):
    """Requirement 11: Jira API optional, read-only, and safe offline degradation."""

    def test_null_adapter_offline_mode(self):
        """NullJiraAdapter behaves safely offline."""
        adapter = NullJiraAdapter()
        self.assertFalse(adapter.is_configured())
        self.assertIsNone(adapter.get_issue("PROJ-148"))

    def test_generic_jira_rest_adapter_unreachable_network_graceful(self):
        """When Jira base URL is configured but unreachable, return None without crashing."""
        adapter = GenericJiraRestAdapter(
            base_url="https://jira.unreachable.invalid",
            api_token="test-token",
            timeout_seconds=0.5,
        )
        self.assertTrue(adapter.is_configured())
        # Should gracefully return None without raising an uncaught network exception
        result = adapter.get_issue("PROJ-148")
        self.assertIsNone(result)

    def test_generic_jira_rest_adapter_successful_resolution(self):
        """Mock successful read-only resolution of title and status."""
        adapter = GenericJiraRestAdapter(base_url="https://jira.company.internal", api_token="secret")
        mock_response_data = {
            "fields": {
                "summary": "Fix authentication session timeout",
                "status": {"name": "In Progress"},
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            info = adapter.get_issue("PROJ-148")
            self.assertIsNotNone(info)
            self.assertEqual(info.key, "PROJ-148")
            self.assertEqual(info.title, "Fix authentication session timeout")
            self.assertEqual(info.status, "In Progress")
            self.assertIn("/browse/PROJ-148", info.url)

    def test_normalize_jira_base_url(self):
        self.assertIsNone(normalize_jira_base_url(None))
        self.assertIsNone(normalize_jira_base_url(""))
        self.assertIsNone(normalize_jira_base_url("   "))
        self.assertIsNone(normalize_jira_base_url("ftp://example.com"))

        # Cloud URL normalization
        self.assertEqual(
            normalize_jira_base_url("https://my-company.atlassian.net/"),
            "https://my-company.atlassian.net",
        )
        self.assertEqual(
            normalize_jira_base_url("my-company.atlassian.net"),
            "https://my-company.atlassian.net",
        )
        # Strips accidental /rest/api/... paths
        self.assertEqual(
            normalize_jira_base_url("https://my-company.atlassian.net/rest/api/2/issue"),
            "https://my-company.atlassian.net",
        )
        # Preserves enterprise server subpath
        self.assertEqual(
            normalize_jira_base_url("http://jira.corp.internal:8080/jira/rest/api/2/"),
            "http://jira.corp.internal:8080/jira",
        )

    def test_jira_username_setting_and_env_loading(self):
        # In Settings directly
        s = Settings(member_id="m", device_id="d", jira_username="cloud-user@example.com")
        self.assertEqual(s.jira_username, "cloud-user@example.com")

        # In load_settings from env
        with patch.dict(os.environ, {"JIRA_USERNAME": "env-user@example.com"}):
            loaded = load_settings()
            self.assertEqual(loaded.jira_username, "env-user@example.com")

        # get_jira_adapter_from_env picks up JIRA_USERNAME
        with patch.dict(
            os.environ,
            {
                "JIRA_BASE_URL": "https://test.atlassian.net",
                "JIRA_API_TOKEN": "secret-token",
                "JIRA_USERNAME": "env-user@example.com",
            },
        ):
            adapter = get_jira_adapter_from_env()
            self.assertIsInstance(adapter, GenericJiraRestAdapter)
            self.assertEqual(adapter.username, "env-user@example.com")
            self.assertEqual(adapter.auth_type, "basic")

    def test_jira_cloud_basic_auth_header_construction(self):
        import base64

        # Jira Cloud automatically uses Basic auth when username is supplied
        adapter = GenericJiraRestAdapter(
            base_url="https://acme.atlassian.net",
            api_token="my-cloud-token",
            username="dev@acme.com",
        )
        self.assertEqual(adapter.auth_type, "basic")
        auth_header = adapter._build_headers().get("Authorization")
        self.assertIsNotNone(auth_header)
        self.assertTrue(auth_header.startswith("Basic "))
        b64_creds = auth_header.split("Basic ", 1)[1]
        decoded = base64.b64decode(b64_creds).decode("utf-8")
        self.assertEqual(decoded, "dev@acme.com:my-cloud-token")

    def test_jira_server_pat_auth_preserved(self):
        # Jira Server PAT defaults to 'pat' auth_type producing Bearer headers
        adapter = GenericJiraRestAdapter(
            base_url="https://jira.server.internal",
            api_token="personal-access-token-123",
        )
        self.assertEqual(adapter.auth_type, "pat")
        self.assertEqual(
            adapter._build_headers().get("Authorization"),
            "Bearer personal-access-token-123",
        )

        # Explicit bearer auth_type
        adapter_bearer = GenericJiraRestAdapter(
            base_url="https://jira.server.internal",
            auth_type="bearer",
            api_token="personal-access-token-123",
        )
        self.assertEqual(adapter_bearer.auth_type, "bearer")
        self.assertEqual(
            adapter_bearer._build_headers().get("Authorization"),
            "Bearer personal-access-token-123",
        )

    def test_test_connection_diagnostics_success(self):
        adapter = GenericJiraRestAdapter(
            base_url="https://acme.atlassian.net",
            api_token="secret",
            username="dev@acme.com",
        )
        mock_user = {
            "displayName": "Ada Lovelace",
            "emailAddress": "ada@acme.com",
            "active": True,
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_user).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "success")
            self.assertEqual(diag["status_code"], 200)
            self.assertEqual(diag["display_name"], "Ada Lovelace")
            self.assertEqual(diag["email"], "ada@acme.com")
            # Verify secret token is NOT leaked in diagnostic output
            self.assertNotIn("secret", str(diag))

    def test_test_connection_diagnostics_errors(self):
        adapter = GenericJiraRestAdapter(
            base_url="https://acme.atlassian.net",
            api_token="my-token",
            username="dev@acme.com",
        )

        # 401 Unauthorized
        err_401 = urllib.error.HTTPError(
            url="https://acme.atlassian.net/rest/api/2/myself",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err_401):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "auth_error")
            self.assertEqual(diag["status_code"], 401)
            self.assertIn("Authentication failed", diag["message"])
            self.assertNotIn("my-token", str(diag))

        # 403 Forbidden
        err_403 = urllib.error.HTTPError(
            url="https://acme.atlassian.net/rest/api/2/myself",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err_403):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "permission_denied")
            self.assertEqual(diag["status_code"], 403)
            self.assertIn("Access forbidden", diag["message"])

        # 404 Not Found
        err_404 = urllib.error.HTTPError(
            url="https://acme.atlassian.net/rest/api/2/myself",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err_404):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "not_found")
            self.assertEqual(diag["status_code"], 404)
            self.assertIn("API endpoint not found", diag["message"])

        # TimeoutError
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "timeout")
            self.assertIn("Connection timed out", diag["message"])

        # Network URLError
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("DNS lookup failed")):
            diag = adapter.test_connection()
            self.assertEqual(diag["status"], "network_error")
            self.assertIn("Network connection failed", diag["message"])

    def test_test_issue_diagnostics(self):
        adapter = GenericJiraRestAdapter(
            base_url="https://acme.atlassian.net",
            api_token="my-token",
            username="dev@acme.com",
        )
        # Success
        mock_issue = {
            "fields": {
                "summary": "Implement Telemetry Ingestion",
                "status": {"name": "In Progress"},
            }
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(mock_issue).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            diag = adapter.test_issue("SCRUM-1")
            self.assertEqual(diag["status"], "success")
            self.assertEqual(diag["key"], "SCRUM-1")
            self.assertEqual(diag["title"], "Implement Telemetry Ingestion")
            self.assertEqual(diag["issue_status"], "In Progress")

        # 404 Not Found
        err_404 = urllib.error.HTTPError(
            url="https://acme.atlassian.net/rest/api/2/issue/SCRUM-999",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=err_404):
            diag = adapter.test_issue("SCRUM-999")
            self.assertEqual(diag["status"], "not_found")
            self.assertEqual(diag["status_code"], 404)
            self.assertIn("SCRUM-999", diag["message"])


class TestJiraCliCommands(unittest.TestCase):
    """End-to-end tests for CLI command handlers."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.settings = Settings(
            member_id="dev-test",
            device_id="dev-id-123",
            telemetry_data_dir=str(self.data_dir),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_jira_select_and_status(self):
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        parser = build_parser()
        args = parser.parse_args(["jira", "select", "PROJ-148", "--repo", str(self.data_dir), "--allow-unverified"])

        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            cmd_jira(args)

        # Check status
        status_args = parser.parse_args(["jira", "status", "--repo", str(self.data_dir), "--json"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            with patch("sys.stdout.write"):
                cmd_jira(status_args)

        # Verify active in store
        attr_store = AttributionStore(self.data_dir)
        active = attr_store.get_active_attribution(self.data_dir)
        self.assertIsNotNone(active)
        self.assertEqual(active.issue_key, "PROJ-148")

    def test_cli_jira_preview_and_confirm(self):
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        parser = build_parser()
        attr_store = AttributionStore(self.data_dir)
        with patch.object(attr_store, "_resolve_repo", return_value=(str(self.data_dir), "test_repo", "main")):
            attr_store.select_ticket("PROJ-148", repo_path=self.data_dir)

        # Preview
        prev_args = parser.parse_args(["jira", "preview", "--repo", str(self.data_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            with patch("ai_telemetry_agent.attribution.detector.detect_repository_context") as mock_ctx:
                mock_ctx.return_value.is_git_repo = True
                mock_ctx.return_value.branch = "feature/PROJ-148-login"
                mock_ctx.return_value.repository_root = str(self.data_dir)
                mock_ctx.return_value.repository_name = "test_repo"
                cmd_jira(prev_args)

        # Clear
        clr_args = parser.parse_args(["jira", "clear", "--repo", str(self.data_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            cmd_jira(clr_args)

        active = attr_store.get_active_attribution(self.data_dir)
        self.assertIsNone(active)

    def test_cli_jira_summary_and_sessions(self):
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        parser = build_parser()
        sum_args = parser.parse_args(["jira", "summary", "--repo", str(self.data_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            cmd_jira(sum_args)

        sess_args = parser.parse_args(["jira", "sessions", "--repo", str(self.data_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            cmd_jira(sess_args)

    def test_cli_jira_test_connection_unconfigured(self):
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        parser = build_parser()
        args = parser.parse_args(["jira", "test-connection"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.settings):
            with self.assertRaises(SystemExit) as cm:
                cmd_jira(args)
            self.assertEqual(cm.exception.code, 1)

    def test_cli_jira_test_connection_configured_and_json(self):
        from ai_telemetry_agent.cli import build_parser, cmd_jira
        from io import StringIO

        parser = build_parser()
        args = parser.parse_args(["jira", "test-connection", "--issue", "SCRUM-1", "--json"])

        configured_settings = Settings(
            member_id="dev-test",
            device_id="dev-id-123",
            telemetry_data_dir=str(self.data_dir),
            jira_base_url="https://acme.atlassian.net",
            jira_api_token="token-123",
            jira_username="dev@acme.com",
        )
        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_connection.return_value = {
            "status": "success",
            "status_code": 200,
            "display_name": "Dev User",
            "email": "dev@acme.com",
            "message": "Connected",
        }
        mock_adapter.test_issue.return_value = {
            "status": "success",
            "status_code": 200,
            "key": "SCRUM-1",
            "title": "Setup Telemetry",
            "issue_status": "Done",
            "url": "https://acme.atlassian.net/browse/SCRUM-1",
        }

        with patch("ai_telemetry_agent.cli.load_settings", return_value=configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    cmd_jira(args)
                    output = mock_stdout.getvalue()
                    data = json.loads(output)
                    self.assertEqual(data["connection"]["status"], "success")
                    self.assertEqual(data["issue_lookup"]["status"], "success")
                    self.assertEqual(data["issue_lookup"]["key"], "SCRUM-1")


class TestJiraValidationAndOfflinePolicy(unittest.TestCase):
    """Requirements 2-8: Jira existence validation, rejection of 404s, error distinction, and offline policy."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.configured_settings = Settings(
            member_id="dev-test",
            device_id="dev-id-123",
            telemetry_data_dir=str(self.data_dir),
            jira_base_url="https://acme.atlassian.net",
            jira_api_token="token-123",
            jira_username="dev@acme.com",
        )
        self.unconfigured_settings = Settings(
            member_id="dev-test",
            device_id="dev-id-123",
            telemetry_data_dir=str(self.data_dir),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_select_valid_ticket_verified_success(self):
        """When Jira is configured and returns 200, ticket is verified and activated."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_issue.return_value = {
            "status": "success",
            "status_code": 200,
            "key": "SCRUM-1",
            "title": "kiro_cli flow testing",
            "issue_status": "In Progress",
            "url": "https://acme.atlassian.net/browse/SCRUM-1",
            "message": "Successfully retrieved issue 'SCRUM-1'.",
        }

        parser = build_parser()
        args = parser.parse_args(["jira", "select", "SCRUM-1", "--repo", str(self.repo_dir)])

        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                cmd_jira(args)

        attr_store = AttributionStore(self.data_dir)
        active = attr_store.get_active_attribution(self.repo_dir)
        self.assertIsNotNone(active)
        self.assertEqual(active.issue_key, "SCRUM-1")
        self.assertEqual(active.confirmation_state, "confirmed")
        self.assertEqual(active.validation_status, "verified")
        self.assertEqual(active.issue_title, "kiro_cli flow testing")
        self.assertEqual(active.issue_status, "In Progress")

    def test_select_nonexistent_ticket_rejected_without_modifying_active_interval(self):
        """A verified HTTP 404 rejects selection without modifying active ticket or its attribution interval."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira
        from io import StringIO

        # 1. Establish an active ticket SCRUM-1
        attr_store = AttributionStore(self.data_dir)
        initial_attr = attr_store.select_ticket("SCRUM-1", repo_path=self.repo_dir, source="manual")
        initial_start_time = initial_attr.start_time
        initial_attr_id = initial_attr.id
        self.assertIsNone(initial_attr.end_time)

        # 2. Attempt to select nonexistent SCRUM-7 (returns HTTP 404)
        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_issue.return_value = {
            "status": "not_found",
            "status_code": 404,
            "key": "SCRUM-7",
            "message": "Issue 'SCRUM-7' not found or inaccessible (HTTP 404).",
        }

        parser = build_parser()
        args = parser.parse_args(["jira", "select", "SCRUM-7", "--repo", str(self.repo_dir)])

        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    with self.assertRaises(SystemExit) as cm:
                        cmd_jira(args)
                    self.assertEqual(cm.exception.code, 1)
                    err_output = mock_stderr.getvalue()
                    self.assertIn("rejected", err_output)
                    self.assertIn("SCRUM-7", err_output)
                    self.assertIn("404", err_output)
                    self.assertIn("Active ticket remains unchanged: SCRUM-1", err_output)

        # 3. Verify active ticket and its attribution interval are 100% UNCHANGED
        active_after = attr_store.get_active_attribution(self.repo_dir)
        self.assertIsNotNone(active_after)
        self.assertEqual(active_after.id, initial_attr_id)
        self.assertEqual(active_after.issue_key, "SCRUM-1")
        self.assertEqual(active_after.start_time, initial_start_time)
        self.assertIsNone(active_after.end_time)

        # 4. Verify SCRUM-7 was NEVER created in attributions
        all_keys = [a.issue_key for a in attr_store._attributions.values()]
        self.assertNotIn("SCRUM-7", all_keys)

    def test_select_auth_error_distinguished_and_requires_offline_policy(self):
        """Authentication failure (401/403) blocks selection unless --allow-unverified is passed."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_issue.return_value = {
            "status": "auth_error",
            "status_code": 401,
            "key": "SCRUM-1",
            "message": "Access denied for issue 'SCRUM-1' (HTTP 401).",
        }

        parser = build_parser()

        # Without --allow-unverified: rejected with exit 1
        args_no_flag = parser.parse_args(["jira", "select", "SCRUM-1", "--repo", str(self.repo_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                with patch("sys.stderr"):
                    with self.assertRaises(SystemExit) as cm:
                        cmd_jira(args_no_flag)
                    self.assertEqual(cm.exception.code, 1)

        # With --allow-unverified: proceeds as unverified
        args_flag = parser.parse_args(["jira", "select", "SCRUM-1", "--repo", str(self.repo_dir), "--allow-unverified"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                cmd_jira(args_flag)

        attr_store = AttributionStore(self.data_dir)
        active = attr_store.get_active_attribution(self.repo_dir)
        self.assertEqual(active.issue_key, "SCRUM-1")
        self.assertEqual(active.validation_status, "unverified")
        self.assertEqual(active.confirmation_state, "confirmed")

    def test_select_network_error_distinguished_and_requires_offline_policy(self):
        """Network failure blocks selection unless --offline / --allow-unverified is passed."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_issue.return_value = {
            "status": "network_error",
            "key": "SCRUM-1",
            "message": "Network connection failed: [Errno 11001] getaddrinfo failed",
        }

        parser = build_parser()

        # Without flag: rejected
        args = parser.parse_args(["jira", "select", "SCRUM-1", "--repo", str(self.repo_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                with patch("sys.stderr"):
                    with self.assertRaises(SystemExit) as cm:
                        cmd_jira(args)
                    self.assertEqual(cm.exception.code, 1)

        # With --offline: succeeds as unverified
        args_offline = parser.parse_args(["jira", "select", "SCRUM-1", "--repo", str(self.repo_dir), "--offline"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                cmd_jira(args_offline)

        attr_store = AttributionStore(self.data_dir)
        active = attr_store.get_active_attribution(self.repo_dir)
        self.assertEqual(active.issue_key, "SCRUM-1")
        self.assertEqual(active.validation_status, "unverified")

    def test_select_offline_unconfigured_requires_allow_unverified(self):
        """When Jira is unconfigured, selection is rejected by default, requiring --allow-unverified or --offline."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        parser = build_parser()

        # Without flag: rejected by default
        args_no_flag = parser.parse_args(["jira", "select", "PROJ-148", "--repo", str(self.repo_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.unconfigured_settings):
            with patch("sys.stderr"):
                with self.assertRaises(SystemExit) as cm:
                    cmd_jira(args_no_flag)
                self.assertEqual(cm.exception.code, 1)

        attr_store = AttributionStore(self.data_dir)
        self.assertIsNone(attr_store.get_active_attribution(self.repo_dir))

        # With --allow-unverified: succeeds under offline policy as unverified
        args_flag = parser.parse_args(["jira", "select", "PROJ-148", "--repo", str(self.repo_dir), "--allow-unverified"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.unconfigured_settings):
            cmd_jira(args_flag)

        attr_store_after = AttributionStore(self.data_dir)
        active = attr_store_after.get_active_attribution(self.repo_dir)
        self.assertIsNotNone(active)
        self.assertEqual(active.issue_key, "PROJ-148")
        self.assertEqual(active.validation_status, "unverified")
        self.assertEqual(active.confirmation_state, "confirmed")

    def test_confirm_offline_unconfigured_requires_allow_unverified(self):
        """When Jira is unconfigured, confirming a proposed branch switch requires --allow-unverified."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        attr_store = AttributionStore(self.data_dir)
        initial_attr = attr_store.select_ticket("SCRUM-1", repo_path=self.repo_dir, source="manual")

        # Propose branch switch to PROJ-200
        with attr_store._lock:
            repo_key, _, _ = attr_store._resolve_repo(self.repo_dir)
            attr_store._contexts[repo_key]["proposed_key"] = "PROJ-200"
            attr_store._save()

        parser = build_parser()

        # Without flag: rejected
        confirm_no_flag = parser.parse_args(["jira", "confirm", "--repo", str(self.repo_dir)])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.unconfigured_settings):
            with patch("sys.stderr"):
                with self.assertRaises(SystemExit) as cm:
                    cmd_jira(confirm_no_flag)
                self.assertEqual(cm.exception.code, 1)

        # Active ticket remains SCRUM-1
        store_check = AttributionStore(self.data_dir)
        active = store_check.get_active_attribution(self.repo_dir)
        self.assertEqual(active.issue_key, "SCRUM-1")
        self.assertEqual(active.id, initial_attr.id)

        # With --offline: succeeds as unverified
        confirm_flag = parser.parse_args(["jira", "confirm", "--repo", str(self.repo_dir), "--offline"])
        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.unconfigured_settings):
            cmd_jira(confirm_flag)

        attr_store_after = AttributionStore(self.data_dir)
        active_after = attr_store_after.get_active_attribution(self.repo_dir)
        self.assertEqual(active_after.issue_key, "PROJ-200")
        self.assertEqual(active_after.validation_status, "unverified")
        self.assertEqual(active_after.confirmation_state, "confirmed")

    def test_confirm_branch_switch_validates_against_jira_rejects_404(self):
        """Confirming a proposed ticket switch also validates against Jira and rejects 404."""
        from ai_telemetry_agent.cli import build_parser, cmd_jira

        attr_store = AttributionStore(self.data_dir)
        initial_attr = attr_store.select_ticket("SCRUM-1", repo_path=self.repo_dir, source="manual")

        # Simulate proposed switch to SCRUM-7
        with attr_store._lock:
            repo_key, _, _ = attr_store._resolve_repo(self.repo_dir)
            attr_store._contexts[repo_key]["proposed_key"] = "SCRUM-7"
            attr_store._save()

        mock_adapter = MagicMock()
        mock_adapter.is_configured.return_value = True
        mock_adapter.test_issue.return_value = {
            "status": "not_found",
            "status_code": 404,
            "key": "SCRUM-7",
            "message": "Issue 'SCRUM-7' not found (HTTP 404).",
        }

        parser = build_parser()
        confirm_args = parser.parse_args(["jira", "confirm", "--repo", str(self.repo_dir)])

        with patch("ai_telemetry_agent.cli.load_settings", return_value=self.configured_settings):
            with patch("ai_telemetry_agent.cli._get_jira_adapter", return_value=mock_adapter):
                with patch("sys.stderr"):
                    with self.assertRaises(SystemExit) as cm:
                        cmd_jira(confirm_args)
                    self.assertEqual(cm.exception.code, 1)

        # Active ticket remains SCRUM-1
        active = attr_store.get_active_attribution(self.repo_dir)
        self.assertEqual(active.issue_key, "SCRUM-1")
        self.assertEqual(active.id, initial_attr.id)
        self.assertIsNone(active.end_time)

    def test_separation_of_user_confirmation_and_jira_validation(self):
        """User confirmation is tracked separately from Jira API existence validation."""
        attr_store = AttributionStore(self.data_dir)
        attr = attr_store.select_ticket("PROJ-999", repo_path=self.repo_dir, source="manual")
        # User confirmed intent, but Jira existence is unverified
        self.assertEqual(attr.confirmation_state, "confirmed")
        self.assertEqual(attr.validation_status, "unverified")


if __name__ == "__main__":
    unittest.main()


