"""Tests for Antigravity Lifecycle Hook Handler."""

import io
import json
import sys
import pytest
from src.config.settings import Settings
from src.core.event import EventType
from src.integrations.hooks.hook_handler import process_hook
from src.storage.sqlite_store import SQLiteEventStore


def test_hook_handler_pre_invocation(tmp_path, monkeypatch):
    db_path = tmp_path / "hooks_test.db"
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MEMBER_ID", "M001")
    monkeypatch.setenv("DEVICE_ID", "DEV-001")

    hook_input = {
        "conversationId": "conv-test-123",
        "modelName": "gemini-3.7-flash",
        "stepIdx": 1,
    }

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    stdout_capture = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout_capture)

    process_hook("PreInvocation")

    # Verify event stored
    store = SQLiteEventStore(str(db_path))
    events = store.get_events(limit=5)
    assert len(events) == 1
    assert events[0].event_type == EventType.AI_RUN_STARTED
    assert events[0].context.session_id == "conv-test-123"
    assert events[0].payload["model"] == "gemini-3.7-flash"
    assert events[0].payload["capture_method"] == "hooks"


def test_hook_handler_pre_tool_use(tmp_path, monkeypatch):
    db_path = tmp_path / "hooks_tool_test.db"
    monkeypatch.setenv("LOCAL_DATABASE_PATH", str(db_path))
    monkeypatch.setenv("MEMBER_ID", "M001")
    monkeypatch.setenv("DEVICE_ID", "DEV-001")

    hook_input = {
        "conversationId": "conv-test-123",
        "stepIdx": 4,
        "toolCall": {
            "name": "run_command",
            "args": {"CommandLine": "pytest"}
        }
    }

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    stdout_capture = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout_capture)

    process_hook("PreToolUse")

    # Verify stdout decision response required by Antigravity
    stdout_val = stdout_capture.getvalue()
    out_obj = json.loads(stdout_val)
    assert out_obj["decision"] == "allow"

    # Verify stored tool event
    store = SQLiteEventStore(str(db_path))
    events = store.get_events(limit=5)
    assert len(events) == 1
    assert events[0].event_type == EventType.AI_TOOL_CALL
    assert events[0].payload["tool_name"] == "run_command"
