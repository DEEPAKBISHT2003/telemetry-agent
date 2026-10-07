"""Tests for local event queue operations, buffering, and retries."""

import pytest
from src.core.event import CollectorInfo, ContextInfo, EventType, IdentityInfo, TelemetryEvent
from src.storage.queue import SQLiteEventQueue


@pytest.fixture
def temp_queue(tmp_path):
    db_path = tmp_path / "test_queue.db"
    return SQLiteEventQueue(str(db_path), max_retries=3)


def make_event(event_id: str) -> TelemetryEvent:
    return TelemetryEvent(
        event_id=event_id,
        event_type=EventType.COLLECTOR_HEARTBEAT,
        timestamp="2026-10-05T12:00:00Z",
        collector=CollectorInfo(version="0.1.0", device_id="DEV-001"),
        identity=IdentityInfo(member_id="M001"),
        context=ContextInfo(),
        payload={"uptime": 30},
    )


def test_queue_enqueue_and_dequeue(temp_queue):
    ev1 = make_event("q-1")
    ev2 = make_event("q-2")

    qid1 = temp_queue.enqueue(ev1)
    qid2 = temp_queue.enqueue(ev2)
    assert qid1 > 0
    assert qid2 > qid1
    assert temp_queue.size(SQLiteEventQueue.STATUS_PENDING) == 2

    # Dequeue batch of 1
    items = temp_queue.dequeue(batch_size=1)
    assert len(items) == 1
    assert items[0].event.event_id == "q-1"
    assert items[0].status == SQLiteEventQueue.STATUS_PROCESSING
    assert temp_queue.size(SQLiteEventQueue.STATUS_PENDING) == 1

    # Mark processed
    marked = temp_queue.mark_processed(items[0].queue_id)
    assert marked is True
    assert temp_queue.size(SQLiteEventQueue.STATUS_PROCESSED) == 1


def test_queue_retry_and_max_failure(temp_queue):
    ev = make_event("q-fail")
    qid = temp_queue.enqueue(ev)

    # Dequeue
    items = temp_queue.dequeue(batch_size=1)
    assert len(items) == 1

    # Retry 1
    temp_queue.retry(qid, "Network timeout")
    assert temp_queue.size(SQLiteEventQueue.STATUS_PENDING) == 1

    # Dequeue and Retry 2
    temp_queue.dequeue(batch_size=1)
    temp_queue.retry(qid, "Network timeout 2")

    # Dequeue and Retry 3 (exceeds max_retries=3)
    temp_queue.dequeue(batch_size=1)
    temp_queue.retry(qid, "Final failure")

    assert temp_queue.size(SQLiteEventQueue.STATUS_FAILED) == 1
    assert temp_queue.size(SQLiteEventQueue.STATUS_PENDING) == 0
