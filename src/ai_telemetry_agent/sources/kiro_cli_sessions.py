"""Kiro CLI Session File Reader Source.

Reads and normalizes session telemetry from ~/.kiro/sessions/cli/{session_id}.json.
Adheres strictly to the AI telemetry schema:
- Extracts authoritative credit metering without converting to USD.
- Extracts context utilization percentage and turn duration.
- Correctly preserves model selection without misidentifying 'auto' as a resolved model.
- Maps unreported token count fields (which default to 0 in Kiro's Rust struct) to None.
- Implements robust incremental ingestion and turn-level deduplication.
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

logger = get_logger("telemetry.source.kiro_cli")


def get_default_kiro_cli_sessions_dir() -> Path:
    """Return default directory where Kiro CLI persists session JSON files."""
    override = os.environ.get("KIRO_CLI_SESSIONS_DIR")
    if override:
        return Path(override).resolve()
    return (Path.home() / ".kiro" / "sessions" / "cli").resolve()


def get_default_kiro_cli_state_file() -> Path:
    """Return persistent state file tracking ingested Kiro CLI sessions and turns."""
    home = os.environ.get("TELEMETRY_AGENT_HOME")
    base = Path(home).resolve() if home else (Path.home() / ".telemetry_agent").resolve()
    return base / "state" / "kiro_cli_state.json"


def parse_kiro_cli_jsonl(jsonl_file: Path) -> Dict[str, Any]:
    """Parse Kiro CLI session JSONL file and extract tool invocations and results.

    Resilient to:
    - Non-existent or empty files.
    - Blank lines.
    - Malformed individual JSON lines.
    - Partially written final lines.
    - Unknown message types.
    - Missing toolUseId or tool names.
    - Duplicate message records.
    - Tool results arriving without a recognized invocation.
    - Tool invocations without results.

    Strict privacy allowlist:
    Only captures tool_name, status, and tool_use_id.
    Explicitly excludes input arguments, command/file paths, file contents,
    tool outputs, prompts, and assistant response texts.
    """
    if not jsonl_file.is_file():
        return {"invocations": [], "results": {}}

    try:
        content = jsonl_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"invocations": [], "results": {}}

    invocations: List[Dict[str, Any]] = []
    results: Dict[str, str] = {}
    seen_tool_use_ids: Set[str] = set()

    current_prompt_index = -1

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(record, dict):
            continue

        kind = record.get("kind") or record.get("type") or ""
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        msg_id = data.get("message_id") or record.get("message_id") or record.get("id")

        if kind == "Prompt":
            current_prompt_index += 1

        # 1. Tool Invocations
        content_items: List[Any] = []
        if isinstance(data.get("content"), list):
            content_items.extend(data["content"])
        if isinstance(record.get("content"), list):
            content_items.extend(record["content"])

        for item in content_items:
            if not isinstance(item, dict):
                continue
            item_kind = item.get("kind") or item.get("type") or ""
            if item_kind in ("toolUse", "tool_use"):
                item_data = item.get("data") if isinstance(item.get("data"), dict) else item
                tid = item_data.get("toolUseId") or item_data.get("tool_use_id") or item_data.get("id")
                tname = item_data.get("name") or item_data.get("tool_name") or item_data.get("toolName")
                if tname and isinstance(tname, str):
                    tname_str = tname.strip()
                    if tname_str:
                        tid_str = str(tid).strip() if tid else None
                        if tid_str and tid_str in seen_tool_use_ids:
                            continue
                        if tid_str:
                            seen_tool_use_ids.add(tid_str)
                        invocations.append({
                            "tool_use_id": tid_str,
                            "tool_name": tname_str,
                            "message_id": str(msg_id) if msg_id else None,
                            "prompt_index": current_prompt_index if current_prompt_index >= 0 else 0,
                        })

        # Standalone toolUse record
        if kind in ("toolUse", "tool_use"):
            item_data = data if data else record
            tid = item_data.get("toolUseId") or item_data.get("tool_use_id") or item_data.get("id")
            tname = item_data.get("name") or item_data.get("tool_name") or item_data.get("toolName")
            if tname and isinstance(tname, str):
                tname_str = tname.strip()
                if tname_str:
                    tid_str = str(tid).strip() if tid else None
                    if not (tid_str and tid_str in seen_tool_use_ids):
                        if tid_str:
                            seen_tool_use_ids.add(tid_str)
                        invocations.append({
                            "tool_use_id": tid_str,
                            "tool_name": tname_str,
                            "message_id": str(msg_id) if msg_id else None,
                            "prompt_index": current_prompt_index if current_prompt_index >= 0 else 0,
                        })

        # 2. Tool Results
        for item in content_items:
            if not isinstance(item, dict):
                continue
            item_kind = item.get("kind") or item.get("type") or ""
            if item_kind in ("toolResult", "tool_result"):
                item_data = item.get("data") if isinstance(item.get("data"), dict) else item
                tid = item_data.get("toolUseId") or item_data.get("tool_use_id") or item_data.get("id")
                raw_st = item_data.get("status") or item_data.get("result_status")
                if tid and raw_st and isinstance(raw_st, str):
                    tid_str = str(tid).strip()
                    st_str = raw_st.strip().lower()
                    if st_str in ("success", "ok", "completed"):
                        results[tid_str] = "success"
                    elif st_str in ("error", "failed", "failure"):
                        results[tid_str] = "error"
                    else:
                        results[tid_str] = st_str

        # Results map in ToolResults
        res_map = data.get("results") or record.get("results")
        if isinstance(res_map, dict):
            for r_tid, r_val in res_map.items():
                if not isinstance(r_val, dict):
                    continue
                r_inner = r_val.get("result")
                r_tid_str = str(r_tid).strip()
                if isinstance(r_inner, dict):
                    if "Success" in r_inner or "success" in r_inner:
                        results[r_tid_str] = "success"
                    elif any(k in r_inner for k in ("Failure", "failure", "Error", "error")):
                        results[r_tid_str] = "error"
                elif isinstance(r_inner, str):
                    st_str = r_inner.strip().lower()
                    if st_str in ("success", "ok", "completed"):
                        results[r_tid_str] = "success"
                    elif st_str in ("error", "failed", "failure"):
                        results[r_tid_str] = "error"
                    else:
                        results[r_tid_str] = st_str

        # Standalone toolResult record
        if kind in ("toolResult", "tool_result"):
            item_data = data if data else record
            tid = item_data.get("toolUseId") or item_data.get("tool_use_id") or item_data.get("id")
            raw_st = item_data.get("status") or item_data.get("result_status")
            if tid and raw_st and isinstance(raw_st, str):
                tid_str = str(tid).strip()
                st_str = raw_st.strip().lower()
                if st_str in ("success", "ok", "completed"):
                    results[tid_str] = "success"
                elif st_str in ("error", "failed", "failure"):
                    results[tid_str] = "error"
                else:
                    results[tid_str] = st_str

    return {"invocations": invocations, "results": results}


def extract_turn_tools(
    turn: Dict[str, Any],
    raw_turn: Dict[str, Any],
    turn_idx: int,
    parsed_jsonl: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Correlate tool invocations to a specific user turn and match results.

    Returns an explicitly sanitized list of tool dictionaries containing only:
    - tool_name: str
    - status: str ('success', 'error', or 'unknown')
    - tool_use_id: Optional[str]
    """
    invocations = parsed_jsonl.get("invocations", [])
    results_map = parsed_jsonl.get("results", {})
    if not invocations:
        return []

    raw_msg_ids = raw_turn.get("message_ids") or turn.get("message_ids")
    if isinstance(raw_msg_ids, list) and raw_msg_ids:
        msg_id_set = {str(m).strip() for m in raw_msg_ids if m}
        turn_invocations = [inv for inv in invocations if inv.get("message_id") in msg_id_set]
    else:
        turn_invocations = [inv for inv in invocations if inv.get("prompt_index") == turn_idx]

    turn_tools: List[Dict[str, Any]] = []
    for inv in turn_invocations:
        tname = inv.get("tool_name")
        if not tname or not isinstance(tname, str):
            continue
        tid = inv.get("tool_use_id")
        if tid and tid in results_map:
            status = results_map[tid]
        else:
            status = "unknown"

        turn_tools.append({
            "tool_name": tname,
            "status": status,
            "tool_use_id": tid,
        })

    return turn_tools


class KiroCLISessionSource(EventSource):
    """Event source for monitoring and parsing Kiro CLI session JSON files."""

    def __init__(
        self,
        enabled: bool = True,
        sessions_dir: Optional[Path] = None,
        state_file: Optional[Path] = None,
    ):
        self.enabled = enabled
        self.sessions_dir = sessions_dir or get_default_kiro_cli_sessions_dir()
        self.state_file = state_file or get_default_kiro_cli_state_file()
        self._started = False
        self._detected_emitted = False
        self._buffered_events: List[Dict[str, Any]] = []
        # State schema: {session_id: {"updated_at": str, "ingested_turns": [str, ...]}}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._staged_state: Dict[str, Dict[str, Any]] = {}
        self._load_state()

    @property
    def name(self) -> str:
        return "kiro_cli"

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
                logger.warning("kiro_cli_state_load_failed", error=str(e))
                self._state = {}
        self._staged_state = {
            k: {
                "updated_at": v.get("updated_at"),
                "ingested_turns": list(v.get("ingested_turns", [])),
                "deferred_turns": dict(v.get("deferred_turns", {})) if isinstance(v.get("deferred_turns"), dict) else {},
            }
            for k, v in self._state.items()
        }

    def commit_state(self) -> bool:
        """Atomically persist staged reader cursor state to disk."""
        try:
            self._state = {
                k: {
                    "updated_at": v.get("updated_at"),
                    "ingested_turns": list(v.get("ingested_turns", [])),
                    "deferred_turns": dict(v.get("deferred_turns", {})),
                }
                for k, v in self._staged_state.items()
            }
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.state_file.parent / f"{self.state_file.name}.tmp"
            tmp_file.write_text(json.dumps(self._state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp_file.replace(self.state_file)
            return True
        except Exception as e:
            logger.error("kiro_cli_state_commit_failed", error=str(e))
            return False

    def rollback_state(self) -> None:
        """Revert staged cursor state in memory back to the last committed state."""
        self._staged_state = {
            k: {
                "updated_at": v.get("updated_at"),
                "ingested_turns": list(v.get("ingested_turns", [])),
                "deferred_turns": dict(v.get("deferred_turns", {})),
            }
            for k, v in self._state.items()
        }

    def start(self) -> None:
        self._started = True
        exists = self.sessions_dir.exists()
        if exists:
            self._buffered_events.append({
                "event_type": EventType.KIRO_DETECTED,
                "payload": {
                    "source": "kiro_cli",
                    "sessions_dir": str(self.sessions_dir),
                    "status": "DETECTED",
                },
            })
            self._detected_emitted = True

        logger.info(
            "kiro_cli_source_started",
            sessions_dir=str(self.sessions_dir),
            exists=exists,
            enabled=self.enabled,
        )

    def stop(self) -> None:
        self._started = False
        self.commit_state()
        logger.info("kiro_cli_source_stopped")

    def collect(self) -> List[Dict[str, Any]]:
        """Scan sessions directory, extract new turns, and return raw telemetry events."""
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
                    "source": "kiro_cli",
                    "sessions_dir": str(self.sessions_dir),
                    "status": "DETECTED",
                },
            })
            self._detected_emitted = True

        try:
            session_files = list(self.sessions_dir.glob("*.json"))
        except Exception as e:
            logger.warning("kiro_cli_dir_glob_failed", error=str(e))
            return events

        for s_file in session_files:
            # Skip hidden files or lock files
            if s_file.name.startswith(".") or s_file.suffix.lower() != ".json":
                continue

            try:
                new_events = self._process_session_file(s_file)
                if new_events:
                    events.extend(new_events)
            except Exception as e:
                logger.debug("kiro_cli_file_process_failed", file=s_file.name, error=str(e))

        return events

    def _process_session_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Parse single session JSON file with partial-write recovery and turn deduplication."""
        try:
            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                return []
            data = json.loads(content)
        except (json.JSONDecodeError, OSError):
            # Concurrent write in progress; defer to next poll
            return []

        if not isinstance(data, dict):
            return []

        session_id = data.get("session_id") or data.get("session_state", {}).get("id") or file_path.stem
        if not session_id or not isinstance(session_id, str):
            return []

        try:
            mtime_str = str(file_path.stat().st_mtime)
        except Exception:
            mtime_str = ""

        updated_at = data.get("updated_at") or data.get("session_state", {}).get("updated_at") or mtime_str
        cwd = data.get("cwd") or data.get("session_state", {}).get("cwd") or os.getcwd()

        # Check existing state
        session_state_entry = self._staged_state.setdefault(
            session_id, {"updated_at": "", "ingested_turns": [], "deferred_turns": {}}
        )
        ingested_turn_ids: Set[str] = set(session_state_entry.get("ingested_turns", []))
        deferred_turns: Dict[str, int] = session_state_entry.setdefault("deferred_turns", {})

        # Check if already fully processed and unchanged (and no pending deferred turns)
        if not deferred_turns and session_state_entry.get("updated_at") == updated_at and ingested_turn_ids:
            return []

        # Find corresponding JSONL message file
        jsonl_path = file_path.with_suffix(".jsonl")
        if not jsonl_path.is_file():
            alt_path = file_path.parent / f"{session_id}.jsonl"
            if alt_path.is_file():
                jsonl_path = alt_path

        has_jsonl = jsonl_path.is_file()
        parsed_jsonl = parse_kiro_cli_jsonl(jsonl_path) if has_jsonl else {}

        conv_metadata = data.get("session_state", {}).get("conversation_metadata", {})
        user_turn_metadatas = conv_metadata.get("user_turn_metadatas", [])
        if not isinstance(user_turn_metadatas, list):
            return []

        # Resolve repository context
        repo_context = detect_repository_context(cwd)
        repo_dict = repo_context.to_dict(include_local_root=True) if repo_context.is_git_repo else None

        events: List[Dict[str, Any]] = []

        for idx, raw_turn in enumerate(user_turn_metadatas):
            if not isinstance(raw_turn, dict):
                continue

            # Support both flat user_turn_metadata and nested result.Ok / wrapper dicts
            result_ok = raw_turn.get("result", {}).get("Ok", {})
            if isinstance(result_ok, dict) and "user_turn_metadata" in result_ok:
                turn = result_ok["user_turn_metadata"]
                explicit_turn_id = result_ok.get("id")
            elif "user_turn_metadata" in raw_turn and isinstance(raw_turn["user_turn_metadata"], dict):
                turn = raw_turn["user_turn_metadata"]
                explicit_turn_id = raw_turn.get("id")
            else:
                turn = raw_turn
                explicit_turn_id = raw_turn.get("id")

            # Stable turn identifier
            turn_id = explicit_turn_id or f"{session_id}_turn_{idx}"

            if turn_id in ingested_turn_ids:
                continue

            # Extract sanitized tools from JSONL
            turn_tools = extract_turn_tools(turn, raw_turn, idx, parsed_jsonl)

            # Delayed JSONL handling:
            # If JSONL file exists and the turn indicates tool use was expected,
            # but fewer tools were found in JSONL than expected:
            expected_tools = turn.get("builtin_tool_uses") or raw_turn.get("builtin_tool_uses") or 0
            if has_jsonl and isinstance(expected_tools, int) and expected_tools > 0 and len(turn_tools) < expected_tools:
                deferral_count = deferred_turns.get(turn_id, 0)
                if deferral_count < 1:
                    deferred_turns[turn_id] = deferral_count + 1
                    continue
                else:
                    # Max deferral reached; proceed with ingestion so parent credits are never lost
                    deferred_turns.pop(turn_id, None)
            else:
                deferred_turns.pop(turn_id, None)

            tool_name_field = turn_tools[0]["tool_name"] if len(turn_tools) == 1 else None

            # Model extraction and normalization
            raw_model = raw_turn.get("selected_model") or raw_turn.get("model") or turn.get("model")
            if is_kiro_routing_alias(raw_model):
                selected_model = raw_model  # Preserve the routing alias as the selection
                resolved_model = None  # Never label a routing alias as a resolved model
                display_model = raw_model
            else:
                selected_model = raw_model
                resolved_model = raw_model
                display_model = raw_model

            # Duration calculation from secs and nanos or ms directly
            duration_ms: Optional[float] = None
            if "turn_duration_ms" in turn and isinstance(turn["turn_duration_ms"], (int, float)):
                duration_ms = float(turn["turn_duration_ms"])
            else:
                raw_duration = turn.get("turn_duration")
                if isinstance(raw_duration, dict):
                    secs = raw_duration.get("secs", 0)
                    nanos = raw_duration.get("nanos", 0)
                    if isinstance(secs, (int, float)) and isinstance(nanos, (int, float)):
                        duration_ms = (secs * 1000.0) + (nanos / 1_000_000.0)

            # Credit metering extraction (keep distinct from USD cost)
            metering_usage = turn.get("metering_usage", [])
            total_credits: Optional[float] = None
            credit_unit: Optional[str] = None
            if isinstance(metering_usage, (int, float)):
                total_credits = float(metering_usage)
                credit_unit = "credit"
            elif isinstance(metering_usage, list) and metering_usage:
                credits_sum = 0.0
                has_valid_credit = False
                for m in metering_usage:
                    if isinstance(m, dict):
                        val = m.get("value")
                        if isinstance(val, (int, float)):
                            credits_sum += float(val)
                            has_valid_credit = True
                        if not credit_unit and m.get("unit"):
                            credit_unit = str(m.get("unit"))
                    elif isinstance(m, (int, float)):
                        credits_sum += float(m)
                        has_valid_credit = True
                if has_valid_credit:
                    total_credits = credits_sum
                    credit_unit = credit_unit or "credit"

            # Context utilization percentage
            ctx_percentage = turn.get("final_context_usage_percentage")
            if ctx_percentage is None:
                ctx_percentage = turn.get("context_usage_percentage")
            if not isinstance(ctx_percentage, (int, float)):
                ctx_percentage = None

            # Token counts: Raw values in JSON are 0 because backend does not report integer tokens.
            # Must map to None in normalized event to avoid reporting fake 0 token usage.
            raw_input_tokens = turn.get("input_token_count")
            raw_output_tokens = turn.get("output_token_count")
            raw_cache_read = turn.get("cache_read_input_token_count")
            raw_cache_write = turn.get("cache_write_input_token_count")

            # Preserved for diagnostic tracking without treating as actual token metrics
            raw_tokens_diag = {
                "raw_input_token_count": raw_input_tokens,
                "raw_output_token_count": raw_output_tokens,
                "raw_cache_read_input_token_count": raw_cache_read,
                "raw_cache_write_input_token_count": raw_cache_write,
                "unreported_by_backend": True,
            }

            clean_meta = {
                "turn_index": idx,
                "end_reason": turn.get("end_reason"),
                "total_request_count": turn.get("total_request_count"),
                "request_attempts": turn.get("request_attempts"),
                "builtin_tool_uses": turn.get("builtin_tool_uses"),
                "assistant_response_length": turn.get("assistant_response_length"),
                "user_prompt_length": turn.get("user_prompt_length"),
                "turn_duration_ms": duration_ms,
                "raw_token_diagnostics": raw_tokens_diag,
            }

            end_ts = turn.get("end_timestamp")

            turn_event = AIEventBuilder.build_run_completed(
                provider="kiro_cli",
                model=display_model,
                session_id=session_id,
                run_id=turn_id,
                duration_ms=duration_ms,
                # Explicitly None: exact integer tokens were not returned by Kiro backend
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                cache_tokens=None,
                total_tokens=None,
                cost_usd=None,  # Credits kept distinct from USD
                turns=1,
                tool_name=tool_name_field,
                tools=turn_tools,
                agent_name="kiro_cli",
                capture_method="session_json",
                repository=repo_dict,
                metadata=clean_meta,
                timestamp=end_ts,
                credits=total_credits,
                credit_unit=credit_unit,
                context_usage_percentage=ctx_percentage,
                selected_model=selected_model,
                resolved_model=resolved_model,
            )

            events.append(turn_event)
            ingested_turn_ids.add(turn_id)

        # Update staged state
        if not deferred_turns:
            session_state_entry["updated_at"] = updated_at
        session_state_entry["ingested_turns"] = list(ingested_turn_ids)

        return events
