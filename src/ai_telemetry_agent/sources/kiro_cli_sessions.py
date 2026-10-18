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
        self._staged_state = {k: {"updated_at": v.get("updated_at"), "ingested_turns": list(v.get("ingested_turns", []))} for k, v in self._state.items()}

    def commit_state(self) -> bool:
        """Atomically persist staged reader cursor state to disk."""
        try:
            self._state = {k: {"updated_at": v.get("updated_at"), "ingested_turns": list(v.get("ingested_turns", []))} for k, v in self._staged_state.items()}
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
        session_state_entry = self._staged_state.setdefault(session_id, {"updated_at": "", "ingested_turns": []})
        ingested_turn_ids: Set[str] = set(session_state_entry.get("ingested_turns", []))

        # Check if already fully processed and unchanged
        if session_state_entry.get("updated_at") == updated_at and ingested_turn_ids:
            return []

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
        session_state_entry["updated_at"] = updated_at
        session_state_entry["ingested_turns"] = list(ingested_turn_ids)

        return events
