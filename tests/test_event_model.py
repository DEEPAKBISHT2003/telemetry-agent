"""Tests for standardized event model and validation rules."""

import json
import pytest
from src.core.event import (
    AITelemetryPayload,
    CollectorInfo,
    ContextInfo,
    EventType,
    IdentityInfo,
    TelemetryEvent,
)
from src.core.processor import EventProcessor
from src.core.identity import LocalIdentityProvider
from src.config.settings import Settings
from src.core.validation import validate_event, is_valid_iso8601


def test_valid_event_serialization():
    event = TelemetryEvent(
        event_id="evt-123",
        event_type=EventType.COLLECTOR_STARTED,
        timestamp="2026-10-05T16:30:00Z",
        collector=CollectorInfo(version="0.1.0", device_id="DEV-001"),
        identity=IdentityInfo(member_id="M001"),
        context=ContextInfo(session_id="sess-1", project_id="proj-1", jira_task_id=None),
        payload={"status": "RUNNING"},
    )
    is_valid, errors = validate_event(event)
    assert is_valid
    assert len(errors) == 0

    d = event.to_dict()
    assert d["event_id"] == "evt-123"
    assert d["collector"]["device_id"] == "DEV-001"
    assert d["identity"]["member_id"] == "M001"
    assert d["context"]["session_id"] == "sess-1"

    json_str = event.to_json()
    reconstructed = TelemetryEvent.from_dict(json.loads(json_str))
    assert reconstructed.event_id == event.event_id
    assert reconstructed.collector.device_id == event.collector.device_id


def test_invalid_event_missing_fields():
    event = TelemetryEvent(
        event_id="",
        event_type="",
        timestamp="invalid-timestamp",
        collector=CollectorInfo(version="", device_id=""),
        identity=IdentityInfo(member_id=""),
        payload="not-a-dict",  # type: ignore
    )
    is_valid, errors = validate_event(event)
    assert not is_valid
    assert any("event_id" in e for e in errors)
    assert any("event_type" in e for e in errors)
    assert any("timestamp" in e for e in errors)
    assert any("device_id" in e for e in errors)
    assert any("member_id" in e for e in errors)
    assert any("payload" in e for e in errors)


def test_ai_payload_schema_and_null_preservation():
    # Test strict NULL preservation: metrics must not be fabricated
    payload = AITelemetryPayload(
        provider="antigravity",
        model="gemini-3.7-flash",
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )
    d = payload.to_dict()
    assert d["provider"] == "antigravity"
    assert d["model"] == "gemini-3.7-flash"
    assert d["input_tokens"] is None
    assert d["output_tokens"] is None
    assert d["cost_usd"] is None
    assert d["tools"] == []
    assert d["agents"] == []


def test_processor_normalizes_raw_event():
    settings = Settings(member_id="M001", device_id="DEV-001")
    id_provider = LocalIdentityProvider(settings)
    processor = EventProcessor(id_provider, collector_version="0.1.0")

    raw = {
        "event_type": EventType.GIT_COMMIT_DETECTED,
        "payload": {"repository": "repo-a", "commit_sha": "abcdef123456"},
    }
    processed = processor.process_raw(raw)
    assert processed is not None
    assert processed.event_id != ""  # auto-generated UUID
    assert is_valid_iso8601(processed.timestamp)
    assert processed.collector.device_id == "DEV-001"
    assert processed.identity.member_id == "M001"
    assert processed.payload["commit_sha"] == "abcdef123456"


def test_processor_rejects_malformed_event():
    settings = Settings(member_id="M001", device_id="DEV-001")
    id_provider = LocalIdentityProvider(settings)
    processor = EventProcessor(id_provider)

    # Missing event_type
    assert processor.process_raw({}) is None
    assert processor.process_raw("not a dict") is None  # type: ignore
