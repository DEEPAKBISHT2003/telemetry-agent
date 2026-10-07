"""Minimal stdio Model Context Protocol (MCP) probe server.

Evaluates whether MCP can observe Antigravity AI execution telemetry events.
"""

import datetime
import json
import os
import sys
from typing import Any, Dict, Optional

# Structured log for probe observation
def log_probe(event_type: str, **fields: Any) -> None:
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    field_str = " ".join(f"{k}={v}" for k, v in fields.items())
    sys.stderr.write(f"[MCP_PROBE] {now_str} {event_type} {field_str}\n")
    sys.stderr.flush()


def handle_initialize(request_id: Any) -> Dict[str, Any]:
    log_probe("mcp_initialized", status="READY")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "ai-telemetry-mcp-probe",
                "version": "0.1.1"
            }
        }
    }


def handle_tools_list(request_id: Any) -> Dict[str, Any]:
    log_probe("mcp_tools_listed")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "telemetry_health_check",
                    "description": "Harmless diagnostic tool to verify telemetry collector connectivity.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "echo_message": {
                                "type": "string",
                                "description": "Optional message to echo back."
                            }
                        }
                    }
                }
            ]
        }
    }


def handle_tools_call(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    echo_msg = arguments.get("echo_message", "telemetry_ok")

    log_probe(
        "mcp_tool_called",
        tool_name=tool_name,
        can_observe_tokens=False,
        can_observe_cost=False,
        can_observe_prompts=False,
    )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "status": "HEALTHY",
                        "echo": echo_msg,
                        "observation": "MCP tool invoked directly; no LLM token metrics or external prompt context are accessible."
                    })
                }
            ]
        }
    }


def run_stdio_server() -> None:
    """Run MCP server over standard input / standard output."""
    log_probe("server_starting", pid=os.getpid())
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            request = json.loads(line)
            req_id = request.get("id")
            method = request.get("method")

            response = None
            if method == "initialize":
                response = handle_initialize(req_id)
            elif method == "tools/list":
                response = handle_tools_list(req_id)
            elif method == "tools/call":
                response = handle_tools_call(req_id, request.get("params", {}))
            elif method == "notifications/initialized":
                # Notifications do not return a response
                continue
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method {method} not supported"
                    }
                }

            if response:
                out_str = json.dumps(response, ensure_ascii=False)
                sys.stdout.write(out_str + "\n")
                sys.stdout.flush()

        except Exception as e:
            log_probe("server_error", error=str(e))


if __name__ == "__main__":
    run_stdio_server()
