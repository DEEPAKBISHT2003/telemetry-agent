"""SQLite persistent storage implementation for telemetry events."""

import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import CollectorInfo, ContextInfo, IdentityInfo, RepositoryInfo, TelemetryEvent
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.storage.base import EventStore

logger = get_logger("telemetry.storage")


class SQLiteEventStore(EventStore):
    """Local SQLite database store for validated telemetry events."""

    def __init__(self, db_path: str = "./data/telemetry.db"):
        self.db_file = Path(db_path).resolve()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_file), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        collector_version TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        member_id TEXT NOT NULL,
                        session_id TEXT,
                        project_id TEXT,
                        jira_task_id TEXT,
                        repository_name TEXT,
                        branch TEXT,
                        repository_root TEXT,
                        remote_url TEXT,
                        is_git_repo INTEGER DEFAULT 1,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_member_id ON events (member_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_repo_name ON events (repository_name)")
                conn.commit()

    def save_event(self, event: TelemetryEvent) -> bool:
        """Persist a single validated telemetry event. Idempotent against duplicate event_ids."""
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        repo = event.context.repository if event.context else None
        repo_name = repo.name if repo else None
        branch = repo.branch if repo else None
        repo_root = repo.root if repo else None
        remote_url = repo.remote_url if repo else None
        is_git_repo = 1 if (repo and repo.is_git_repo) else 0

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT INTO events (
                            event_id, event_type, timestamp, collector_version,
                            device_id, member_id, session_id, project_id,
                            jira_task_id, repository_name, branch, repository_root,
                            remote_url, is_git_repo, payload, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.event_id,
                        event.event_type,
                        event.timestamp,
                        event.collector.version,
                        event.collector.device_id,
                        event.identity.member_id,
                        event.context.session_id if event.context else None,
                        event.context.project_id if event.context else None,
                        event.context.jira_task_id if event.context else None,
                        repo_name,
                        branch,
                        repo_root,
                        remote_url,
                        is_git_repo,
                        json.dumps(event.payload, ensure_ascii=False),
                        now_str,
                    ))
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    # Duplicate event_id detected
                    logger.warning("duplicate_event_ignored", event_id=event.event_id)
                    return False
                except Exception as e:
                    logger.error("event_save_error", event_id=event.event_id, error=str(e))
                    return False

    def _row_to_event(self, row: sqlite3.Row) -> TelemetryEvent:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            payload = {}

        repo_info = None
        if row["repository_name"] or row["branch"] or row["repository_root"] or row["remote_url"]:
            repo_info = RepositoryInfo(
                name=row["repository_name"],
                branch=row["branch"],
                root=row["repository_root"],
                remote_url=row["remote_url"],
                is_git_repo=bool(row["is_git_repo"]),
            )

        return TelemetryEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            timestamp=row["timestamp"],
            collector=CollectorInfo(
                version=row["collector_version"],
                device_id=row["device_id"],
            ),
            identity=IdentityInfo(
                member_id=row["member_id"],
            ),
            context=ContextInfo(
                session_id=row["session_id"],
                project_id=row["project_id"],
                jira_task_id=row["jira_task_id"],
                repository=repo_info,
            ),
            payload=payload,
        )

    def get_event_by_id(self, event_id: str) -> Optional[TelemetryEvent]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
                row = cursor.fetchone()
                if row:
                    return self._row_to_event(row)
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
        query = "SELECT * FROM events"
        params: List[Any] = []
        conditions: List[str] = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        if member_id:
            conditions.append("member_id = ?")
            params.append(member_id)

        if repository_name:
            conditions.append("repository_name = ?")
            params.append(repository_name)

        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        direction = "DESC" if descending else "ASC"
        query += f" ORDER BY timestamp {direction}, rowid {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        events: List[TelemetryEvent] = []
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                for row in rows:
                    events.append(self._row_to_event(row))
        return events

    def get_count(self, event_type: Optional[str] = None, repository_name: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) FROM events"
        params: List[Any] = []
        conditions: List[str] = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        if repository_name:
            conditions.append("repository_name = ?")
            params.append(repository_name)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                res = cursor.fetchone()
                return int(res[0]) if res else 0

    def get_sessions(
        self,
        repository_name: Optional[str] = None,
        member_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query and aggregate distinct session records with multi-model breakdown."""
        query = "SELECT * FROM events WHERE session_id IS NOT NULL AND session_id != ''"
        params: List[Any] = []

        if repository_name:
            query += " AND repository_name = ?"
            params.append(repository_name)

        if member_id:
            query += " AND member_id = ?"
            params.append(member_id)

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        query += " ORDER BY timestamp ASC"

        sessions_map: Dict[str, Dict[str, Any]] = {}

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()

                for row in rows:
                    sid = row["session_id"]
                    if sid not in sessions_map:
                        sessions_map[sid] = {
                            "session_id": sid,
                            "member_id": row["member_id"],
                            "device_id": row["device_id"],
                            "repository_name": row["repository_name"],
                            "branch": row["branch"],
                            "models": set(),
                            "model_counts": {},
                            "started_at": row["timestamp"],
                            "last_activity_at": row["timestamp"],
                            "duration_seconds": 0.0,
                            "ai_runs_count": 0,
                            "tool_calls_count": 0,
                            "tools_used": set(),
                        }

                    sess = sessions_map[sid]
                    sess["last_activity_at"] = row["timestamp"]

                    if row["repository_name"] and not sess["repository_name"]:
                        sess["repository_name"] = row["repository_name"]

                    if row["branch"]:
                        sess["branch"] = row["branch"]

                    # Inspect payload for model and tool info
                    try:
                        p = json.loads(row["payload"])
                    except Exception:
                        p = {}

                    model = p.get("model")
                    etype = row["event_type"]

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

        # Calculate durations and convert tools/models sets to sorted lists
        session_list: List[Dict[str, Any]] = []
        for s in sessions_map.values():
            try:
                t0_str = s["started_at"].replace("Z", "+00:00")
                t1_str = s["last_activity_at"].replace("Z", "+00:00")
                t0 = datetime.datetime.fromisoformat(t0_str)
                t1 = datetime.datetime.fromisoformat(t1_str)
                s["duration_seconds"] = round((t1 - t0).total_seconds(), 2)
            except Exception:
                s["duration_seconds"] = 0.0

            s["models"] = sorted(list(s["models"]))
            s["model"] = ", ".join(s["models"]) if s["models"] else None
            s["tools_used"] = sorted(list(s["tools_used"]))
            session_list.append(s)

        # Sort descending by last activity
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
            for m in s.get("models", []):
                rsum["models_used"].add(m)
            for m, count in s.get("model_counts", {}).items():
                rsum["model_counts"][m] = rsum["model_counts"].get(m, 0) + count
            for t in s["tools_used"]:
                rsum["tools_used"].add(t)

            if s["last_activity_at"] > rsum["last_active"]:
                rsum["last_active"] = s["last_activity_at"]

        # Convert sets to sorted lists for clean JSON/table rendering
        for rsum in summary.values():
            rsum["branches"] = sorted(list(rsum["branches"]))
            rsum["models_used"] = sorted(list(rsum["models_used"]))
            rsum["tools_used"] = sorted(list(rsum["tools_used"]))

        return summary

    def close(self) -> None:
        pass
