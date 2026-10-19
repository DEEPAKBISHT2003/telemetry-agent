"""Date-based JSONL telemetry storage module.

Stores events in ~/.telemetry_agent/data/telemetry-YYYY-MM-DD.jsonl
using UTC dates, append-only JSON lines, and thread/process-safe writes.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Set, Union

from ai_telemetry_agent.config.settings import get_user_agent_home, resolve_data_dir
from ai_telemetry_agent.core.event import TelemetryEvent
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.storage.base import EventStore, StoragePersistenceError

logger = get_logger("telemetry.storage.jsonl")


class JSONLEventStore(EventStore):
    """Event store persisting events to date-based JSONL files in ~/.telemetry_agent/data/."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen_ids: Set[str] = set()
        self._load_recent_seen_ids()

    def _load_recent_seen_ids(self) -> None:
        """Populate recent event IDs into memory to prevent duplicates without file scans."""
        files = self._get_jsonl_files(descending=True)[:3]
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            eid = data.get("event_id")
                            if eid:
                                self._seen_ids.add(eid)
                        except Exception:
                            pass
            except Exception:
                pass

    @staticmethod
    def get_filename_for_timestamp(ts: Optional[str] = None) -> str:
        """Derive telemetry-YYYY-MM-DD.jsonl filename from ISO timestamp or current UTC date."""
        if ts and isinstance(ts, str) and len(ts) >= 10:
            date_candidate = ts[:10]
            parts = date_candidate.split("-")
            if len(parts) == 3 and len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
                if all(p.isdigit() for p in parts):
                    return f"telemetry-{date_candidate}.jsonl"

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"telemetry-{now_utc}.jsonl"

    def get_file_path_for_event(self, event: TelemetryEvent) -> Path:
        """Construct date-based JSONL file path based on event UTC timestamp."""
        filename = self.get_filename_for_timestamp(event.timestamp)
        return self.data_dir / filename

    def _is_id_in_file(self, file_path: Path, event_id: str) -> bool:
        """Scan a specific JSONL file to check if event_id is already present."""
        if not file_path.is_file():
            return False
        needle = f'"{event_id}"'
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    if needle in line:
                        return True
        except Exception:
            pass
        return False

    def save_event(self, event: TelemetryEvent) -> bool:
        """Save a single normalized event. Returns True if newly saved, False if duplicate.

        Raises:
            StoragePersistenceError: If the event fails to persist to disk.
        """
        if not event or not event.event_id:
            return False

        with self._lock:
            if event.event_id in self._seen_ids:
                logger.warning("duplicate_event_ignored", event_id=event.event_id)
                return False

            file_path = self.get_file_path_for_event(event)
            if self._is_id_in_file(file_path, event.event_id):
                self._seen_ids.add(event.event_id)
                logger.warning("duplicate_event_on_disk_ignored", event_id=event.event_id)
                return False

            try:
                event_dict = event.to_dict()
                json_str = json.dumps(event_dict, ensure_ascii=False)
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json_str + "\n")
                    f.flush()

                self._seen_ids.add(event.event_id)
                return True
            except (OSError, IOError) as e:
                logger.error("event_save_error", event_id=event.event_id, error=str(e))
                raise StoragePersistenceError(
                    f"Failed to persist event {event.event_id} to {file_path.name}: {e}"
                ) from e
            except Exception as e:
                logger.error("event_serialization_error", event_id=event.event_id, error=str(e))
                raise StoragePersistenceError(
                    f"Failed to serialize/persist event {event.event_id}: {e}"
                ) from e

    def append_event(self, event: TelemetryEvent) -> bool:
        """Convenience alias for save_event."""
        return self.save_event(event)

    def _get_jsonl_files(self, descending: bool = True) -> List[Path]:
        """Return all telemetry-YYYY-MM-DD.jsonl files sorted by date."""
        if not self.data_dir.exists():
            return []
        pattern = re.compile(r"^telemetry-\d{4}-\d{2}-\d{2}\.jsonl$")
        files = [p for p in self.data_dir.iterdir() if p.is_file() and pattern.match(p.name)]
        files.sort(key=lambda p: p.name, reverse=descending)
        return files

    def _matches_filters(
        self,
        event: TelemetryEvent,
        event_type: Optional[str] = None,
        member_id: Optional[str] = None,
        repository_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        if event_type and event.event_type != event_type:
            return False
        if member_id and event.identity.member_id != member_id:
            return False
        if session_id:
            sess = event.context.session_id if event.context else None
            if sess != session_id:
                return False
        if repository_name:
            repo = event.context.repository if event.context else None
            rname = repo.name if repo else None
            if rname != repository_name:
                return False
        return True

    def get_event_by_id(self, event_id: str) -> Optional[TelemetryEvent]:
        """Retrieve an event by its unique ID."""
        files = self._get_jsonl_files(descending=True)
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        if f'"{event_id}"' not in line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("event_id") == event_id:
                                return TelemetryEvent.from_dict(data)
                        except Exception:
                            continue
            except Exception:
                continue
        return None

    def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        event_type: Optional[str] = None,
        member_id: Optional[str] = None,
        repository_name: Optional[str] = None,
        session_id: Optional[str] = None,
        descending: bool = True,
    ) -> List[TelemetryEvent]:
        """Query stored events across date files with filtering and pagination."""
        files = self._get_jsonl_files(descending=descending)
        matched_events: List[TelemetryEvent] = []

        for fpath in files:
            file_events: List[TelemetryEvent] = []
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            ev = TelemetryEvent.from_dict(data)
                            if self._matches_filters(
                                ev,
                                event_type=event_type,
                                member_id=member_id,
                                repository_name=repository_name,
                                session_id=session_id,
                            ):
                                file_events.append(ev)
                        except Exception:
                            continue
            except Exception:
                continue

            if descending:
                # Within a daily file lines are chronological, so reverse for descending view
                file_events.reverse()

            matched_events.extend(file_events)
            if len(matched_events) >= offset + limit:
                break

        return matched_events[offset: offset + limit]

    def read_recent_events(self, limit: int = 50) -> List[TelemetryEvent]:
        """Read recent events in descending chronological order."""
        return self.get_events(limit=limit, descending=True)

    def read_events_for_date(self, date_val: Union[str, Any]) -> List[TelemetryEvent]:
        """Read all events recorded on a specific date (YYYY-MM-DD)."""
        date_str = str(date_val)[:10]
        target_file = self.data_dir / f"telemetry-{date_str}.jsonl"
        if not target_file.is_file():
            return []

        events: List[TelemetryEvent] = []
        try:
            with open(target_file, "r", encoding="utf-8", errors="replace") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(TelemetryEvent.from_dict(json.loads(line)))
                    except Exception:
                        pass
        except Exception:
            pass
        return events

    def read_events_between(self, start_date: str, end_date: str) -> List[TelemetryEvent]:
        """Read all events recorded between start_date and end_date (inclusive, YYYY-MM-DD)."""
        s_date = str(start_date)[:10]
        e_date = str(end_date)[:10]
        files = self._get_jsonl_files(descending=False)
        matched: List[TelemetryEvent] = []

        for fpath in files:
            # fpath.name is telemetry-YYYY-MM-DD.jsonl
            f_date = fpath.name.replace("telemetry-", "").replace(".jsonl", "")
            if s_date <= f_date <= e_date:
                matched.extend(self.read_events_for_date(f_date))
    def get_session_events(self, session_id: str) -> List[TelemetryEvent]:
        """Retrieve all events for a specific session in chronological order."""
        if not session_id:
            return []
        files = self._get_jsonl_files(descending=False)
        events: List[TelemetryEvent] = []
        seen_event_ids: Set[str] = set()

        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line or f'"{session_id}"' not in line:
                            continue
                        try:
                            data = json.loads(line)
                            ev_id = data.get("event_id")
                            if ev_id and ev_id in seen_event_ids:
                                continue
                            ev = TelemetryEvent.from_dict(data)
                            sid = ev.context.session_id if ev.context else None
                            if sid == session_id:
                                if ev_id:
                                    seen_event_ids.add(ev_id)
                                events.append(ev)
                        except Exception:
                            continue
            except Exception:
                continue

        return events

    def get_events_by_session(
        self,
        repository_name: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, List[TelemetryEvent]]:
        """Retrieve events grouped by session_id in chronological order.
        
        Performs a single chronological pass over JSONL files, filtering by date and repository,
        and deduplicating events by event_id.
        """
        files = self._get_jsonl_files(descending=False)
        grouped: Dict[str, List[TelemetryEvent]] = {}
        seen_event_ids: Set[str] = set()

        s_date = str(date_from)[:10] if date_from else None
        e_date = str(date_to)[:10] if date_to else None

        for fpath in files:
            f_date = fpath.name.replace("telemetry-", "").replace(".jsonl", "")
            if s_date and f_date < s_date:
                continue
            if e_date and f_date > e_date:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            ev_id = data.get("event_id")
                            if ev_id and ev_id in seen_event_ids:
                                continue
                            ev = TelemetryEvent.from_dict(data)
                            sid = ev.context.session_id if ev.context else None
                            if not sid:
                                continue

                            # Repository filter
                            if repository_name:
                                ev_repo = ev.context.repository if ev.context else None
                                ev_repo_name = ev_repo.name if ev_repo else None
                                if ev_repo_name != repository_name:
                                    continue

                            if ev_id:
                                seen_event_ids.add(ev_id)

                            if sid not in grouped:
                                grouped[sid] = []
                            grouped[sid].append(ev)
                        except Exception:
                            continue
            except Exception:
                continue

        return grouped

    def get_count(self, event_type: Optional[str] = None, repository_name: Optional[str] = None) -> int:
        """Return the total number of events stored across all JSONL files."""
        count = 0
        files = self._get_jsonl_files(descending=False)
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        if not event_type and not repository_name:
                            count += 1
                            continue
                        try:
                            data = json.loads(line)
                            ev = TelemetryEvent.from_dict(data)
                            if self._matches_filters(ev, event_type=event_type, repository_name=repository_name):
                                count += 1
                        except Exception:
                            pass
            except Exception:
                continue
        return count

    def get_sessions(
        self,
        repository_name: Optional[str] = None,
        member_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query and aggregate distinct session records with multi-model breakdown."""
        files = self._get_jsonl_files(descending=False)
        sessions_map: Dict[str, Dict[str, Any]] = {}
        # Per-session set of completion run_ids, kept outside the session dict so
        # it never leaks into serialized session summaries. Used to prevent
        # duplicate completion events from inflating accumulated duration and
        # completed-run counts.
        seen_completion_run_ids: Dict[str, Set[str]] = {}
        seen_tool_call_ids: Dict[str, Set[str]] = {}

        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            ev = TelemetryEvent.from_dict(data)
                        except Exception:
                            continue

                        sid = ev.context.session_id if ev.context else None
                        if not sid:
                            continue

                        if session_id and sid != session_id:
                            continue

                        ev_member = ev.identity.member_id
                        if member_id and ev_member != member_id:
                            continue

                        ev_repo = ev.context.repository if ev.context else None
                        ev_repo_name = ev_repo.name if ev_repo else None
                        if repository_name and ev_repo_name != repository_name:
                            continue

                        ev_branch = ev_repo.branch if ev_repo else None

                        if sid not in sessions_map:
                            sessions_map[sid] = {
                                "session_id": sid,
                                "member_id": ev_member,
                                "device_id": ev.collector.device_id,
                                "repository_name": ev_repo_name,
                                "branch": ev_branch,
                                "models": set(),
                                "model_counts": {},
                                "sqlite_gen_count": 0,
                                "gen_model_counts": {},
                                "completed_runs_count": 0,
                                "completed_model_counts": {},
                                "started_runs_count": 0,
                                "started_model_counts": {},
                                "started_at": ev.timestamp,
                                "last_activity_at": ev.timestamp,
                                "duration_seconds": 0.0,
                                "recorded_turn_duration_seconds": 0.0,
                                "ai_runs_count": 0,
                                "tool_calls_count": 0,
                                "tools_used": set(),
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "cache_read_tokens": 0,
                                "total_tokens": 0,
                                "has_token_data": False,
                                "total_credits": 0.0,
                                "credit_unit": None,
                                "has_credit_data": False,
                            }

                        sess = sessions_map[sid]
                        sess["last_activity_at"] = ev.timestamp

                        if ev_repo_name and not sess["repository_name"]:
                            sess["repository_name"] = ev_repo_name

                        if ev_branch:
                            sess["branch"] = ev_branch

                        # Inspect payload
                        p = ev.payload or {}
                        model = p.get("model")
                        etype = ev.event_type
                        cm = p.get("capture_method")

                        # Deduplicate completion events by run_id so duplicate
                        # ai_run_completed records (e.g. after a cursor reset) cannot
                        # inflate accumulated duration or completed-run counts.
                        # Falls back to event_id when no run_id is present.
                        is_duplicate_completion = False
                        if etype == "ai_run_completed":
                            comp_key = p.get("run_id") or p.get("execution_id") or ev.event_id
                            seen_completions = seen_completion_run_ids.setdefault(sid, set())
                            if comp_key in seen_completions:
                                is_duplicate_completion = True
                            else:
                                seen_completions.add(comp_key)

                        # Preserve authoritative AI operation durations for session
                        # summaries. Applies to Kiro CLI (session_json) and Kiro IDE
                        # (messages_jsonl). For CLI, prefer metadata.turn_duration_ms
                        # then fall back to payload.duration_ms (preserving the
                        # verified CLI fallback). For IDE, use payload.duration_ms
                        # (sourced from elapsedTime) directly.
                        if (
                            etype == "ai_run_completed"
                            and cm in ("session_json", "messages_jsonl")
                            and not is_duplicate_completion
                        ):
                            metadata = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
                            duration_ms = metadata.get("turn_duration_ms")

                            # Fall back to the normalized payload duration when metadata lacks it.
                            if duration_ms is None:
                                duration_ms = p.get("duration_ms")

                            is_valid_duration = False
                            if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
                                try:
                                    is_valid_duration = math.isfinite(duration_ms) and duration_ms >= 0
                                except (TypeError, ValueError, OverflowError):
                                    is_valid_duration = False

                            if is_valid_duration:
                                sess["recorded_turn_duration_seconds"] += duration_ms / 1000.0


                        # Track credits if present. Skip duplicate completions
                        # so a repeated ai_run_completed record cannot double-count
                        # the operation's credits in the session summary.
                        creds = p.get("credits")
                        if (
                            creds is not None
                            and isinstance(creds, (int, float))
                            and not is_duplicate_completion
                        ):
                            sess["has_credit_data"] = True
                            sess["total_credits"] = round(sess["total_credits"] + float(creds), 4)
                            if not sess["credit_unit"]:
                                sess["credit_unit"] = p.get("credit_unit") or "credit"

                        if etype == "ai_run" or cm == "sqlite_gen_metadata":
                            sess["sqlite_gen_count"] += 1
                            if model:
                                sess["models"].add(model)
                                sess["gen_model_counts"][model] = sess["gen_model_counts"].get(model, 0) + 1

                            in_tok = p.get("input_tokens")
                            out_tok = p.get("output_tokens")
                            cache_tok = p.get("cache_read_tokens") if p.get("cache_read_tokens") is not None else p.get("cache_tokens")
                            tot_tok = p.get("total_tokens")

                            has_tok = any(isinstance(v, (int, float)) for v in (in_tok, out_tok, cache_tok, tot_tok))
                            if has_tok:
                                sess["has_token_data"] = True
                                if isinstance(in_tok, (int, float)):
                                    sess["input_tokens"] += int(in_tok)
                                if isinstance(out_tok, (int, float)):
                                    sess["output_tokens"] += int(out_tok)
                                if isinstance(cache_tok, (int, float)):
                                    sess["cache_read_tokens"] += int(cache_tok)
                                if isinstance(tot_tok, (int, float)):
                                    sess["total_tokens"] += int(tot_tok)
                                elif isinstance(in_tok, (int, float)) or isinstance(out_tok, (int, float)):
                                    sess["total_tokens"] += int(in_tok or 0) + int(cache_tok or 0) + int(out_tok or 0)

                        elif etype == "ai_run_completed":
                            if not is_duplicate_completion:
                                sess["completed_runs_count"] += 1
                                if model:
                                    sess["models"].add(model)
                                    sess["completed_model_counts"][model] = sess["completed_model_counts"].get(model, 0) + 1
                                tools_in_payload = p.get("tools")
                                seen_tools = seen_tool_call_ids.setdefault(sid, set())
                                if isinstance(tools_in_payload, list) and tools_in_payload:
                                    for t in tools_in_payload:
                                        if isinstance(t, dict):
                                            tn = t.get("tool_name") or t.get("name")
                                            if tn:
                                                sess["tools_used"].add(tn)
                                            t_id = t.get("tool_use_id") or t.get("tool_call_id") or t.get("id")
                                            if t_id:
                                                if t_id not in seen_tools:
                                                    seen_tools.add(t_id)
                                                    sess["tool_calls_count"] += 1
                                            else:
                                                sess["tool_calls_count"] += 1
                                elif p.get("tool_name"):
                                    tn = p.get("tool_name")
                                    sess["tools_used"].add(tn)
                                    comp_key = p.get("run_id") or p.get("execution_id") or ev.event_id
                                    tool_key = f"{comp_key}:{tn}"
                                    if tool_key not in seen_tools:
                                        seen_tools.add(tool_key)
                                        sess["tool_calls_count"] += 1

                        elif etype == "ai_run_started":
                            sess["started_runs_count"] += 1
                            if model:
                                sess["models"].add(model)
                                sess["started_model_counts"][model] = sess["started_model_counts"].get(model, 0) + 1

                        elif etype in ("ai_tool_call", "tool_call"):
                            tname = p.get("tool_name")
                            if tname:
                                sess["tools_used"].add(tname)
                            if model:
                                sess["models"].add(model)
                            meta = p.get("metadata") or {}
                            t_id = (
                                meta.get("tool_use_id")
                                or meta.get("tool_call_id")
                                or p.get("run_id")
                                or p.get("tool_call_id")
                            )
                            seen_tools = seen_tool_call_ids.setdefault(sid, set())
                            if t_id:
                                if t_id not in seen_tools:
                                    seen_tools.add(t_id)
                                    sess["tool_calls_count"] += 1
                            else:
                                sess["tool_calls_count"] += 1
            except Exception:
                continue

        # Format session summaries
        session_list: List[Dict[str, Any]] = []
        for s in sessions_map.values():
            try:
                t0_str = s["started_at"].replace("Z", "+00:00")
                t1_str = s["last_activity_at"].replace("Z", "+00:00")
                t0 = datetime.fromisoformat(t0_str)
                t1 = datetime.fromisoformat(t1_str)
                activity_span_seconds = max(0.0, (t1 - t0).total_seconds())
            except (AttributeError, TypeError, ValueError):
                activity_span_seconds = 0.0

            recorded_turn_seconds = s.get("recorded_turn_duration_seconds", 0.0)

            s["session_activity_span_seconds"] = round(activity_span_seconds, 2)

            # Duration means accumulated recorded AI operation time,
            # not the elapsed wall-clock span of the session.
            is_valid_recorded = False
            if isinstance(recorded_turn_seconds, (int, float)) and not isinstance(recorded_turn_seconds, bool):
                try:
                    is_valid_recorded = math.isfinite(recorded_turn_seconds) and recorded_turn_seconds > 0
                except (TypeError, ValueError, OverflowError):
                    is_valid_recorded = False

            s["duration_seconds"] = (
                round(recorded_turn_seconds, 2)
                if is_valid_recorded
                else None
            )

            # Determine primary run count per model:
            # 1. If SQLite generation records exist for a model, they represent exact, authoritative model runs.
            # 2. Otherwise for hook runs, take max(completed, started) to avoid start+stop doubling while capturing incomplete runs.
            model_counts: Dict[str, int] = {}
            for m in s["models"]:
                if s["gen_model_counts"].get(m, 0) > 0:
                    model_counts[m] = s["gen_model_counts"][m]
                else:
                    model_counts[m] = max(s["completed_model_counts"].get(m, 0), s["started_model_counts"].get(m, 0))

            s["model_counts"] = model_counts
            s["ai_runs_count"] = sum(model_counts.values()) if model_counts else (s["sqlite_gen_count"] or max(s["completed_runs_count"], s["started_runs_count"]))

            s["models"] = sorted(list(s["models"]))
            s["model"] = ", ".join(s["models"]) if s["models"] else None
            s["tools_used"] = sorted(list(s["tools_used"]))
            session_list.append(s)

        session_list.sort(key=lambda x: x["last_activity_at"], reverse=True)
        return session_list[:limit]

    def get_repository_summary(self, repository_name: Optional[str] = None) -> Dict[str, Any]:
        """Calculate aggregated session, AI run, and tool call counts grouped by repository."""
        sessions = self.get_sessions(repository_name=repository_name, limit=1000)
        summary: Dict[str, Dict[str, Any]] = {}

        for s in sessions:
            rname = s["repository_name"] or "unknown_repository"
            if rname not in summary:
                summary[rname] = {
                    "repository_name": rname,
                    "total_sessions": 0,
                    "total_ai_runs": 0,
                    "total_tool_calls": 0,
                    "total_credits": 0.0,
                    "has_credit_data": False,
                    "branches": set(),
                    "models_used": set(),
                    "model_counts": {},
                    "tools_used": set(),
                    "last_active": s["last_activity_at"],
                }

            rsum = summary[rname]
            rsum["total_sessions"] += 1
            rsum["total_ai_runs"] += s["ai_runs_count"]
            rsum["total_tool_calls"] += s["tool_calls_count"]
            if s.get("has_credit_data"):
                rsum["has_credit_data"] = True
                rsum["total_credits"] = round(rsum["total_credits"] + float(s.get("total_credits", 0.0)), 4)
            if s["branch"]:
                rsum["branches"].add(s["branch"])
            for m in s["models"]:
                rsum["models_used"].add(m)
                rsum["model_counts"][m] = rsum["model_counts"].get(m, 0) + s.get("model_counts", {}).get(m, 1)
            for t in s["tools_used"]:
                rsum["tools_used"].add(t)
            if s["last_activity_at"] > rsum["last_active"]:
                rsum["last_active"] = s["last_activity_at"]

        # Convert sets to sorted lists
        for rsum in summary.values():
            rsum["branches"] = sorted(list(rsum["branches"]))
            rsum["models_used"] = sorted(list(rsum["models_used"]))
            rsum["tools_used"] = sorted(list(rsum["tools_used"]))

        return summary

    def close(self) -> None:
        """Cleanly close storage resources."""
        pass
