"""Tests for SQLite event storage, indexing, retrieval, and duplicates."""

import os
from pathlib import Path
import pytest
from src.core.event import CollectorInfo, ContextInfo, EventType, IdentityInfo, TelemetryEvent
from src.storage.sqlite_store import SQLiteEventStore


@pytest.fixture
def temp_store(tmp_path):
    db_path = tmp_path / "test_telemetry.db"
    store = SQLiteEventStore(str(db_path))
    yield store
    store.close()


def make_event(event_id: str, event_type: str, timestamp: str, member_id: str = "M001") -> TelemetryEvent:
    return TelemetryEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=timestamp,
        collector=CollectorInfo(version="0.1.0", device_id="DEV-001"),
        identity=IdentityInfo(member_id=member_id),
        context=ContextInfo(session_id="sess-1"),
        payload={"sample_key": "sample_val"},
    )


def test_insert_and_retrieve_event(temp_store):
    ev = make_event("e-1", EventType.COLLECTOR_STARTED, "2026-10-05T12:00:00Z")
    saved = temp_store.save_event(ev)
    assert saved is True

    retrieved = temp_store.get_event_by_id("e-1")
    assert retrieved is not None
    assert retrieved.event_id == "e-1"
    assert retrieved.event_type == EventType.COLLECTOR_STARTED
    assert retrieved.identity.member_id == "M001"
    assert retrieved.payload["sample_key"] == "sample_val"


def test_duplicate_event_handling(temp_store):
    ev = make_event("e-dup", EventType.COLLECTOR_HEARTBEAT, "2026-10-05T12:00:00Z")
    assert temp_store.save_event(ev) is True
    # Duplicate ID should be rejected safely
    assert temp_store.save_event(ev) is False
    assert temp_store.get_count() == 1


def test_event_filtering_and_ordering(temp_store):
    temp_store.save_event(make_event("e-1", EventType.COLLECTOR_STARTED, "2026-10-05T10:00:00Z", member_id="M001"))
    temp_store.save_event(make_event("e-2", EventType.GIT_COMMIT_DETECTED, "2026-10-05T11:00:00Z", member_id="M001"))
    temp_store.save_event(make_event("e-3", EventType.GIT_COMMIT_DETECTED, "2026-10-05T12:00:00Z", member_id="M002"))

    assert temp_store.get_count() == 3
    assert temp_store.get_count(event_type=EventType.GIT_COMMIT_DETECTED) == 2

    # Query all descending
    events_desc = temp_store.get_events(limit=10, descending=True)
    assert len(events_desc) == 3
    assert events_desc[0].event_id == "e-3"

    # Query filtered by member_id
    m002_events = temp_store.get_events(member_id="M002")
    assert len(m002_events) == 1
    assert m002_events[0].event_id == "e-3"
