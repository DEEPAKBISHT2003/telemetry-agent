"""Lifecycle hook handler invoked by Kiro IDE (.kiro/hooks/telemetry.json or ~/.kiro/hooks/telemetry.json).

Receives hook context over stdin, parses lifecycle metadata, resolves Git repository context,
attaches local developer identity, and directly persists events into the local JSONL event store.
Never blocks user actions (exits 0) and never logs prompt content or sensitive tool arguments.
"""

import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

# Add project root to sys.path if running standalone
CURRENT_DIR = Path(__file__).resolve().parent
for parent in [CURRENT_DIR.parents[2], CURRENT_DIR.parents[3]]:
    if str(parent) not in sys.path:
        sys.path.insert(0, str(parent))

from ai_telemetry_agent.config.settings import load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.event import EventType
from ai_telemetry_agent.core.repository import detect_repository_context
from ai_telemetry_agent.sources.ai_provider import AIEventBuilder


def process_kiro_hook(hook_trigger: str, collector: Optional[TelemetryCollector] = None) -> None:
    """Read stdin from Kiro hook, emit telemetry event directly to store, and exit cleanly with code 0."""
    try:
        raw_input = sys.stdin.read()
        data: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        data = {}

    try:
        # Extract common hook metadata across triggers
        hook_event_name = data.get("hook_event_name") or hook_trigger
        session_id = data.get("session_id") or data.get("conversation_id")
        cwd = data.get("cwd") or os.getcwd()
        tool_name = data.get("tool_name")
        file_path = data.get("file_path")
        command = data.get("command")

        # Resolve Git repository context
        repo_context = detect_repository_context(cwd)
        repo_dict = repo_context.to_dict(include_local_root=True) if repo_context.is_git_repo else None

        # Resolve explicit parent AI operation correlation
        parent_op_id = (
            data.get("parent_operation_id")
            or data.get("parent_run_id")
            or data.get("parent_execution_id")
        )
        tool_use_id = data.get("tool_use_id")
        exec_id = data.get("execution_id")
        if not parent_op_id and tool_use_id and exec_id and tool_use_id != exec_id:
            parent_op_id = exec_id

        # Privacy-safe metadata (no prompt text, no tool arguments, no file contents)
        clean_meta: Dict[str, Any] = {
            "hook_trigger": hook_trigger,
            "hook_event_name": hook_event_name,
            "provider": "kiro_ide",
        }
        if parent_op_id:
            clean_meta["parent_operation_id"] = parent_op_id
        if file_path:
            clean_meta["file_extension"] = Path(file_path).suffix
        if command:
            clean_meta["command_name"] = Path(command.split()[0]).name if command.strip() else None

        if collector is None:
            settings = load_settings()
            collector = TelemetryCollector(settings)
        raw_event: Optional[Dict[str, Any]] = None

        if hook_trigger in ("SessionStart", "session_start"):
            raw_event = AIEventBuilder.build_run_started(
                provider="kiro_ide",
                session_id=session_id,
                capture_method="kiro_hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
        elif hook_trigger in ("UserPromptSubmit", "user_prompt_submit"):
            # Record turn submission without recording prompt content
            clean_meta["prompt_submitted"] = True
            if "prompt" in data and isinstance(data["prompt"], str):
                clean_meta["prompt_length"] = len(data["prompt"])
            raw_event = AIEventBuilder.build_turn(
                turns=1,
                session_id=session_id,
                capture_method="kiro_hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
        elif hook_trigger in ("PreToolUse", "PostToolUse", "pre_tool_use", "post_tool_use"):
            clean_meta["is_post"] = hook_trigger in ("PostToolUse", "post_tool_use")
            tool_call_id = data.get("tool_use_id") or data.get("execution_id")
            raw_event = AIEventBuilder.build_tool_call(
                tool_name=tool_name or "unknown_tool",
                provider="kiro_ide",
                session_id=session_id,
                run_id=tool_call_id,
                capture_method="kiro_hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
            if tool_call_id:
                raw_event["event_id"] = f"kiro_tool_{tool_call_id}"
        elif hook_trigger in ("Stop", "stop", "SessionEnd", "session_end"):
            # Assign deterministic run_id and event_id for the session completion so that
            # firing both Stop and SessionEnd does not result in duplicate events.
            run_id = f"{session_id}_completed" if session_id else None
            event_id = f"kiro_completed_{session_id}" if session_id else None
            raw_event = AIEventBuilder.build_run_completed(
                provider="kiro_ide",
                session_id=session_id,
                run_id=run_id,
                capture_method="kiro_hooks",
                repository=repo_dict,
                metadata=clean_meta,
            )
            if event_id:
                raw_event["event_id"] = event_id
        elif hook_trigger in ("PostFileSave", "PostFileCreate", "PostFileDelete"):
            clean_meta["file_action"] = hook_trigger
            raw_event = {
                "event_type": EventType.AI_TOOL_CALL,
                "context": {"session_id": session_id, "repository": repo_dict} if repo_dict else {"session_id": session_id},
                "payload": {
                    "provider": "kiro_ide",
                    "session_id": session_id,
                    "tool_name": f"fs_{hook_trigger.lower()}",
                    "capture_method": "kiro_hooks",
                    "metadata": clean_meta,
                },
            }
        elif hook_trigger in ("PreTaskExec", "PostTaskExec"):
            clean_meta["task_action"] = hook_trigger
            raw_event = {
                "event_type": EventType.AI_TOOL_CALL,
                "context": {"session_id": session_id, "repository": repo_dict} if repo_dict else {"session_id": session_id},
                "payload": {
                    "provider": "kiro_ide",
                    "session_id": session_id,
                    "tool_name": "task_exec",
                    "capture_method": "kiro_hooks",
                    "metadata": clean_meta,
                },
            }

        event = None
        rec = None
        resulting_op_id: Optional[str] = None
        jira_key: Optional[str] = None

        if raw_event and parent_op_id and "payload" in raw_event:
            raw_event["payload"]["parent_operation_id"] = parent_op_id

        if raw_event:
            event = collector.processor.process_raw(raw_event)
            if event:
                collector.store.save_event(event)
                rec = collector._ai_event_to_operation_record(event)
                if event.context and event.context.jira_task_id:
                    jira_key = event.context.jira_task_id

        # Resolve confirmed Jira ticket using attribution store with repository matching
        # Do not infer Jira attribution solely from timestamps without repository matching
        if not jira_key and repo_context.is_git_repo and collector and getattr(collector, "attribution_store", None):
            try:
                now_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                ev_ts = event.timestamp if event else now_ts
                attr = collector.attribution_store.find_attribution_for_event(
                    repo_name=repo_context.repository_name,
                    timestamp=ev_ts,
                    repo_root=repo_context.repository_root,
                )
                if attr:
                    jira_key = attr.issue_key
            except Exception:
                jira_key = None

        if rec:
            if not rec.jira_key and jira_key:
                rec.jira_key = jira_key
            if parent_op_id and not rec.parent_operation_id:
                rec.parent_operation_id = parent_op_id
            try:
                collector.accounting_store.save_operation(rec)
                resulting_op_id = rec.operation_id
            except Exception:
                pass

        is_shell = hook_trigger in ("PostFileSave", "PostFileCreate", "PostFileDelete")
        initiates_ai = hook_trigger in ("UserPromptSubmit", "user_prompt_submit", "SessionStart", "session_start")
        is_schedule = bool(clean_meta.get("is_schedule") or data.get("schedule_expression"))
        if is_schedule:
            trig = "scheduled"
        elif hook_trigger in ("UserPromptSubmit", "user_prompt_submit", "SessionStart", "session_start"):
            trig = "manual"
        else:
            trig = "event_driven"

        dedup_id = (
            data.get("execution_id")
            or data.get("tool_use_id")
            or (f"{session_id}:{hook_trigger}:{tool_name or file_path or command or ''}" if session_id else None)
        )

        try:
            collector.hook_registry.record_execution(
                hook_id=f"kiro_{hook_trigger.lower()}",
                hook_name=hook_trigger,
                trigger_type=trig,
                provider="kiro_ide",
                session_id=session_id,
                outcome="success",
                is_shell_only=is_shell,
                initiates_ai=initiates_ai,
                resulting_operation_id=resulting_op_id,
                parent_operation_id=parent_op_id,
                jira_key=jira_key,
                credits_used=0 if is_shell else None,
                measurement_status="measured" if is_shell else "unavailable",
                schedule_expression=data.get("schedule_expression"),
                metadata=clean_meta,
                deduplication_key=dedup_id,
            )
        except Exception:
            pass

    except Exception:
        # Fast and resilient: telemetry failure must never block user workflow
        pass

    # Exit 0 cleanly without writing to stdout (Kiro forwards stdout to model context if non-empty)
    sys.exit(0)


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "SessionStart"
    process_kiro_hook(trigger)
