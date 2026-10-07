"""End-to-End Test for Telemetry Collector V0 workflow."""

import os
from pathlib import Path
import subprocess
import time
import pytest

from src.config.settings import Settings
from src.core.collector import TelemetryCollector
from src.core.event import AITelemetryPayload, EventType
from src.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS


def test_full_collector_e2e_flow(tmp_path):
    # 1. Setup isolated test environment
    db_file = tmp_path / "e2e_telemetry.db"
    repo_dir = tmp_path / "e2e_repo"
    repo_dir.mkdir()

    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev Alice"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "alice@org.internal"], cwd=str(repo_dir), check=True, capture_output=True)

    test_file = repo_dir / "main.py"
    test_file.write_text("# initial commit", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=str(repo_dir), check=True, capture_output=True)

    # 2. Configure Collector
    settings = Settings(
        member_id="M001",
        device_id="DEV-001",
        local_database_path=str(db_file),
        heartbeat_interval_seconds=1,
        git_monitor_paths=[str(repo_dir)],
        git_monitor_interval_seconds=0,
    )

    collector = TelemetryCollector(settings)

    # 3. Start Collector
    collector.start()

    # Verify Antigravity adapter status
    assert collector.antigravity_source.status == ANTIGRAVITY_AI_TELEMETRY_STATUS

    # 4. Perform Git operations: Branch switch
    subprocess.run(["git", "checkout", "-b", "feature/v0-telemetry"], cwd=str(repo_dir), check=True, capture_output=True)
    collector.poll_and_process()

    # 5. Perform Git operations: New commit
    test_file.write_text("# updated commit with telemetry", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: implement telemetry collector v0"], cwd=str(repo_dir), check=True, capture_output=True)
    collector.poll_and_process()

    # 6. Heartbeat
    time.sleep(1.2)
    collector.poll_and_process()

    # 7. AI Adapter test (demonstrating safe adapter capability with strictly non-fabricated nulls)
    ai_payload = AITelemetryPayload(
        provider="antigravity",
        model="gemini-3.7-flash",
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )
    collector.antigravity_source.emit_ai_event(
        event_type=EventType.AI_RUN,
        payload=ai_payload,
        context={"project_id": "ai-telemetry"},
    )
    collector.poll_and_process()

    # 8. Stop Collector
    collector.stop()

    # 9. Verify Database Records and Sequence
    events = collector.store.get_events(limit=50, descending=False)
    event_types = [e.event_type for e in events]

    assert EventType.COLLECTOR_STARTED in event_types
    assert EventType.DEVELOPER_DETECTED in event_types
    assert EventType.GIT_REPOSITORY_DETECTED in event_types
    assert EventType.GIT_BRANCH_CHANGED in event_types
    assert EventType.GIT_COMMIT_DETECTED in event_types
    assert EventType.COLLECTOR_HEARTBEAT in event_types
    assert EventType.AI_RUN in event_types
    assert EventType.COLLECTOR_STOPPED in event_types

    # Validate identity on all stored events
    for ev in events:
        assert ev.identity.member_id == "M001"
        assert ev.collector.device_id == "DEV-001"
        assert ev.collector.version == "0.1.0"
        assert ev.event_id is not None and len(ev.event_id) > 0
