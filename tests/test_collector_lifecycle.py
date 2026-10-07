"""Tests for collector lifecycle: startup, shutdown, heartbeat, error handling."""

import time
import pytest
from src.config.settings import Settings
from src.core.collector import TelemetryCollector
from src.core.event import EventType


def test_collector_lifecycle_events(tmp_path):
    db_file = tmp_path / "collector_test.db"
    settings = Settings(
        member_id="M001",
        device_id="DEV-001",
        local_database_path=str(db_file),
        heartbeat_interval_seconds=1,
        git_monitor_paths=[],  # skip git for pure lifecycle test
    )

    collector = TelemetryCollector(settings)
    collector.start()

    # Verify startup and developer detection events in store
    events = collector.store.get_events(limit=10, descending=False)
    types = [e.event_type for e in events]
    assert EventType.COLLECTOR_STARTED in types
    assert EventType.DEVELOPER_DETECTED in types

    # Wait for heartbeat interval
    time.sleep(1.2)
    collector.poll_and_process()

    events_after_hb = collector.store.get_events(limit=10, descending=False)
    types_after_hb = [e.event_type for e in events_after_hb]
    assert EventType.COLLECTOR_HEARTBEAT in types_after_hb

    # Stop collector
    collector.stop()

    events_after_stop = collector.store.get_events(limit=10, descending=False)
    types_after_stop = [e.event_type for e in events_after_stop]
    assert EventType.COLLECTOR_STOPPED in types_after_stop


def test_collector_error_event(tmp_path):
    db_file = tmp_path / "collector_error_test.db"
    settings = Settings(
        member_id="M001",
        device_id="DEV-001",
        local_database_path=str(db_file),
    )
    collector = TelemetryCollector(settings)
    collector.start()

    collector.system_source.emit_error("TEST_ERROR", "Test error message")
    collector.poll_and_process()

    error_events = collector.store.get_events(event_type=EventType.COLLECTOR_ERROR)
    assert len(error_events) == 1
    assert error_events[0].payload["error_type"] == "TEST_ERROR"
    assert error_events[0].payload["message"] == "Test error message"

    collector.stop()
