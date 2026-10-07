"""Lifecycle hook handler invoked by Antigravity (%USERPROFILE%/.gemini/config/hooks.json).

Receives hook context over stdin, parses lifecycle metadata, resolves Git repository context,
attaches local developer identity, and safely registers events into the local JSONL event store.
"""

import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict

# Add project root to sys.path if running standalone
CURRENT_DIR = Path(__file__).resolve().parent
for parent in [CURRENT_DIR.parents[2], CURRENT_DIR.parents[3]]:
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))

from ai_telemetry_agent.config.settings import load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.repository import detect_repository_context
from ai_telemetry_agent.sources.ai_provider import AIEventBuilder


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
    target_path = None
    repo_context = None

    if workspace_paths and isinstance(workspace_paths, list):
        # Find first path that is a valid git repository
        for wp in workspace_paths:
            if wp:
                ctx = detect_repository_context(wp)
                if ctx.is_git_repo:
                    repo_context = ctx
                    target_path = wp
                    break
        # If no git repository found in paths, use the first valid workspace path
        if not repo_context and len(workspace_paths) > 0 and workspace_paths[0]:
            target_path = workspace_paths[0]
            repo_context = detect_repository_context(target_path)

    if not repo_context:
        target_path = os.getcwd()
        repo_context = detect_repository_context(target_path)

    repo_dict = repo_context.to_dict(include_local_root=True) if repo_context.is_git_repo else None

    # Clean metadata (no source code, no secret values)
    clean_meta = {
        "hook_type": hook_type,
        "step_idx": step_idx,
    }
    if workspace_paths and len(workspace_paths) > 1:
        clean_meta["workspace_count"] = len(workspace_paths)
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
                collector.store.save_event(event)

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
