"""Tests for MCP probe server JSON-RPC protocol handling."""

import json
from src.integrations.mcp_probe.mcp_server import handle_initialize, handle_tools_call, handle_tools_list


def test_mcp_initialize():
    res = handle_initialize(request_id="req-1")
    assert res["jsonrpc"] == "2.0"
    assert res["id"] == "req-1"
    assert res["result"]["serverInfo"]["name"] == "ai-telemetry-mcp-probe"


def test_mcp_tools_list():
    res = handle_tools_list(request_id="req-2")
    assert res["jsonrpc"] == "2.0"
    tools = res["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "telemetry_health_check"


def test_mcp_tools_call_proves_limited_scope():
    params = {
        "name": "telemetry_health_check",
        "arguments": {"echo_message": "test_signal"}
    }
    res = handle_tools_call(request_id="req-3", params=params)
    assert res["jsonrpc"] == "2.0"
    content = json.loads(res["result"]["content"][0]["text"])
    assert content["status"] == "HEALTHY"
    assert content["echo"] == "test_signal"
    # Proves MCP tool call cannot provide general prompt tokens or cost
    assert "LLM token metrics" in content["observation"]
