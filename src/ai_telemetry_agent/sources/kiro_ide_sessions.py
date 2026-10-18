"""Kiro IDE Session Reader Source.

Reads and normalizes session telemetry from ~/.kiro/sessions/<workspace_hash>/<session_id>/.
Adheres strictly to the AI telemetry schema:
- Reads session.json and messages.jsonl.
- Extracts tool calls, turn completions, context utilization percentage, and credit summaries.
- Never collects message contents, tool arguments, or prompt text.
- Maps unavailable token counts to None rather than fabricating numbers.
- Implements incremental line-based cursor tracking to avoid re-reading previously ingested messages.
"""

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ai_telemetry_agent.core.event import EventType, is_kiro_routing_alias
from ai_telemetry_agent.core.repository import detect_repository_context
from ai_telemetry_agent.logging.structured import get_logger, sanitize_data
from ai_telemetry_agent.sources.ai_provider import AIEventBuilder
from ai_telemetry_agent.sources.base import EventSource

logger = get_logger("telemetry.source.kiro_ide")


def get_default_kiro_ide_sessions_dir() -> Path:
    """Return default root directory for Kiro IDE sessions."""
    override = os.environ.get("KIRO_IDE_SESSIONS_DIR")
    if override:
        return Path(override).resolve()
    return (Path.home() / ".kiro" / "sessions").resolve()


def get_default_kiro_ide_state_file() -> Path:
    """Return persistent state file tracking ingested Kiro IDE sessions and line offsets."""
    home = os.environ.get("TELEMETRY_AGENT_HOME")
    base = Path(home).resolve() if home else (Path.home() / ".telemetry_agent").resolve()
    return base / "state" / "kiro_ide_state.json"


def correlate_and_enrich_run_tools(
    completed_event: Dict[str, Any],
    tool_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Correlate tool events to an ai_run_completed event and enrich it with sanitized tool metadata.

    Correlation order:
    1. Provider match ('kiro_ide').
    2. Session ID match.
    3. Execution/run ID match (tool event execution_id == completed_event run_id).
       - If completed run has an execution ID, tools without a matching execution_id are excluded.
       - If completed run has no execution ID, only tools with no execution_id are attributed.
    4. Status determination: 'approved' without execution-result evidence remains 'unknown'.
    5. Deduplication: Invocations with identical tool_use_id are deduplicated;
       distinct invocations with the same tool_name are preserved.
    6. Privacy: Allowlist only tool_name, status, and tool_use_id.
    """
    if not isinstance(completed_event, dict):
        return completed_event

    payload = completed_event.get("payload")
    if not isinstance(payload, dict):
        return completed_event

    target_provider = payload.get("provider") or "kiro_ide"
    target_session_id = (
        (completed_event.get("context", {}) or {}).get("session_id")
        or payload.get("session_id")
    )
    target_run_id = payload.get("run_id") or payload.get("execution_id")

    candidate_tools: List[Dict[str, Any]] = []
    seen_tool_use_ids: Set[str] = set()

    for te in tool_events:
        if not isinstance(te, dict):
            continue
        t_payload = te.get("payload", {})
        if not isinstance(t_payload, dict):
            continue

        # 1. Provider check
        t_prov = t_payload.get("provider") or "kiro_ide"
        if t_prov != target_provider:
            continue

        # 2. Session ID check
        t_sess = (
            (te.get("context", {}) or {}).get("session_id")
            or t_payload.get("session_id")
        )
        if target_session_id and t_sess and str(t_sess) != str(target_session_id):
            continue

        # 3. Execution ID check
        t_meta = t_payload.get("metadata", {}) if isinstance(t_payload.get("metadata"), dict) else {}
        t_exec_id = t_meta.get("execution_id") or t_payload.get("execution_id")

        if target_run_id:
            if not t_exec_id or str(t_exec_id) != str(target_run_id):
                continue
        else:
            if t_exec_id:
                continue

        # 4. Extract sanitized fields
        tool_name = t_payload.get("tool_name") or t_meta.get("tool_name") or "unknown_tool"
        tool_use_id = (
            t_meta.get("tool_call_id")
            or t_meta.get("tool_use_id")
            or t_payload.get("run_id")
            or t_payload.get("tool_call_id")
            or ""
        )
        raw_status = t_meta.get("status") or t_payload.get("status")
        # Status rule: 'approved' without execution-result evidence remains 'unknown'
        if raw_status in ("success", "error"):
            status_val = raw_status
        else:
            status_val = "unknown"

        tid_str = str(tool_use_id)
        if tid_str and tid_str in seen_tool_use_ids:
            continue
        if tid_str:
            seen_tool_use_ids.add(tid_str)

        candidate_tools.append({
            "tool_name": str(tool_name),
            "status": status_val,
            "tool_use_id": tid_str,
        })

    payload["tools"] = candidate_tools
    payload["tool_name"] = candidate_tools[0]["tool_name"] if len(candidate_tools) == 1 else None
    return completed_event


class KiroIDESessionSource(EventSource):
    """Event source for monitoring and parsing Kiro IDE session artifacts."""

    def __init__(
        self,
        enabled: bool = True,
        sessions_dir: Optional[Path] = None,
        state_file: Optional[Path] = None,
    ):
        self.enabled = enabled
        self.sessions_dir = sessions_dir or get_default_kiro_ide_sessions_dir()
        self.state_file = state_file or get_default_kiro_ide_state_file()
        self._started = False
        self._detected_emitted = False
        self._buffered_events: List[Dict[str, Any]] = []
        # State schema: {session_id: {"last_modified_at": str, "processed_lines": int, "started_emitted": bool, "pending_tools": dict, "pending_results": dict, "completed_exec_ids": list}}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._staged_state: Dict[str, Dict[str, Any]] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "kiro_ide"

    @property
    def status(self) -> str:
        if not self.sessions_dir.exists():
            return "NOT_INSTALLED"
        return "ACTIVE" if self._started and self.enabled else "STANDBY"

    def _load_state(self) -> None:
        if self.state_file.is_file():
            try:
                content = self.state_file.read_text(encoding="utf-8")
                data = json.loads(content)
                if isinstance(data, dict):
                    self._state = data
            except Exception as e:
                logger.warning("kiro_ide_state_load_failed", error=str(e))
                self._state = {}
        self._staged_state = {k: dict(v) for k, v in self._state.items()}

    def commit_state(self) -> bool:
        """Atomically persist staged reader cursor state to disk."""
        try:
            self._state = {k: dict(v) for k, v in self._staged_state.items()}
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.state_file.parent / f"{self.state_file.name}.tmp"
            tmp_file.write_text(json.dumps(self._state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp_file.replace(self.state_file)
            return True
        except Exception as e:
            logger.error("kiro_ide_state_commit_failed", error=str(e))
            return False

    def rollback_state(self) -> None:
        """Revert staged cursor state in memory back to the last committed state."""
        self._staged_state = {k: dict(v) for k, v in self._state.items()}

    def start(self) -> None:
        self._started = True
        exists = self.sessions_dir.exists()
        if exists:
            self._buffered_events.append({
                "event_type": EventType.KIRO_DETECTED,
                "payload": {
                    "source": "kiro_ide",
                    "sessions_dir": str(self.sessions_dir),
                    "status": "DETECTED",
                },
            })
            self._detected_emitted = True

        logger.info(
            "kiro_ide_source_started",
            sessions_dir=str(self.sessions_dir),
            exists=exists,
            enabled=self.enabled,
        )

    def stop(self) -> None:
        self._started = False
        self.commit_state()
        logger.info("kiro_ide_source_stopped")

    def collect(self) -> List[Dict[str, Any]]:
        """Scan IDE session folders, extract new tool/usage records, and return raw telemetry events."""
        if not self._started or not self.enabled:
            return []

        events: List[Dict[str, Any]] = []
        if self._buffered_events:
            events.extend(self._buffered_events)
            self._buffered_events.clear()

        if not self.sessions_dir.is_dir():
            return events

        if not self._detected_emitted:
            events.append({
                "event_type": EventType.KIRO_DETECTED,
                "payload": {
                    "source": "kiro_ide",
                    "sessions_dir": str(self.sessions_dir),
                    "status": "DETECTED",
                },
            })
            self._detected_emitted = True

        # Discover all workspace hash directories (excluding 'cli')
        try:
            for workspace_entry in self.sessions_dir.iterdir():
                if not workspace_entry.is_dir() or workspace_entry.name == "cli" or workspace_entry.name.startswith("."):
                    continue

                for sess_dir in workspace_entry.iterdir():
                    if not sess_dir.is_dir() or sess_dir.name.startswith("."):
                        continue

                    try:
                        sess_events = self._process_session_dir(sess_dir)
                        if sess_events:
                            events.extend(sess_events)
                    except Exception as e:
                        logger.debug("kiro_ide_sess_dir_error", sess_dir=sess_dir.name, error=str(e))
        except Exception as e:
            logger.warning("kiro_ide_scan_failed", error=str(e))

        return events

    def _process_session_dir(self, sess_dir: Path) -> List[Dict[str, Any]]:
        """Process session.json and messages.jsonl for an individual IDE session."""
        session_json_file = sess_dir / "session.json"
        messages_jsonl_file = sess_dir / "messages.jsonl"

        if not session_json_file.is_file():
            return []

        try:
            content = session_json_file.read_text(encoding="utf-8")
            if not content.strip():
                return []
            s_data = json.loads(content)
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(s_data, dict):
            return []

        session_id = s_data.get("id") or sess_dir.name
        created_at = s_data.get("createdAt")
        last_modified_at = s_data.get("lastModifiedAt") or ""
        raw_model = s_data.get("modelId")
        workspace_paths = s_data.get("workspacePaths") or s_data.get("rootPaths") or []

        # Resolve repository context
        repo_dict = None
        if isinstance(workspace_paths, list) and workspace_paths:
            for p in workspace_paths:
                if p:
                    ctx = detect_repository_context(p)
                    if ctx.is_git_repo:
                        repo_dict = ctx.to_dict(include_local_root=True)
                        break
            if not repo_dict and workspace_paths[0]:
                ctx = detect_repository_context(workspace_paths[0])
                repo_dict = ctx.to_dict(include_local_root=True) if ctx.is_git_repo else None

        # State check
        sess_state = self._staged_state.setdefault(
            session_id,
            {"last_modified_at": "", "processed_lines": 0, "started_emitted": False}
        )

        events: List[Dict[str, Any]] = []

        # Model resolution
        # `modelId` records the model selection mode (e.g. "auto"); the resolved
        # backend model is chosen server-side and is not persisted locally.
        selected_model = raw_model
        resolved_model = None if is_kiro_routing_alias(raw_model) else raw_model

        # Emit session started if not yet emitted
        if not sess_state.get("started_emitted", False):
            start_event = AIEventBuilder.build_run_started(
                provider="kiro_ide",
                model=raw_model,
                session_id=session_id,
                started_at=created_at,
                agent_name="kiro_ide",
                capture_method="ide_session_files",
                repository=repo_dict,
                selected_model=selected_model,
                resolved_model=resolved_model,
                metadata={"title": s_data.get("title"), "agent_mode": s_data.get("agentMode")},
            )
            events.append(start_event)
            sess_state["started_emitted"] = True

        # Read messages.jsonl incrementally
        if messages_jsonl_file.is_file():
            processed_lines = sess_state.get("processed_lines", 0)
            try:
                with open(messages_jsonl_file, "r", encoding="utf-8", errors="replace") as f:
                    for current_line_idx, line in enumerate(f):
                        if current_line_idx < processed_lines:
                            continue

                        line_str = line.strip()
                        if not line_str:
                            processed_lines = current_line_idx + 1
                            continue

                        try:
                            msg_entry = json.loads(line_str)
                        except json.JSONDecodeError:
                            # Incomplete write at end of file; break and retry next poll
                            break

                        if not isinstance(msg_entry, dict):
                            processed_lines = current_line_idx + 1
                            continue

                        ev = self._parse_message_entry(
                            msg_entry,
                            session_id,
                            repo_dict,
                            selected_model,
                            resolved_model,
                            sess_state=sess_state,
                        )
                        if ev:
                            events.append(ev)

                        processed_lines = current_line_idx + 1

                sess_state["processed_lines"] = processed_lines
            except Exception as e:
                logger.debug("kiro_ide_messages_read_error", session_id=session_id, error=str(e))

        sess_state["last_modified_at"] = last_modified_at
        return events

    def _parse_message_entry(
        self,
        entry: Dict[str, Any],
        session_id: str,
        repo_dict: Optional[Dict[str, Any]],
        selected_model: Optional[str],
        resolved_model: Optional[str],
        sess_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Map individual messages.jsonl entry to standardized telemetry event."""
        payload = entry.get("payload", entry)
        if not isinstance(payload, dict):
            return None

        msg_type = payload.get("type") or entry.get("type")
        timestamp = entry.get("timestamp")

        # 1. Tool Call
        if msg_type in ("tool_call", "tool_use"):
            tool_name = payload.get("toolName") or payload.get("name") or payload.get("actionType") or "unknown_tool"
            tool_call_id = payload.get("toolCallId") or entry.get("id")
            exec_id = payload.get("executionId")
            raw_status = payload.get("status")
            clean_meta = {
                "tool_call_id": tool_call_id,
                "status": raw_status,
                "action_type": payload.get("actionType"),
                "execution_id": exec_id,
            }

            if sess_state is not None:
                pending_tools = sess_state.setdefault("pending_tools", {})
                pending_results = sess_state.setdefault("pending_results", {})
                exec_key = str(exec_id) if exec_id else ""
                tool_list = pending_tools.setdefault(exec_key, [])
                tid_str = str(tool_call_id) if tool_call_id else ""

                # Deduplication: do not add duplicate invocation with the same toolCallId
                existing_tool = next((t for t in tool_list if t.get("tool_use_id") == tid_str), None)
                if not existing_tool:
                    # Check if result arrived beforehand or out of order
                    res_status = pending_results.get(tid_str, "unknown")
                    tool_list.append({
                        "tool_name": str(tool_name),
                        "status": res_status,
                        "tool_use_id": tid_str,
                        "execution_id": exec_id,
                    })

            return AIEventBuilder.build_tool_call(
                tool_name=tool_name,
                provider="kiro_ide",
                session_id=session_id,
                run_id=tool_call_id,
                capture_method="messages_jsonl",
                repository=repo_dict,
                metadata=clean_meta,
            )

        # 2. Tool Result
        if msg_type in ("tool_result",):
            tool_call_id = payload.get("toolCallId") or entry.get("id")
            tid_str = str(tool_call_id) if tool_call_id else ""

            if payload.get("success") is True:
                res_status = "success"
            elif payload.get("success") is False or payload.get("is_error") is True or payload.get("error"):
                res_status = "error"
            elif str(payload.get("status", "")).lower() == "success":
                res_status = "success"
            elif str(payload.get("status", "")).lower() == "error":
                res_status = "error"
            else:
                res_status = "unknown"

            if sess_state is not None and tid_str:
                pending_results = sess_state.setdefault("pending_results", {})
                pending_results[tid_str] = res_status
                pending_tools = sess_state.setdefault("pending_tools", {})
                for tool_list in pending_tools.values():
                    for t in tool_list:
                        if t.get("tool_use_id") == tid_str:
                            t["status"] = res_status

            return None

        # 3. Context Usage Update
        if msg_type == "session_metadata" and payload.get("key") == "contextUsage":
            val = payload.get("value", {})
            if isinstance(val, dict):
                usage_pct = val.get("usagePercentage")
                if isinstance(usage_pct, (int, float)):
                    return {
                        "event_type": EventType.AI_TURN,
                        "timestamp": timestamp,
                        "context": {"session_id": session_id, "repository": repo_dict} if repo_dict else {"session_id": session_id},
                        "payload": {
                            "provider": "kiro_ide",
                            "session_id": session_id,
                            "context_usage_percentage": float(usage_pct),
                            "capture_method": "messages_jsonl",
                            "metadata": {"key": "contextUsage"},
                        },
                    }

        # 4. Usage Summary / Turn Completed (Credit Consumption)
        if msg_type in ("usage_summary", "turn_completed"):
            summaries = payload.get("promptTurnSummaries", [])
            total_credits: Optional[float] = None
            credit_unit: Optional[str] = None
            if isinstance(payload.get("credits"), (int, float)):
                total_credits = float(payload["credits"])
                credit_unit = payload.get("credit_unit") or "credit"
            elif isinstance(summaries, list) and summaries:
                c_sum = 0.0
                has_cred = False
                for s in summaries:
                    if isinstance(s, dict):
                        val = s.get("usage")
                        if isinstance(val, (int, float)):
                            c_sum += float(val)
                            has_cred = True
                        if not credit_unit and s.get("unit"):
                            credit_unit = str(s.get("unit"))
                if has_cred:
                    total_credits = c_sum
                    credit_unit = credit_unit or "credit"

            elapsed_ms = payload.get("elapsedTime") or payload.get("turn_duration_ms")
            duration_ms = float(elapsed_ms) if isinstance(elapsed_ms, (int, float)) else None
            status = payload.get("status")
            exec_id = payload.get("executionId") or entry.get("id")
            exec_key = str(exec_id) if exec_id else ""

            # Replay protection: if completed run was already emitted for this exec_id, return None
            if sess_state is not None and exec_key:
                completed_exec_ids = sess_state.setdefault("completed_exec_ids", [])
                if exec_key in completed_exec_ids:
                    return None

            # Correlate tools for this completed run
            raw_tools: List[Dict[str, Any]] = []
            if sess_state is not None:
                pending_tools = sess_state.setdefault("pending_tools", {})
                if exec_key and exec_key in pending_tools:
                    raw_tools = pending_tools.pop(exec_key, [])
                elif not exec_key and "" in pending_tools:
                    raw_tools = pending_tools.pop("", [])

            clean_tools: List[Dict[str, Any]] = []
            seen_ids: Set[str] = set()
            for t in raw_tools:
                tid = str(t.get("tool_use_id", ""))
                if tid and tid in seen_ids:
                    continue
                if tid:
                    seen_ids.add(tid)
                status_val = t.get("status", "unknown")
                clean_tools.append({
                    "tool_name": str(t.get("tool_name", "unknown_tool")),
                    "status": str(status_val) if status_val in ("success", "error") else "unknown",
                    "tool_use_id": tid,
                })

            single_tool_name = clean_tools[0]["tool_name"] if len(clean_tools) == 1 else None

            if sess_state is not None and exec_key:
                sess_state.setdefault("completed_exec_ids", []).append(exec_key)

            ctx_pct = payload.get("context_usage_percentage")
            if ctx_pct is not None and not isinstance(ctx_pct, (int, float)):
                ctx_pct = None

            return AIEventBuilder.build_run_completed(
                provider="kiro_ide",
                model=selected_model,
                session_id=session_id,
                run_id=exec_id,
                duration_ms=duration_ms,
                # Explicitly None: exact tokens are not returned by Kiro IDE files
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                cache_tokens=None,
                total_tokens=None,
                cost_usd=None,
                turns=1,
                tool_name=single_tool_name,
                tools=clean_tools,
                agent_name="kiro_ide",
                capture_method="messages_jsonl",
                repository=repo_dict,
                timestamp=timestamp,
                credits=total_credits,
                credit_unit=credit_unit,
                context_usage_percentage=ctx_pct,
                selected_model=selected_model,
                resolved_model=resolved_model,
                metadata={"status": status, "token_status": "unreported_by_backend"},
            )

        # 4. Sub-Agent Lifecycle Events
        if msg_type in ("sub_agent_start", "sub_agent_complete"):
            sub_id = payload.get("subSessionId") or entry.get("id")
            parent_exec_id = payload.get("parentExecutionId")
            sub_agent_name = payload.get("subAgentName") or "sub_agent"
            is_start = msg_type == "sub_agent_start"
            ev_type = EventType.AGENT_STARTED if is_start else EventType.AGENT_COMPLETED

            clean_meta = {
                "sub_agent_id": sub_id,
                "parent_execution_id": parent_exec_id,
                "sub_agent_name": sub_agent_name,
                "is_subagent": True,
                "lifecycle_action": msg_type,
            }

            return {
                "event_type": ev_type,
                "timestamp": timestamp,
                "context": {
                    "session_id": sub_id,
                    "repository": repo_dict,
                } if repo_dict else {"session_id": sub_id},
                "payload": {
                    "provider": "kiro_ide",
                    "session_id": sub_id,
                    "parent_session_id": session_id,
                    "run_id": f"{session_id}_sub_{sub_id}",
                    "agent_name": sub_agent_name,
                    "capture_method": "messages_jsonl",
                    # Subagent credits are bundled in parent turn's usage_summary;
                    # Child operation must NOT fabricate credits or double-count.
                    "credits": None,
                    "metadata": clean_meta,
                },
            }

        return None
