"""System event source for lifecycle and heartbeat telemetry."""

import datetime
import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional

from ai_telemetry_agent.core.event import EventType
from ai_telemetry_agent.sources.base import EventSource


class SystemSource(EventSource):
    """Generates lifecycle, developer detection, and periodic heartbeat telemetry."""

    def __init__(
        self,
        collector_version: str = "0.1.1",
        heartbeat_interval_seconds: int = 30,
        member_id: str = "UNKNOWN",
        device_id: str = "UNKNOWN",
    ):
        self.collector_version = collector_version
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.member_id = member_id
        self.device_id = device_id
        self._start_time = time.time()
        self._last_heartbeat = 0.0
        self._started = False
        self._initial_events_emitted = False
        self._queue: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "system"

    def start(self) -> None:
        self._started = True
        self._start_time = time.time()
        self._last_heartbeat = time.time()

        # Emit startup event
        self._queue.append({
            "event_type": EventType.COLLECTOR_STARTED,
            "payload": {
                "collector_version": self.collector_version,
                "os": platform.system(),
                "os_release": platform.release(),
                "python_version": sys.version.split()[0],
                "pid": os.getpid(),
                "status": "RUNNING",
            }
        })

        # Emit developer detection event
        self._queue.append({
            "event_type": EventType.DEVELOPER_DETECTED,
            "payload": {
                "member_id": self.member_id,
                "device_id": self.device_id,
                "hostname": platform.node(),
            }
        })

    def stop(self) -> None:
        if self._started:
            uptime_seconds = round(time.time() - self._start_time, 2)
            self._queue.append({
                "event_type": EventType.COLLECTOR_STOPPED,
                "payload": {
                    "collector_version": self.collector_version,
                    "uptime_seconds": uptime_seconds,
                    "status": "STOPPED",
                }
            })
            self._started = False

    def emit_error(self, error_type: str, message: str) -> None:
        """Manually trigger an error event."""
        self._queue.append({
            "event_type": EventType.COLLECTOR_ERROR,
            "payload": {
                "error_type": error_type,
                "message": message,
            }
        })

    def collect(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        # Flush queued lifecycle events
        if self._queue:
            events.extend(self._queue)
            self._queue.clear()

        # Check if heartbeat interval elapsed
        if self._started:
            now = time.time()
            if (now - self._last_heartbeat) >= self.heartbeat_interval_seconds:
                self._last_heartbeat = now
                uptime = round(now - self._start_time, 2)
                events.append({
                    "event_type": EventType.COLLECTOR_HEARTBEAT,
                    "payload": {
                        "collector_version": self.collector_version,
                        "uptime_seconds": uptime,
                        "process_status": "RUNNING",
                        "pid": os.getpid(),
                    }
                })

        return events
