"""Tests for AI provider abstractions, null token/cost preservation, and event builders."""

import pytest
from src.core.event import EventType
from src.sources.ai_provider import (
    AIEventBuilder,
    HookProvider,
    MCPProvider,
    OfficialExtensionProvider,
    ProxyProvider,
    UnsupportedProvider,
)


def test_ai_event_builder_null_preservation():
    event = AIEventBuilder.build_run_completed(
        provider="antigravity",
        model="gemini-3.7-flash",
        session_id="conv-100",
        duration_ms=450.5,
        # Intentionally leaving token and cost metrics unprovided
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )

    assert event["event_type"] == EventType.AI_RUN_COMPLETED
    payload = event["payload"]
    assert payload["provider"] == "antigravity"
    assert payload["model"] == "gemini-3.7-flash"
    assert payload["duration_ms"] == 450.5
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None
    assert payload["cost_usd"] is None
    assert payload["total_tokens"] is None


def test_hook_provider_captures_lifecycle_events():
    provider = HookProvider()
    assert provider.is_available is True

    # Record PreInvocation
    provider.record_hook_event("PreInvocation", conversation_id="conv-1", model_name="gemini-3.7-flash", step_idx=1)
    # Record Tool Use
    provider.record_hook_event("PreToolUse", conversation_id="conv-1", tool_name="run_command", step_idx=2)
    # Record Stop
    provider.record_hook_event("Stop", conversation_id="conv-1", model_name="gemini-3.7-flash")

    events = provider.collect_events()
    assert len(events) == 3
    assert events[0]["event_type"] == EventType.AI_RUN_STARTED
    assert events[1]["event_type"] == EventType.AI_TOOL_CALL
    assert events[2]["event_type"] == EventType.AI_RUN_COMPLETED

    # Verify buffer cleared after collection
    assert len(provider.collect_events()) == 0


def test_mcp_provider_records_tool_invocation():
    mcp = MCPProvider()
    assert mcp.is_available is True

    mcp.record_mcp_tool_invocation("telemetry_health_check", session_id="conv-mcp-1")
    events = mcp.collect_events()
    assert len(events) == 1
    assert events[0]["event_type"] == EventType.AI_TOOL_CALL
    assert events[0]["payload"]["tool_name"] == "telemetry_health_check"
    assert events[0]["payload"]["capture_method"] == "mcp"


def test_future_provider_states():
    ext = OfficialExtensionProvider()
    assert ext.is_available is False
    assert ext.collect_events() == []

    proxy = ProxyProvider()
    assert proxy.is_available is False

    unsupported = UnsupportedProvider()
    assert unsupported.is_available is False
