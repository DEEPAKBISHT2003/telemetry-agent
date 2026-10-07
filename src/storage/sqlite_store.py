"""SQLite storage implementation for telemetry events and session aggregations."""

import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from src.core.event import CollectorInfo, ContextInfo, IdentityInfo, RepositoryInfo, TelemetryEvent
from src.logging.structured import get_logger
from src.storage.base import EventStore

logger = get_logger("telemetry.storage")


class SQLiteEventStore(EventStore):
    """Local SQLite-backed persistent store with indexed queries and thread safety."""

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
                # Create events table with repository columns
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        member_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        session_id TEXT,
                        project_id TEXT,
                        jira_task_id TEXT,
                        repository_name TEXT,
                        branch TEXT,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)

                # Check for existing table schema migrations (backward compatibility)
                cursor.execute("PRAGMA table_info(events)")
                cols = {row["name"] for row in cursor.fetchall()}
                if "repository_name" not in cols:
                    cursor.execute("ALTER TABLE events ADD COLUMN repository_name TEXT")
                if "branch" not in cols:
                    cursor.execute("ALTER TABLE events ADD COLUMN branch TEXT")

                # Create indexes
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_event_id ON events (event_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events (timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_event_type ON events (event_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_member_id ON events (member_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_device_id ON events (device_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session_id ON events (session_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_repo_name ON events (repository_name)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_branch ON events (branch)")
                conn.commit()

    def save_event(self, event: TelemetryEvent) -> bool:
        """Insert a new event into SQLite. Returns True if inserted, False if duplicate or failed."""
        created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload_json = json.dumps(event.payload, ensure_ascii=False)

        # Extract repository name and branch
        repo_name = None
        branch = None
        if event.context.repository:
            if isinstance(event.context.repository, RepositoryInfo):
                repo_name = event.context.repository.name
                branch = event.context.repository.branch
            elif isinstance(event.context.repository, dict):
                repo_name = event.context.repository.get("name")
                branch = event.context.repository.get("branch")

        # Fallback to payload if not in context
        if not repo_name and isinstance(event.payload, dict):
            repo_name = event.payload.get("repository") or event.payload.get("repository_name")
            branch = event.payload.get("branch") or event.payload.get("current_branch") or branch

        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO events (
                            event_id, event_type, timestamp, member_id, device_id,
                            session_id, project_id, jira_task_id, repository_name, branch, payload, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        event.event_id,
                        event.event_type,
                        event.timestamp,
                        event.identity.member_id,
                        event.collector.device_id,
                        event.context.session_id,
                        event.context.project_id,
                        event.context.jira_task_id,
                        repo_name,
                        branch,
                        payload_json,
                        created_at,
                    ))
                    conn.commit()
                    return True
            except sqlite3.IntegrityError:
                logger.warning("duplicate_event_ignored", event_id=event.event_id)
                return False
            except Exception as ex:
                logger.error("storage_save_error", event_id=event.event_id, error=str(ex))
                return False

    def _row_to_event(self, row: sqlite3.Row) -> TelemetryEvent:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            payload = {}

        repo_name = row["repository_name"] if "repository_name" in row.keys() else None
        branch = row["branch"] if "branch" in row.keys() else None

        repo_info = None
        if repo_name:
            repo_info = RepositoryInfo(
                name=repo_name,
                branch=branch,
                is_git_repo=True,
            )

        return TelemetryEvent(
            event_id=row["event_id"],
            event_type=row["event_type"],
            timestamp=row["timestamp"],
            collector=CollectorInfo(
                version="0.1.0",
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
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query and aggregate distinct session records with repository metrics."""
        query = "SELECT * FROM events WHERE session_id IS NOT NULL AND session_id != ''"
        params: List[Any] = []

        if repository_name:
            query += " AND repository_name = ?"
            params.append(repository_name)

        if member_id:
            query += " AND member_id = ?"
            params.append(member_id)

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
                            "model": None,
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

                    if p.get("model") and not sess["model"]:
                        sess["model"] = p.get("model")

                    etype = row["event_type"]
                    if etype in ("ai_run_started", "ai_run", "ai_run_completed"):
                        sess["ai_runs_count"] += 1
                    elif etype in ("ai_tool_call", "tool_call"):
                        sess["tool_calls_count"] += 1
                        tname = p.get("tool_name")
                        if tname:
                            sess["tools_used"].add(tname)

        # Calculate durations and convert tools set to sorted list
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
                    "tools_used": set(),
                    "last_active": s["last_activity_at"],
                }

            rsum = summary[rname]
            rsum["total_sessions"] += 1
            rsum["total_ai_runs"] += s["ai_runs_count"]
            rsum["total_tool_calls"] += s["tool_calls_count"]
            if s["branch"]:
                rsum["branches"].add(s["branch"])
            if s["model"]:
                rsum["models_used"].add(s["model"])
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
