"""Local Queue Abstraction for Telemetry Events."""

from abc import ABC, abstractmethod
import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
from ai_telemetry_agent.core.event import TelemetryEvent
from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.queue")


class QueueItem:
    """Represents an item in the telemetry queue."""

    def __init__(
        self,
        queue_id: int,
        event: TelemetryEvent,
        status: str,
        attempts: int,
        created_at: str,
        updated_at: str
    ):
        self.queue_id = queue_id
        self.event = event
        self.status = status
        self.attempts = attempts
        self.created_at = created_at
        self.updated_at = updated_at


class EventQueue(ABC):
    """Abstract interface for the local telemetry event queue."""

    @abstractmethod
    def enqueue(self, event: TelemetryEvent) -> int:
        """Add an event to the queue. Returns the queue item id."""
        pass

    @abstractmethod
    def dequeue(self, batch_size: int = 1) -> List[QueueItem]:
        """Fetch pending items from the queue and set their status to PROCESSING."""
        pass

    @abstractmethod
    def mark_processed(self, queue_id: int) -> bool:
        """Mark a queue item as successfully processed."""
        pass

    @abstractmethod
    def retry(self, queue_id: int, error_message: Optional[str] = None) -> bool:
        """Increment attempt count and mark item back to PENDING for retry."""
        pass

    @abstractmethod
    def size(self, status: Optional[str] = None) -> int:
        """Return the number of items in the queue."""
        pass


class SQLiteEventQueue(EventQueue):
    """Persistent SQLite-backed queue for reliable event buffering."""

    STATUS_PENDING = "PENDING"
    STATUS_PROCESSING = "PROCESSING"
    STATUS_PROCESSED = "PROCESSED"
    STATUS_FAILED = "FAILED"

    def __init__(self, db_path: str = "./data/telemetry.db", max_retries: int = 5):
        self.db_file = Path(db_path).resolve()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
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
                    CREATE TABLE IF NOT EXISTS event_queue (
                        queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL,
                        event_data TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON event_queue (status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_event_id ON event_queue (event_id)")
                conn.commit()

    def enqueue(self, event: TelemetryEvent) -> int:
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        event_json = event.to_json()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO event_queue (
                        event_id, event_data, status, attempts, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)
                """, (
                    event.event_id,
                    event_json,
                    self.STATUS_PENDING,
                    now_str,
                    now_str
                ))
                conn.commit()
                return cursor.lastrowid or 0

    def dequeue(self, batch_size: int = 1) -> List[QueueItem]:
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: List[QueueItem] = []

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM event_queue
                    WHERE status = ?
                    ORDER BY queue_id ASC
                    LIMIT ?
                """, (self.STATUS_PENDING, batch_size))
                rows = cursor.fetchall()

                if not rows:
                    return []

                ids = [r["queue_id"] for r in rows]
                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"""
                    UPDATE event_queue
                    SET status = ?, updated_at = ?
                    WHERE queue_id IN ({placeholders})
                """, [self.STATUS_PROCESSING, now_str] + ids)
                conn.commit()

                for r in rows:
                    try:
                        event_dict = json.loads(r["event_data"])
                        event = TelemetryEvent.from_dict(event_dict)
                        items.append(QueueItem(
                            queue_id=r["queue_id"],
                            event=event,
                            status=self.STATUS_PROCESSING,
                            attempts=r["attempts"],
                            created_at=r["created_at"],
                            updated_at=now_str
                        ))
                    except Exception as e:
                        logger.error("dequeue_parse_error", queue_id=r["queue_id"], error=str(e))
        return items

    def mark_processed(self, queue_id: int) -> bool:
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE event_queue
                    SET status = ?, updated_at = ?
                    WHERE queue_id = ?
                """, (self.STATUS_PROCESSED, now_str, queue_id))
                conn.commit()
                return cursor.rowcount > 0

    def retry(self, queue_id: int, error_message: Optional[str] = None) -> bool:
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT attempts FROM event_queue WHERE queue_id = ?", (queue_id,))
                row = cursor.fetchone()
                if not row:
                    return False

                current_attempts = row["attempts"] + 1
                new_status = self.STATUS_FAILED if current_attempts >= self.max_retries else self.STATUS_PENDING

                cursor.execute("""
                    UPDATE event_queue
                    SET status = ?, attempts = ?, last_error = ?, updated_at = ?
                    WHERE queue_id = ?
                """, (new_status, current_attempts, error_message or "", now_str, queue_id))
                conn.commit()
                return True

    def size(self, status: Optional[str] = None) -> int:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if status:
                    cursor.execute("SELECT COUNT(*) FROM event_queue WHERE status = ?", (status,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM event_queue")
                row = cursor.fetchone()
                return int(row[0]) if row else 0
