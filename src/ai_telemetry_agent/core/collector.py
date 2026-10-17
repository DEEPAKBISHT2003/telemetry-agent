"""Core Telemetry Collector engine managing lifecycle, sources, and JSONL persistence."""

import datetime
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import List, Optional, Tuple

from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.event import ContextInfo, EventType, TelemetryEvent
from ai_telemetry_agent.core.identity import LocalIdentityProvider
from ai_telemetry_agent.core.processor import EventProcessor
from ai_telemetry_agent.logging.structured import get_logger, setup_structured_logging
from ai_telemetry_agent.sources.antigravity import AntigravitySource
from ai_telemetry_agent.sources.base import EventSource
from ai_telemetry_agent.sources.git import GitSource
from ai_telemetry_agent.sources.kiro import KiroSource
from ai_telemetry_agent.sources.kiro_cli_sessions import KiroCLISessionSource
from ai_telemetry_agent.sources.kiro_ide_sessions import KiroIDESessionSource
from ai_telemetry_agent.sources.system import SystemSource
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.storage.base import StoragePersistenceError
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore

logger = get_logger("telemetry.collector")


def check_process_liveness(pid: int) -> bool:
    """Verify whether a process with the given PID is actively running and belongs to python/telemetry."""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False

        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                if exit_code.value != STILL_ACTIVE:
                    return False
                try:
                    buf_size = 1024
                    buf = ctypes.create_unicode_buffer(buf_size)
                    size = wintypes.DWORD(buf_size)
                    if hasattr(kernel32, "QueryFullProcessImageNameW") and kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        exe_name = Path(buf.value).name.lower()
                        valid_names = ("python", "py", "telemetry")
                        if not any(k in exe_name for k in valid_names):
                            return False
                except Exception:
                    pass
                return True
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            proc_cmdline = Path(f"/proc/{pid}/cmdline")
            if proc_cmdline.exists():
                try:
                    cmd_text = proc_cmdline.read_text(errors="ignore").lower()
                    if not any(k in cmd_text for k in ("python", "telemetry")):
                        return False
                except Exception:
                    pass
            return True
        except (ProcessLookupError, OSError):
            return False


def get_running_process_cmdline(pid: int) -> str:
    """Retrieve the command line of a running process on Windows or POSIX."""
    if pid <= 0:
        return ""
    if sys.platform == "win32":
        try:
            import subprocess
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", f"(Get-CimInstance Win32_Process | Where-Object ProcessId -eq {pid}).CommandLine"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            proc_cmdline = Path(f"/proc/{pid}/cmdline")
            if proc_cmdline.exists():
                return proc_cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
        except Exception:
            pass
    return ""


def extract_telemetry_source_from_cmdline(cmdline: str) -> Optional[str]:
    """Parse --source or --telemetry-source from process command line string if present."""
    if not cmdline:
        return None
    import re
    m = re.search(r"--(?:source|telemetry-source)(?:=|\s+)([\w_]+)", cmdline)
    if m:
        val = m.group(1).strip()
        if val in ("antigravity", "kiro_cli", "kiro_ide", "auto"):
            return val
    return None


def read_collector_metadata(data_dir: Path) -> dict:
    """Read collector metadata written by the running collector instance."""
    meta_file = data_dir / "collector_meta.json"
    if not meta_file.exists():
        local_meta = Path("./data/collector_meta.json").resolve()
        if local_meta.exists():
            meta_file = local_meta
        else:
            return {}

    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_collector_status(data_dir: Path, cleanup_stale: bool = True) -> Tuple[bool, Optional[int]]:
    """Determine whether the collector daemon is running for the given data directory.

    If a PID file exists but the process is not alive or does not belong to the collector,
    the PID file is treated as stale. If cleanup_stale is True, the stale file is safely removed.

    Returns:
        (is_running, active_pid)
    """
    pid_file = data_dir / "collector.pid"
    meta_file = data_dir / "collector_meta.json"
    if not pid_file.exists():
        local_pid_file = Path("./data/collector.pid").resolve()
        if local_pid_file.exists():
            pid_file = local_pid_file
            meta_file = Path("./data/collector_meta.json").resolve()
        else:
            return (False, None)

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        if cleanup_stale:
            try:
                pid_file.unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
            except Exception:
                pass
        return (False, None)

    if check_process_liveness(pid):
        return (True, pid)

    # Stale PID file: process is dead or does not belong to collector
    if cleanup_stale:
        try:
            pid_file.unlink(missing_ok=True)
            meta_file.unlink(missing_ok=True)
        except Exception:
            pass
    return (False, None)


class TelemetryCollector:
    """Main Telemetry Collector instance running on developer's laptop."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or load_settings()
        setup_structured_logging(self.settings.log_level)

        self.identity_provider = LocalIdentityProvider(self.settings)
        self.attribution_store = AttributionStore(self.settings.data_dir)
        self.processor = EventProcessor(
            identity_provider=self.identity_provider,
            collector_version=self.settings.collector_version,
            attribution_store=self.attribution_store,
        )

        self.store = JSONLEventStore(self.settings.data_dir)

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

        # Configure AI event source based on telemetry_source setting
        source_mode = getattr(self.settings, "telemetry_source", "antigravity")
        ai_sources: List[EventSource] = []

        # Antigravity source initialization
        ag_state_file = self.settings.data_dir.parent / "state" / "antigravity_sqlite_state.json"
        self.antigravity_source = AntigravitySource(
            enabled=(self.settings.antigravity_monitor_enabled and source_mode in ("antigravity", "auto")),
            state_file=ag_state_file,
        )

        # Kiro source initialization
        if getattr(self.settings, "kiro_sessions_dir", None):
            base = Path(self.settings.kiro_sessions_dir)
            cli_dir = base if base.name.lower() == "cli" else (base / "cli")
            ide_dir = base.parent if base.name.lower() == "cli" else base
        else:
            cli_dir = None
            ide_dir = None

        kiro_state_dir = self.settings.data_dir.parent / "state"

        self.kiro_cli_source = KiroCLISessionSource(
            enabled=(self.settings.kiro_monitor_enabled and source_mode in ("kiro_cli", "auto")),
            sessions_dir=cli_dir,
            state_file=kiro_state_dir / "kiro_cli_state.json",
        )
        self.kiro_ide_source = KiroIDESessionSource(
            enabled=(self.settings.kiro_monitor_enabled and source_mode in ("kiro_ide", "auto")),
            sessions_dir=ide_dir,
            state_file=kiro_state_dir / "kiro_ide_state.json",
        )

        if source_mode == "kiro_cli":
            ai_sources.append(self.kiro_cli_source)
        elif source_mode == "kiro_ide":
            ai_sources.append(self.kiro_ide_source)
        elif source_mode == "auto":
            # Auto mode: include both Kiro CLI and Kiro IDE sources concurrently.
            # Their session paths are completely disjoint (cli/ vs workspace hashes).
            ai_sources.append(self.kiro_cli_source)
            ai_sources.append(self.kiro_ide_source)

            # Fall back to Antigravity when no Kiro directories exist at initialization.
            has_kiro_dir = (
                (self.kiro_cli_source.sessions_dir and self.kiro_cli_source.sessions_dir.exists())
                or (self.kiro_ide_source.sessions_dir and self.kiro_ide_source.sessions_dir.exists())
            )
            if not has_kiro_dir:
                ai_sources.append(self.antigravity_source)
        else:
            # Default: antigravity
            ai_sources.append(self.antigravity_source)

        self.sources: List[EventSource] = [
            self.system_source,
            self.git_source,
            *ai_sources,
        ]

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        self._pid_file = self.settings.data_dir / "collector.pid"
        self._meta_file = self.settings.data_dir / "collector_meta.json"

    def _write_pid_file(self) -> None:
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._pid_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except Exception as e:
            logger.warning("pid_file_write_failed", error=str(e))

        try:
            meta = {
                "pid": os.getpid(),
                "telemetry_source": getattr(self.settings, "telemetry_source", "antigravity"),
                "member_id": getattr(self.settings, "member_id", None),
                "device_id": getattr(self.settings, "device_id", None),
                "collector_version": getattr(self.settings, "collector_version", None),
                "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            with open(self._meta_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.warning("meta_file_write_failed", error=str(e))

    def _remove_pid_file(self) -> None:
        try:
            if self._pid_file.exists():
                self._pid_file.unlink()
        except Exception:
            pass
        try:
            if self._meta_file.exists():
                self._meta_file.unlink()
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
            storage=str(self.settings.data_dir),
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
        """Gracefully stop collector, emit shutdown events, and close resources."""
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

        self.store.close()
        self._running = False
        self._remove_pid_file()

        logger.info(
            "collector_stopped",
            member_id=self.identity_provider.get_current_member(),
            device_id=self.identity_provider.get_device_id(),
            status="STOPPED",
        )

    def poll_and_process(self) -> int:
        """Poll all active sources, process events, and append to JSONL store.

        Returns:
            Number of new events processed.
        """
        # Sync branch ticket context non-blockingly
        for p in self.settings.git_monitor_paths:
            try:
                self.attribution_store.sync_branch(p)
            except Exception:
                pass

        source_mode = getattr(self.settings, "telemetry_source", "antigravity")
        collected_count = 0
        for src in self.sources:
            if source_mode == "auto" and src == self.antigravity_source:
                # In auto mode, if Kiro directories have appeared since initialization,
                # prefer Kiro and suppress the Antigravity fallback
                has_kiro_active = (
                    (self.kiro_cli_source.sessions_dir and self.kiro_cli_source.sessions_dir.exists())
                    or (self.kiro_ide_source.sessions_dir and self.kiro_ide_source.sessions_dir.exists())
                )
                if has_kiro_active:
                    continue

            try:
                raw_events = src.collect()
                batch_success = True
                for raw in raw_events:
                    event = self.processor.process_raw(raw)
                    if event:
                        try:
                            if self.store.save_event(event):
                                self._log_event(event, src.name)
                                collected_count += 1
                        except (StoragePersistenceError, OSError, IOError) as spe:
                            logger.error(
                                "event_persistence_failed",
                                source=src.name,
                                event_id=getattr(event, "event_id", None),
                                error=str(spe),
                            )
                            batch_success = False
                            break

                # Atomically commit staged cursor state ONLY if all events in batch succeeded
                if batch_success:
                    if hasattr(src, "commit_state") and callable(src.commit_state):
                        src.commit_state()
                else:
                    if hasattr(src, "rollback_state") and callable(src.rollback_state):
                        src.rollback_state()
                    logger.warning(
                        "cursor_commit_aborted_due_to_persistence_failure",
                        source=src.name,
                    )
            except Exception as ex:
                if hasattr(src, "rollback_state") and callable(src.rollback_state):
                    try:
                        src.rollback_state()
                    except Exception:
                        pass
                logger.error("source_collect_error", source=src.name, error=str(ex))

        return collected_count

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
