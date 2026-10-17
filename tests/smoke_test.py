"""Comprehensive Production Smoke Test for ai-telemetry-agent.
Tests 7 operational scenarios in isolated temporary environments:
1. Antigravity-only mode.
2. Kiro CLI-only mode.
3. Kiro IDE-only mode.
4. Auto mode with both Kiro sources.
5. Auto mode before and after Kiro directories appear dynamically.
6. Persistence failure followed by successful retry (no duplicate credits, no lost turns).
7. Repeated Stop and SessionEnd notifications (deduplication check).
"""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock

from ai_telemetry_agent.config.settings import load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.integrations.hooks.kiro_hook_handler import process_kiro_hook
from ai_telemetry_agent.sources.antigravity import AntigravitySource
from ai_telemetry_agent.storage.base import StoragePersistenceError


def _make_cli_session(session_id: str, turn_id: str, credits: float = 0.02) -> dict:
    return {
        "session_id": session_id,
        "updated_at": "2026-10-14T07:30:00Z",
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


def _make_ide_session(session_id: str) -> dict:
    return {
        "id": session_id,
        "title": "Smoke IDE Session",
        "modelId": "claude-sonnet-4.5",
        "createdAt": "2026-10-14T07:00:00Z",
        "lastModifiedAt": "2026-10-14T07:05:00Z",
        "workspacePaths": [],
    }


def run_smoke_tests():
    print("=== STARTING PRODUCTION SMOKE TESTS ===")
    results = {}

    with tempfile.TemporaryDirectory() as tmp_root:
        root = Path(tmp_root)

        # -------------------------------------------------------------
        # 1. Antigravity-only mode
        # -------------------------------------------------------------
        print("\n[Scenario 1] Antigravity-only mode")
        env_1 = root / ".env_1"
        data_1 = root / "data_1"
        env_1.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_1}\nTELEMETRY_SOURCE=antigravity\n",
            encoding="utf-8",
        )
        s1 = load_settings(env_file=str(env_1))
        c1 = TelemetryCollector(s1)
        c1.start()
        ag_sources = [s for s in c1.sources if isinstance(s, AntigravitySource)]
        kiro_sources = [s for s in c1.sources if "kiro" in s.name]
        c1.stop()
        assert len(ag_sources) == 1, f"Expected 1 Antigravity source, got {len(ag_sources)}"
        assert len(kiro_sources) == 0, f"Expected 0 Kiro sources in antigravity mode, got {len(kiro_sources)}"
        results["1. Antigravity-only mode"] = "PASS"
        print(" -> PASSED: Antigravity initialized without Kiro sources")

        # -------------------------------------------------------------
        # 2. Kiro CLI-only mode
        # -------------------------------------------------------------
        print("\n[Scenario 2] Kiro CLI-only mode")
        env_2 = root / ".env_2"
        data_2 = root / "data_2"
        sess_2 = root / "sess_2"
        cli_2 = sess_2 / "cli"
        cli_2.mkdir(parents=True)
        env_2.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_2}\nTELEMETRY_SOURCE=kiro_cli\nKIRO_SESSIONS_DIR={sess_2}\n",
            encoding="utf-8",
        )
        s2 = load_settings(env_file=str(env_2))
        c2 = TelemetryCollector(s2)
        c2.start()
        (cli_2 / "sess_smoke_cli.json").write_text(json.dumps(_make_cli_session("cli_s2", "t2", credits=0.03)), encoding="utf-8")
        count_2 = c2.poll_and_process()
        c2.stop()
        events_2 = c2.store.get_events()
        cli_evs_2 = [e for e in events_2 if e.payload.get("provider") == "kiro_cli"]
        assert len(cli_evs_2) == 1, f"Expected 1 CLI event, got {len(cli_evs_2)}"
        assert cli_evs_2[0].payload.get("credits") == 0.03, "Credits mismatch"
        assert cli_evs_2[0].payload.get("cost_usd") is None, "cost_usd should be None"
        results["2. Kiro CLI-only mode"] = "PASS"
        print(" -> PASSED: CLI session ingested with distinct credits (cost_usd=None)")

        # -------------------------------------------------------------
        # 3. Kiro IDE-only mode
        # -------------------------------------------------------------
        print("\n[Scenario 3] Kiro IDE-only mode")
        env_3 = root / ".env_3"
        data_3 = root / "data_3"
        sess_3 = root / "sess_3"
        ws_3 = sess_3 / "hash_smoke" / "sess_smoke_ide"
        ws_3.mkdir(parents=True)
        env_3.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_3}\nTELEMETRY_SOURCE=kiro_ide\nKIRO_SESSIONS_DIR={sess_3}\n",
            encoding="utf-8",
        )
        s3 = load_settings(env_file=str(env_3))
        c3 = TelemetryCollector(s3)
        c3.start()
        (ws_3 / "session.json").write_text(json.dumps(_make_ide_session("ide_s3")), encoding="utf-8")
        count_3 = c3.poll_and_process()
        c3.stop()
        events_3 = c3.store.get_events()
        ide_evs_3 = [e for e in events_3 if e.payload.get("provider") == "kiro_ide"]
        assert len(ide_evs_3) >= 1, f"Expected at least 1 IDE event, got {len(ide_evs_3)}"
        results["3. Kiro IDE-only mode"] = "PASS"
        print(" -> PASSED: IDE session ingested cleanly")

        # -------------------------------------------------------------
        # 4. Auto mode with both Kiro sources
        # -------------------------------------------------------------
        print("\n[Scenario 4] Auto mode with both Kiro sources")
        env_4 = root / ".env_4"
        data_4 = root / "data_4"
        sess_4 = root / "sess_4"
        cli_4 = sess_4 / "cli"
        cli_4.mkdir(parents=True)
        ws_4 = sess_4 / "hash_both" / "ide_both"
        ws_4.mkdir(parents=True)
        env_4.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_4}\nTELEMETRY_SOURCE=auto\nKIRO_SESSIONS_DIR={sess_4}\n",
            encoding="utf-8",
        )
        s4 = load_settings(env_file=str(env_4))
        c4 = TelemetryCollector(s4)
        c4.start()
        (cli_4 / "sess_cli.json").write_text(json.dumps(_make_cli_session("cli_s4", "t4")), encoding="utf-8")
        (ws_4 / "session.json").write_text(json.dumps(_make_ide_session("ide_s4")), encoding="utf-8")
        c4.poll_and_process()
        c4.stop()
        events_4 = c4.store.get_events()
        cli_evs_4 = [e for e in events_4 if e.payload.get("provider") == "kiro_cli"]
        ide_evs_4 = [e for e in events_4 if e.payload.get("provider") == "kiro_ide"]
        assert len(cli_evs_4) >= 1, "Missing CLI event in auto mode"
        assert len(ide_evs_4) >= 1, "Missing IDE event in auto mode"
        results["4. Auto mode with both Kiro sources"] = "PASS"
        print(" -> PASSED: Concurrent collection from both CLI and IDE sources in auto mode")

        # -------------------------------------------------------------
        # 5. Auto mode before and after Kiro directories appear
        # -------------------------------------------------------------
        print("\n[Scenario 5] Auto mode before and after Kiro directories appear")
        env_5 = root / ".env_5"
        data_5 = root / "data_5"
        sess_5 = root / "sess_5"
        env_5.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_5}\nTELEMETRY_SOURCE=auto\nKIRO_SESSIONS_DIR={sess_5}\n",
            encoding="utf-8",
        )
        s5 = load_settings(env_file=str(env_5))
        c5 = TelemetryCollector(s5)
        # At start, sess_5 does not exist -> Antigravity fallback source is included
        assert any(isinstance(s, AntigravitySource) for s in c5.sources), "Antigravity should be fallback"
        c5.start()
        # Poll before Kiro appears
        c5.poll_and_process()

        # Now Kiro CLI directory appears dynamically
        cli_5 = sess_5 / "cli"
        cli_5.mkdir(parents=True)
        (cli_5 / "sess_dyn.json").write_text(json.dumps(_make_cli_session("cli_dyn", "t_dyn")), encoding="utf-8")

        # Poll after Kiro appears
        c5.poll_and_process()
        c5.stop()
        events_5 = c5.store.get_events()
        dyn_evs = [e for e in events_5 if e.context.session_id == "cli_dyn"]
        assert len(dyn_evs) == 1, "Dynamic CLI session should be discovered and ingested"
        results["5. Auto mode dynamic directory appearance"] = "PASS"
        print(" -> PASSED: Fallback initially active; dynamic discovery of Kiro CLI when created")

        # -------------------------------------------------------------
        # 6. Persistence failure followed by retry (staged-state rollback)
        # -------------------------------------------------------------
        print("\n[Scenario 6] Persistence failure followed by retry (rollback)")
        env_6 = root / ".env_6"
        data_6 = root / "data_6"
        sess_6 = root / "sess_6"
        cli_6 = sess_6 / "cli"
        cli_6.mkdir(parents=True)
        env_6.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_6}\nTELEMETRY_SOURCE=kiro_cli\nKIRO_SESSIONS_DIR={sess_6}\n",
            encoding="utf-8",
        )
        s6 = load_settings(env_file=str(env_6))
        c6 = TelemetryCollector(s6)
        c6.start()
        (cli_6 / "sess_fail.json").write_text(json.dumps(_make_cli_session("cli_f6", "t_f6", credits=0.08)), encoding="utf-8")

        # Mock persistence error
        orig_save = c6.store.save_event
        c6.store.save_event = MagicMock(side_effect=StoragePersistenceError("Simulated disk error"))
        cnt_fail = c6.poll_and_process()
        assert cnt_fail == 0, f"Expected 0 successes on failure, got {cnt_fail}"
        assert c6.kiro_cli_source._staged_state == c6.kiro_cli_source._state, "Staged state not rolled back"
        assert "cli_f6" not in c6.kiro_cli_source._state, "Failed turn committed prematurely"

        # Restore persistence and retry
        c6.store.save_event = orig_save
        cnt_retry = c6.poll_and_process()
        assert cnt_retry >= 1, f"Expected successful retry, got {cnt_retry}"

        # Subsequent poll produces 0 duplicates
        cnt_dup = c6.poll_and_process()
        assert cnt_dup == 0, f"Expected 0 duplicates, got {cnt_dup}"
        c6.stop()

        events_6 = [e for e in c6.store.get_events() if e.context.session_id == "cli_f6"]
        assert len(events_6) == 1, f"Expected exactly 1 event on retry, got {len(events_6)}"
        assert events_6[0].payload.get("credits") == 0.08, "Credits mismatch on retry"
        results["6. Rollback and retry resilience"] = "PASS"
        print(" -> PASSED: State rolled back on failure, successfully retried, zero duplicate credits")

        # -------------------------------------------------------------
        # 7. Repeated Stop and SessionEnd notifications (deduplication)
        # -------------------------------------------------------------
        print("\n[Scenario 7] Repeated Stop and SessionEnd notifications")
        env_7 = root / ".env_7"
        data_7 = root / "data_7"
        env_7.write_text(
            f"MEMBER_ID=TEST_SMOKE\nDEVICE_ID=DEV_SMOKE\nTELEMETRY_DATA_DIR={data_7}\nTELEMETRY_SOURCE=kiro_ide\n",
            encoding="utf-8",
        )
        s7 = load_settings(env_file=str(env_7))
        c7 = TelemetryCollector(s7)

        saved_stdin, saved_stdout = sys.stdin, sys.stdout
        try:
            # 1. Fire Stop
            sys.stdin = io.StringIO(json.dumps({"session_id": "sess_hook_dedup", "cwd": str(root)}))
            sys.stdout = io.StringIO()
            try:
                process_kiro_hook("Stop", collector=c7)
            except SystemExit:
                pass

            # 2. Fire SessionEnd for same session
            sys.stdin = io.StringIO(json.dumps({"session_id": "sess_hook_dedup", "cwd": str(root)}))
            sys.stdout = io.StringIO()
            try:
                process_kiro_hook("SessionEnd", collector=c7)
            except SystemExit:
                pass

            # 3. Fire lower-case session_end for same session
            sys.stdin = io.StringIO(json.dumps({"session_id": "sess_hook_dedup", "cwd": str(root)}))
            sys.stdout = io.StringIO()
            try:
                process_kiro_hook("session_end", collector=c7)
            except SystemExit:
                pass
        finally:
            sys.stdin, sys.stdout = saved_stdin, saved_stdout

        hook_evs = [e for e in c7.store.get_events() if e.context.session_id == "sess_hook_dedup"]
        assert len(hook_evs) == 1, f"Expected exactly 1 deduplicated event across Stop/SessionEnd, got {len(hook_evs)}"
        assert hook_evs[0].event_type == "ai_run_completed", f"Expected ai_run_completed, got {hook_evs[0].event_type}"
        results["7. Hook SessionEnd & Stop deduplication"] = "PASS"
        print(" -> PASSED: Exactly 1 ai_run_completed recorded across Stop, SessionEnd, and session_end")

    print("\n=== SMOKE TEST SUMMARY ===")
    for scenario, res in results.items():
        print(f"[{res}] {scenario}")

    assert all(r == "PASS" for r in results.values()), "One or more smoke scenarios failed"
    print("\nALL 7 PRODUCTION SMOKE SCENARIOS PASSED!")


if __name__ == "__main__":
    run_smoke_tests()
