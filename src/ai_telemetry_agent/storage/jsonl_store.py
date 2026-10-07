"""Date-based JSONL telemetry storage module.

Stores events in ~/.telemetry_agent/data/telemetry-YYYY-MM-DD.jsonl
using UTC dates, append-only JSON lines, and thread/process-safe writes.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Set, Union

from ai_telemetry_agent.config.settings import get_user_agent_home
from ai_telemetry_agent.core.event import TelemetryEvent
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.storage.base import EventStore

logger = get_logger("telemetry.storage.jsonl")


class JSONLEventStore(EventStore):
    """Event store persisting events to date-based JSONL files in ~/.telemetry_agent/data/."""

    def __init__(self, data_dir: Optional[Union[str, Path]] = None):
        if data_dir is None:
            user_home = get_user_agent_home()
            self.data_dir = (user_home / "data").resolve()
        else:
            self.data_dir = Path(data_dir).resolve()

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

    def save_event(self, event: TelemetryEvent) -> bool:
        """Save a single normalized event. Returns True if saved, False if duplicate/error."""
        if not event or not event.event_id:
            return False

        with self._lock:
            if event.event_id in self._seen_ids:
                logger.warning("duplicate_event_ignored", event_id=event.event_id)
                return False

            file_path = self.get_file_path_for_event(event)
            try:
                event_dict = event.to_dict()
                json_str = json.dumps(event_dict, ensure_ascii=False)
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(json_str + "\n")
                    f.flush()

                self._seen_ids.add(event.event_id)
                return True
            except Exception as e:
                logger.error("event_save_error", event_id=event.event_id, error=str(e))
                return False

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
        return matched

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
                                "started_at": ev.timestamp,
                                "last_activity_at": ev.timestamp,
                                "duration_seconds": 0.0,
                                "ai_runs_count": 0,
                                "tool_calls_count": 0,
                                "tools_used": set(),
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

                        if etype in ("ai_run_started", "ai_run", "ai_run_completed"):
                            sess["ai_runs_count"] += 1
                            if model:
                                sess["models"].add(model)
                                sess["model_counts"][model] = sess["model_counts"].get(model, 0) + 1
                        elif etype in ("ai_tool_call", "tool_call"):
                            sess["tool_calls_count"] += 1
                            tname = p.get("tool_name")
                            if tname:
                                sess["tools_used"].add(tname)
                            if model:
                                sess["models"].add(model)
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
                s["duration_seconds"] = round((t1 - t0).total_seconds(), 2)
            except Exception:
                s["duration_seconds"] = 0.0

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
