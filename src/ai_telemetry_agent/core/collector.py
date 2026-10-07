"""Core Telemetry Collector engine managing lifecycle, sources, queueing, and persistence."""

import datetime
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import List, Optional

from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.event import ContextInfo, EventType, TelemetryEvent
from ai_telemetry_agent.core.identity import LocalIdentityProvider
from ai_telemetry_agent.core.processor import EventProcessor
from ai_telemetry_agent.logging.structured import get_logger, setup_structured_logging
from ai_telemetry_agent.sources.antigravity import AntigravitySource
from ai_telemetry_agent.sources.base import EventSource
from ai_telemetry_agent.sources.git import GitSource
from ai_telemetry_agent.sources.system import SystemSource

logger = get_logger("telemetry.collector")


class TelemetryCollector:
    """Main Telemetry Collector instance running on developer's laptop."""

    def __init__(self, settings: Optional[Settings] = None):
        from ai_telemetry_agent.storage.queue import SQLiteEventQueue
        from ai_telemetry_agent.storage.sqlite_store import SQLiteEventStore

        self.settings = settings or load_settings()
        setup_structured_logging(self.settings.log_level)

        self.identity_provider = LocalIdentityProvider(self.settings)
        self.processor = EventProcessor(
            identity_provider=self.identity_provider,
            collector_version=self.settings.collector_version,
        )

        self.store = SQLiteEventStore(str(self.settings.db_path))
        self.queue = SQLiteEventQueue(str(self.settings.db_path))

        # Initialize event sources
        self.system_source = SystemSource(
            collector_version=self.settings.collector_version,
            heartbeat_interval_seconds=self.settings.heartbeat_interval_seconds,
            member_id=self.identity_provider.get_current_member(),
            device_id=self.identity_provider.get_device_id(),
        )

        self.git_source = GitSource(
            watch_paths=self.settings.git_monitor_paths,
            poll_interval_seconds=self.settings.git_monitor_interval_seconds,
        )

        self.antigravity_source = AntigravitySource(
            enabled=self.settings.antigravity_monitor_enabled
        )

        self.sources: List[EventSource] = [
            self.system_source,
            self.git_source,
            self.antigravity_source,
        ]

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._pid_file = Path(self.settings.local_database_path).parent / "collector.pid"

    def _write_pid_file(self) -> None:
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._pid_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except Exception as e:
            logger.warning("pid_file_write_failed", error=str(e))

    def _remove_pid_file(self) -> None:
        try:
            if self._pid_file.exists():
                self._pid_file.unlink()
        except Exception:
            pass

    def start(self) -> None:
        """Start collector engine, initialize sources, and start background workers."""
        if self._running:
            return

        self._running = True
        self._write_pid_file()

        member_id = self.identity_provider.get_current_member()
        device_id = self.identity_provider.get_device_id()

        logger.info(
            "collector_starting",
            version=self.settings.collector_version,
            member_id=member_id,
            device_id=device_id,
            database=str(self.settings.db_path),
        )

        # Start all event sources
        for src in self.sources:
            try:
                src.start()
            except Exception as ex:
                logger.error("source_start_error", source=src.name, error=str(ex))

        # Initial poll to register startup and detection events
        self.poll_and_process()

        logger.info(
            "collector_started",
            member_id=member_id,
            device_id=device_id,
            version=self.settings.collector_version,
            status="RUNNING",
        )

    def stop(self) -> None:
        """Gracefully stop collector, flush pending events, and close resources."""
        if not self._running:
            return

        logger.info(
            "collector_stopping",
            member_id=self.identity_provider.get_current_member(),
            device_id=self.identity_provider.get_device_id(),
        )

        # Stop sources (this produces collector_stopped event in system source)
        for src in self.sources:
            try:
                src.stop()
            except Exception as ex:
                logger.error("source_stop_error", source=src.name, error=str(ex))

        # Process remaining shutdown events
        self.poll_and_process()

        # Flush queue to persistent store
        self.flush_queue()

        self._running = False
        self._remove_pid_file()

        logger.info(
            "collector_stopped",
            member_id=self.identity_provider.get_current_member(),
            device_id=self.identity_provider.get_device_id(),
            status="STOPPED",
        )

    def poll_and_process(self) -> int:
        """Poll all active sources, process events, enqueue, and persist them.

        Returns:
            Number of new events processed.
        """
        collected_count = 0
        for src in self.sources:
            try:
                raw_events = src.collect()
                for raw in raw_events:
                    event = self.processor.process_raw(raw)
                    if event:
                        # 1. Enqueue
                        self.queue.enqueue(event)
                        # 2. Log structured event
                        self._log_event(event, src.name)
                        collected_count += 1
            except Exception as ex:
                logger.error("source_collect_error", source=src.name, error=str(ex))

        # Process queue items into persistent storage
        self.process_queue_batch(batch_size=50)
        return collected_count

    def process_queue_batch(self, batch_size: int = 50) -> int:
        """Dequeue pending items and write them to SQLite event store."""
        items = self.queue.dequeue(batch_size=batch_size)
        processed = 0
        for item in items:
            try:
                saved = self.store.save_event(item.event)
                if saved:
                    self.queue.mark_processed(item.queue_id)
                    processed += 1
                else:
                    self.queue.retry(item.queue_id, "Duplicate or save error")
            except Exception as ex:
                self.queue.retry(item.queue_id, str(ex))
                logger.error("queue_process_error", queue_id=item.queue_id, error=str(ex))
        return processed

    def flush_queue(self) -> None:
        """Flush all pending queue items into store."""
        while True:
            processed = self.process_queue_batch(batch_size=100)
            if processed == 0:
                break

    def _log_event(self, event: TelemetryEvent, source_name: str) -> None:
        """Emit structured log for each collected event."""
        fields = {
            "member_id": event.identity.member_id,
            "device_id": event.collector.device_id,
            "source": source_name,
        }
        # Add key payload fields if present
        if event.payload:
            for k in ["repository", "branch", "commit_sha", "status", "uptime_seconds", "provider", "model"]:
                if k in event.payload and event.payload[k] is not None:
                    fields[k] = event.payload[k]

        logger.info(event.event_type, **fields)

    def run_loop(self, poll_interval_seconds: float = 1.0) -> None:
        """Run blocking event collection loop until interrupted."""
        self.start()

        def _handle_signal(signum, frame):
            logger.info("shutdown_signal_received", signal=signum)
            self.stop()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except Exception:
            pass

        try:
            while self._running:
                self.poll_and_process()
                time.sleep(poll_interval_seconds)
        except KeyboardInterrupt:
            self.stop()
