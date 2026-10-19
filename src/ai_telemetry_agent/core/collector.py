"""Core Telemetry Collector engine managing lifecycle, sources, and JSONL persistence."""

import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import List, Optional, Tuple

from ai_telemetry_agent.config.settings import Settings, load_settings
from ai_telemetry_agent.core.event import ContextInfo, EventType, TelemetryEvent, is_kiro_routing_alias
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
from ai_telemetry_agent.accounting.store import AccountingStore
from ai_telemetry_agent.accounting.aggregator import AccountingAggregator
from ai_telemetry_agent.accounting.registry import HookRegistry
from ai_telemetry_agent.accounting.models import (
    AIOperationRecord,
    ExecutionStatus,
    MeasurementScope,
    MeasurementStatus,
    OperationType,
    TriggerType,
    to_decimal,
)

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
        self.accounting_store = AccountingStore(self.settings.data_dir)
        self.accounting_aggregator = AccountingAggregator(self.accounting_store)
        self.hook_registry = HookRegistry(self.settings.data_dir)

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
                                rec = self._ai_event_to_operation_record(event)
                                if rec:
                                    try:
                                        self.accounting_store.save_operation(rec)
                                    except Exception as ex:
                                        logger.warning("accounting_operation_save_failed", error=str(ex))
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

    def _ai_event_to_operation_record(self, event: TelemetryEvent) -> Optional[AIOperationRecord]:
        """Convert a TelemetryEvent into an authoritative AIOperationRecord if applicable."""
        if not event or not event.payload:
            return None

        if event.event_type == EventType.AI_OPERATION:
            try:
                return AIOperationRecord.from_dict(event.payload)
            except Exception:
                return None

        ai_event_types = {
            EventType.AI_RUN_COMPLETED,
            EventType.AI_TURN,
            EventType.AI_RUN,
            EventType.AI_TOOL_CALL,
            EventType.TOOL_CALL,
            EventType.AGENT_STARTED,
            EventType.AGENT_COMPLETED,
        }
        if event.event_type not in ai_event_types:
            return None

        payload = event.payload
        metadata = payload.get("metadata", {})
        provider = payload.get("provider") or payload.get("source")
        if not provider or provider == "unknown":
            cap = payload.get("capture_method", "")
            if "kiro_ide" in cap or "messages_jsonl" in cap:
                provider = "kiro_ide"
            elif "kiro_cli" in cap or "cli" in cap:
                provider = "kiro_cli"
            elif "antigravity" in cap or cap == "hooks":
                provider = "antigravity"
            else:
                provider = "unknown"
        session_id = (event.context.session_id if event.context else None) or payload.get("session_id") or "unknown"
        run_id = payload.get("run_id") or payload.get("execution_id") or event.event_id

        # Kiro IDE session files emit several message types per AI turn, but only
        # the usage_summary / turn_completed message carries authoritative credits
        # (the turn's metered usage). Tool calls and context-usage metadata are
        # components of a turn, billed in its completion summary, not separately
        # billable operations. Excluding them from the credit ledger:
        #   - avoids inflating the operation and unmeasured-operation counts;
        #   - prevents the contextUsage ai_turn from being misclassified as an
        #     interactive prompt;
        #   - prevents any future per-event credit assignment from double-counting
        #     the completion's credits across prompt/tool-call/completion events.
        # Subagent lifecycle events are retained (they carry credits=None with
        # parent links and represent legitimate unmeasured work). This is scoped
        # to the IDE session-file path (capture_method=messages_jsonl) so Kiro
        # CLI, Antigravity, and Kiro IDE hook-captured events are unaffected.
        cm = payload.get("capture_method", "")
        if (
            provider == "kiro_ide"
            and cm == "messages_jsonl"
            and (
                event.event_type in (
                    EventType.AI_TOOL_CALL,
                    EventType.TOOL_CALL,
                )
                or (
                    event.event_type == EventType.AI_TURN
                    and metadata.get("key") == "contextUsage"
                )
            )
        ):
            return None

        # Determine operation type and trigger type
        is_subagent = bool(
            metadata.get("is_subagent")
            or event.event_type in (EventType.AGENT_STARTED, EventType.AGENT_COMPLETED)
            or metadata.get("session_created_reason") == "subagent"
            or metadata.get("parent_agent_id")
            or metadata.get("parent_execution_id")
        )
        b_tools = metadata.get("builtin_tool_uses") or 0
        t_reqs = metadata.get("total_request_count") if metadata.get("total_request_count") is not None else 1
        is_tool = bool(
            event.event_type in (EventType.TOOL_CALL, EventType.AI_TOOL_CALL)
            or (b_tools > 0 and t_reqs == 0)
        )
        r_attempts = metadata.get("request_attempts") or 1
        has_retries = bool(
            metadata.get("has_retries")
            or (t_reqs is not None and r_attempts is not None and r_attempts > t_reqs)
        )
        retry_attempts = (
            metadata.get("retry_attempts")
            if metadata.get("retry_attempts") is not None
            else (max(0, r_attempts - t_reqs) if (t_reqs is not None and r_attempts is not None) else 0)
        )
        is_dedicated_retry = bool(
            metadata.get("is_retry") and not metadata.get("turn_index") and not metadata.get("total_request_count")
        )

        # Update metadata to reflect retry activity separately from operation type
        metadata["has_retries"] = has_retries
        metadata["retry_attempts"] = retry_attempts
        metadata["is_retry"] = is_dedicated_retry
        if "retry_credits" not in metadata:
            metadata["retry_credits"] = None

        hook_trigger = metadata.get("hook_trigger") or metadata.get("hook_event_name")
        is_hook = bool(hook_trigger or payload.get("capture_method") == "kiro_hooks")

        if is_subagent:
            op_type = OperationType.SUBAGENT
            trig_type = TriggerType.AGENT_SPAWNED
        elif is_dedicated_retry:
            op_type = OperationType.RETRY
            trig_type = TriggerType.USER_INITIATED
        elif is_tool:
            op_type = OperationType.TOOL_TRIGGERED
            trig_type = TriggerType.AGENT_SPAWNED if not is_hook else TriggerType.EVENT_TRIGGERED
        elif provider == "antigravity":
            op_type = OperationType.QUERY
            trig_type = TriggerType.USER_INITIATED
        else:
            op_type = OperationType.PROMPT
            trig_type = TriggerType.USER_INITIATED

        if is_hook:
            if metadata.get("is_schedule") or metadata.get("schedule_expression"):
                trig_type = TriggerType.HOOK_SCHEDULED
            elif hook_trigger in ("SessionStart", "session_start", "UserPromptSubmit", "user_prompt_submit"):
                trig_type = TriggerType.USER_INITIATED
            else:
                trig_type = TriggerType.HOOK_AUTOMATED

        # Credits & measurement status
        raw_credits = payload.get("credits")
        credits_dec = to_decimal(raw_credits)
        credit_src = None

        if credits_dec is not None:
            meas_status = MeasurementStatus.MEASURED
            credit_src = f"{provider}:{payload.get('capture_method', 'telemetry')}"
        elif is_hook and metadata.get("is_shell_only"):
            credits_dec = Decimal("0")
            meas_status = MeasurementStatus.MEASURED
            credit_src = "hook_shell_execution"
        elif provider == "antigravity":
            # Token metered, not credit metered
            meas_status = MeasurementStatus.UNAVAILABLE
            credit_src = "antigravity_sqlite"
        else:
            meas_status = MeasurementStatus.UNAVAILABLE

        # Tokens
        tokens = None
        in_t = payload.get("input_tokens")
        out_t = payload.get("output_tokens")
        tot_t = payload.get("total_tokens")
        if in_t is not None or out_t is not None or tot_t is not None:
            tokens = {
                "input_tokens": in_t,
                "output_tokens": out_t,
                "cache_read_tokens": payload.get("cache_read_tokens"),
                "total_tokens": tot_t,
            }

        # Parent links
        parent_op = (
            payload.get("parent_operation_id")
            or metadata.get("parent_operation_id")
            or payload.get("parent_run_id")
            or metadata.get("parent_run_id")
            or metadata.get("parent_agent_id")
            or metadata.get("parent_execution_id")
        )
        parent_sess = payload.get("parent_session_id") or metadata.get("parent_session_id")
        corr_status = payload.get("correlation_status") or metadata.get("correlation_status")
        corr_reason = payload.get("correlation_reason") or metadata.get("correlation_reason")

        # Models
        model_req = payload.get("selected_model") or payload.get("model")
        model_res = payload.get("resolved_model")
        # A Kiro routing alias is a selection mode, never a resolved model.
        # Normalize consistently for both current and historical records so
        # unavailable model identity is represented uniformly across events,
        # accounting operations, and summaries (no historical rewrite required).
        if is_kiro_routing_alias(model_req):
            model_res = None
        elif model_res is not None and is_kiro_routing_alias(model_res):
            model_res = None

        # Status
        status_val = metadata.get("status") or metadata.get("end_reason") or payload.get("status")
        if event.event_type == EventType.AI_RUN_FAILED or status_val in ("failure", "failed", "error"):
            exec_status = ExecutionStatus.FAILURE
        else:
            exec_status = ExecutionStatus.SUCCESS

        jira_key = event.context.jira_task_id if event.context else None
        repo_dict = event.context.repository.to_dict() if (event.context and event.context.repository) else None
        branch = event.context.repository.branch if (event.context and event.context.repository) else None
        
        started_at = payload.get("started_at")
        completed_at = payload.get("completed_at") or event.timestamp

        if started_at is None and provider == "kiro_cli":            
            duration_ms = metadata.get("turn_duration_ms")

            # Fall back to the structured duration emitted by Kiro CLI.
            if duration_ms is None:
                raw_duration = metadata.get("turn_duration")
                if isinstance(raw_duration, dict):
                    secs = raw_duration.get("secs", 0)
                    nanos = raw_duration.get("nanos", 0)

                    if (
                        isinstance(secs, (int, float))
                        and not isinstance(secs, bool)
                        and isinstance(nanos, (int, float))
                        and not isinstance(nanos, bool)
                        and secs >= 0
                        and nanos >= 0
                    ):
                        duration_ms = (secs * 1000.0) + (nanos / 1_000_000.0)
            if (
                isinstance(duration_ms, (int, float))
                and not isinstance(duration_ms, bool)
                and duration_ms >= 0
                and completed_at is not None
            ):
                try:
                    end_dt = datetime.datetime.fromisoformat(
                        str(completed_at).replace("Z", "+00:00")
                    )
                    started_at = (
                        end_dt - datetime.timedelta(milliseconds=duration_ms)
                    ).isoformat()
                    metadata["start_time_inferred_from_duration"] = True
                except (ValueError, TypeError, OverflowError):
                    started_at = None
            else:
                started_at = None
        
        return AIOperationRecord(
            operation_id=run_id,
            parent_operation_id=parent_op,
            parent_session_id=parent_sess,
            correlation_status=corr_status,
            correlation_reason=corr_reason,
            provider=provider,
            source=payload.get("capture_method", "collector"),
            operation_type=op_type,
            trigger_type=trig_type,
            hook_id=f"kiro_{hook_trigger.lower()}" if hook_trigger else None,
            hook_name=hook_trigger,
            session_id=session_id,
            request_id=run_id,
            model_requested=model_req,
            model_resolved=model_res,
            started_at=started_at,
            completed_at=completed_at,
            credits_used=credits_dec,
            credit_source=credit_src,
            measurement_scope=MeasurementScope.OPERATION,
            measurement_status=meas_status,
            token_usage=tokens,
            jira_key=jira_key,
            repository=repo_dict,
            branch=branch,
            developer_id=event.identity.member_id if event.identity else None,
            device_id=event.collector.device_id if event.collector else None,
            execution_status=exec_status,
            deduplication_key=f"{provider}:{session_id}:{run_id}",
            metadata=metadata,
        )

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
