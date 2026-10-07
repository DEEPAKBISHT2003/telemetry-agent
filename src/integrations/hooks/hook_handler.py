"""Lifecycle hook handler invoked by Antigravity (.agents/hooks.json).

Receives hook context over stdin, parses lifecycle metadata, resolves Git repository context,
attaches local developer identity, and safely registers events into the local SQLite store.
"""

import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import load_settings
from src.core.collector import TelemetryCollector
from src.core.event import AITelemetryPayload, EventType
from src.core.repository import detect_repository_context
from src.logging.structured import sanitize_data
from src.sources.ai_provider import AIEventBuilder


def process_hook(hook_type: str) -> None:
    """Read stdin from Antigravity hook, emit telemetry event, and return required stdout."""
    raw_input = ""
    try:
        raw_input = sys.stdin.read()
        data: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        data = {}

    # Extract common hook metadata
    conversation_id = data.get("conversationId")
    model_name = data.get("modelName")
    step_idx = data.get("stepIdx")
    tool_call = data.get("toolCall", {})
    tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
    error = data.get("error")

    # Detect repository context from workspacePaths or process working directory
    workspace_paths = data.get("workspacePaths", [])
    target_path = workspace_paths[0] if workspace_paths and len(workspace_paths) > 0 else os.getcwd()
    repo_context = detect_repository_context(target_path)
    repo_dict = repo_context.to_dict(include_local_root=True) if repo_context.is_git_repo else None

    # Clean metadata (no source code, no secret values)
    clean_meta = {
        "hook_type": hook_type,
        "step_idx": step_idx,
    }
    if error:
        clean_meta["error"] = str(error)

    # Initialize local collector for storage insertion
    try:
        settings = load_settings()
        collector = TelemetryCollector(settings)

        if hook_type in ("PreInvocation", "pre_invocation"):
            raw_event = AIEventBuilder.build_run_started(
                provider="antigravity",
                model=model_name,
                session_id=conversation_id,
                capture_method="hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
        elif hook_type in ("Stop", "stop"):
            if error:
                raw_event = AIEventBuilder.build_run_failed(
                    provider="antigravity",
                    model=model_name,
                    session_id=conversation_id,
                    error_message=str(error),
                    capture_method="hooks",
                    repository=repo_dict,
                    metadata=clean_meta,
                )
            else:
                raw_event = AIEventBuilder.build_run_completed(
                    provider="antigravity",
                    model=model_name,
                    session_id=conversation_id,
                    capture_method="hooks",
                    repository=repo_dict,
                    metadata=clean_meta,
                )
        elif hook_type in ("PreToolUse", "PostToolUse"):
            raw_event = AIEventBuilder.build_tool_call(
                tool_name=tool_name or "unknown_tool",
                session_id=conversation_id,
                capture_method="hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
        else:
            raw_event = None

        if raw_event:
            event = collector.processor.process_raw(raw_event)
            if event:
                collector.queue.enqueue(event)
                collector.process_queue_batch()

    except Exception:
        # Never fail or break the developer's agent loop
        pass

    # Provide required output back to Antigravity over stdout
    output: Dict[str, Any] = {}
    if hook_type == "PreToolUse":
        output = {"decision": "allow"}
    elif hook_type == "Stop":
        output = {"decision": "allow"}

    sys.stdout.write(json.dumps(output) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    hook_type = sys.argv[1] if len(sys.argv) > 1 else "PreInvocation"
    process_hook(hook_type)
