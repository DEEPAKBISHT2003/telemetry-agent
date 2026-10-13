"""Core Telemetry Uploader engine for AI Telemetry Agent.

Coordinates:
- Cross-platform process-level locking to prevent concurrent upload races.
- Reading unacknowledged JSONL events using CheckpointManager offsets.
- Batching up to 500 events and 2 MiB payload limit.
- Privacy pre-flight scrubbing and HTTPS upload via BackendClient.
- Atomic checkpoint advancement upon HTTP 200 / 201 acknowledgment.
- Exponential backoff on transient network, 429, and 5xx errors.
- Background daemon lifecycle with clean thread shutdown.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import platform
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from ai_telemetry_agent.config.settings import Settings, get_user_agent_home, load_settings
from ai_telemetry_agent.uploader.checkpoint import CheckpointManager
from ai_telemetry_agent.uploader.client import (
    AuthenticationError,
    BackendClient,
    ConflictError,
    NetworkError,
    RateLimitError,
    ServerError,
    UploaderClientError,
)
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.uploader.credentials import CredentialStore

logger = get_logger("telemetry.uploader")


class UploaderLockError(Exception):
    """Raised when another uploader process currently holds the lock."""
    pass


class UploaderFileLock:
    """Cross-platform advisory file lock for the telemetry uploader process."""

    def __init__(self, lock_file_path: Optional[Path] = None):
        if lock_file_path is None:
            self.lock_path = (get_user_agent_home() / "uploader.lock").resolve()
        else:
            self.lock_path = Path(lock_file_path).resolve()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = None
        self._locked = False

    def acquire(self) -> bool:
        """Attempt non-blocking acquisition of the file lock."""
        if self._locked:
            return True

        try:
            self._fp = open(self.lock_path, "a+", encoding="utf-8")
            if platform.system() == "Windows":
                import msvcrt
                try:
                    self._fp.seek(0)
                    msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
                except (OSError, PermissionError) as e:
                    self._fp.close()
                    self._fp = None
                    raise UploaderLockError(f"Another uploader process holds lock on '{self.lock_path}'") from e
            else:
                import fcntl
                try:
                    fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError) as e:
                    self._fp.close()
                    self._fp = None
                    raise UploaderLockError(f"Another uploader process holds lock on '{self.lock_path}'") from e

            # Write current PID
            try:
                self._fp.seek(0)
                self._fp.truncate(0)
                self._fp.write(f"{os.getpid()}\n")
                self._fp.flush()
            except Exception:
                pass

            self._locked = True
            return True
        except UploaderLockError:
            raise
        except Exception as ex:
            if self._fp:
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
            raise UploaderLockError(f"Failed to acquire uploader lock: {ex}") from ex

    def release(self) -> None:
        """Release the acquired file lock."""
        if not self._locked or not self._fp:
            return

        try:
            if platform.system() == "Windows":
                import msvcrt
                try:
                    self._fp.seek(0)
                    msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
        finally:
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
            self._locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


@dataclass
class UploadCycleResult:
    """Summary metrics and status for an upload cycle."""
    status: str  # SUCCESS, EMPTY, NOT_ENROLLED, LOCKED, AUTH_ERROR, ERROR, DISABLED
    batches_submitted: int = 0
    events_submitted: int = 0
    newly_persisted_count: int = 0
    duplicate_count: int = 0
    error_message: Optional[str] = None


class TelemetryUploader:
    """Coordinates batch preparation, transport, and atomic checkpoint persistence."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        credential_store: Optional[CredentialStore] = None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        backend_client: Optional[BackendClient] = None,
        lock_file_path: Optional[Path] = None,
    ):
        self.settings = settings or load_settings()
        self.home = self.settings.app_data_dir
        self.credential_store = credential_store or CredentialStore(custom_home=self.home)
        self.checkpoint_manager = checkpoint_manager or CheckpointManager(custom_home=self.home)
        self.lock = UploaderFileLock(lock_file_path or (self.home / "uploader.lock"))

        # Backend client resolution
        server_url = self.settings.telemetry_server_url
        if not server_url:
            meta = self.credential_store.load_metadata()
            if meta and meta.server_url:
                server_url = meta.server_url

        if backend_client:
            self.client = backend_client
        elif server_url:
            self.client = BackendClient(
                server_url=server_url,
                timeout_seconds=self.settings.upload_timeout_seconds,
                allow_insecure_http=self.settings.allow_insecure_http,
                max_retries=self.settings.upload_max_retries,
                backoff_factor=self.settings.upload_backoff_factor,
            )
        else:
            self.client = None

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status information about current uploader state."""
        has_cred = self.credential_store.has_credential()
        meta = self.credential_store.load_metadata()
        server_url = self.settings.telemetry_server_url or (meta.server_url if meta else None)

        pending_est = self._estimate_pending_events()

        return {
            "upload_enabled": self.settings.upload_enabled,
            "enrolled": has_cred,
            "server_url": server_url,
            "device_id": self.settings.device_id,
            "member_id": self.settings.member_id,
            "estimated_pending_events": pending_est,
            "files_tracked": len(self.checkpoint_manager.get_all_file_checkpoints()),
        }

    def _estimate_pending_events(self) -> int:
        """Estimate pending unacknowledged events across active JSONL files."""
        data_dir = self.settings.data_dir
        if not data_dir.is_dir():
            return 0

        pending_lines = 0
        jsonl_files = sorted(data_dir.glob("telemetry-*.jsonl"))
        for fpath in jsonl_files:
            if self.checkpoint_manager.is_completed(fpath.name):
                continue
            offset = self.checkpoint_manager.get_offset(fpath.name)
            try:
                fsize = fpath.stat().st_size
                if fsize > offset:
                    # Quick approximate line estimate based on unread bytes
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fp:
                        fp.seek(offset)
                        for _ in fp:
                            pending_lines += 1
            except Exception:
                pass
        return pending_lines

    def upload_cycle(self, force: bool = False) -> UploadCycleResult:
        """Execute one complete batch-upload sweep across pending JSONL files.

        Args:
            force: If True, executes even if settings.upload_enabled is False.
        """
        if not self.settings.upload_enabled and not force:
            return UploadCycleResult(status="DISABLED", error_message="Automatic upload is disabled")

        # 1. Verify device enrollment
        credential_token = self.credential_store.load_credential()
        if not credential_token:
            logger.info("uploader_not_enrolled", msg="No device credentials found")
            return UploadCycleResult(
                status="NOT_ENROLLED",
                error_message="Device is not enrolled. Run 'telemetry-agent enroll' first.",
            )

        if not self.client:
            return UploadCycleResult(
                status="ERROR",
                error_message="TELEMETRY_SERVER_URL is not configured and cannot be resolved from enrollment metadata.",
            )

        # 2. Acquire concurrency lock
        try:
            self.lock.acquire()
        except UploaderLockError as le:
            logger.warning("uploader_lock_busy", error=str(le))
            return UploadCycleResult(status="LOCKED", error_message=str(le))

        try:
            return self._execute_upload_sweep(credential_token)
        finally:
            self.lock.release()

    def _execute_upload_sweep(self, credential_token: str) -> UploadCycleResult:
        """Sweep all active JSONL files and upload batches sequentially."""
        data_dir = self.settings.data_dir
        if not data_dir.is_dir():
            return UploadCycleResult(status="EMPTY")

        jsonl_files = sorted(data_dir.glob("telemetry-*.jsonl"), key=lambda p: p.name)
        if not jsonl_files:
            return UploadCycleResult(status="EMPTY")

        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_today_file = f"telemetry-{today_utc}.jsonl"

        batches_submitted = 0
        events_submitted = 0
        newly_persisted_count = 0
        duplicate_count = 0

        max_batch_events = min(self.settings.upload_max_batch_size, 500)
        max_batch_bytes = min(self.settings.upload_max_batch_bytes, 2 * 1024 * 1024)

        for fpath in jsonl_files:
            filename = fpath.name
            if self.checkpoint_manager.is_completed(filename):
                continue

            start_offset = self.checkpoint_manager.get_offset(filename)
            file_size = fpath.stat().st_size
            if file_size <= start_offset:
                # If file belongs to a past UTC date and is at EOF, mark completed
                if filename < current_today_file:
                    self.checkpoint_manager.mark_completed(filename, start_offset)
                continue

            # Read events starting from last acknowledged byte offset
            try:
                with open(fpath, "rb") as fp:
                    fp.seek(start_offset)
                    current_offset = start_offset

                    batch_events: List[Dict[str, Any]] = []
                    batch_bytes = 0
                    last_event_id: Optional[str] = None
                    batch_end_offset = current_offset

                    while True:
                        line_start_offset = fp.tell()
                        line_bytes = fp.readline()
                        if not line_bytes:
                            # Reached EOF for this file
                            break

                        line_end_offset = fp.tell()
                        line_str = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line_str:
                            current_offset = line_end_offset
                            continue

                        try:
                            event_data = json.loads(line_str)
                            if isinstance(event_data, dict):
                                batch_events.append(event_data)
                                batch_bytes += len(line_bytes)
                                last_event_id = event_data.get("event_id")
                                batch_end_offset = line_end_offset
                        except Exception as parse_err:
                            logger.warning(
                                "jsonl_parse_error_skipping_line",
                                file=filename,
                                offset=line_start_offset,
                                error=str(parse_err),
                            )
                            current_offset = line_end_offset
                            continue

                        # Check if batch reached size or byte limit
                        if len(batch_events) >= max_batch_events or batch_bytes >= max_batch_bytes:
                            # Send batch
                            res = self._send_and_checkpoint(
                                credential_token=credential_token,
                                events=batch_events,
                                filename=filename,
                                end_offset=batch_end_offset,
                                last_event_id=last_event_id,
                            )
                            batches_submitted += 1
                            events_submitted += len(batch_events)
                            newly_persisted_count += res.get("newly_persisted_count", 0)
                            duplicate_count += res.get("duplicate_count", 0)

                            # Reset batch accumulation
                            batch_events = []
                            batch_bytes = 0
                            current_offset = batch_end_offset

                    # Send any remaining events accumulated before EOF
                    if batch_events:
                        res = self._send_and_checkpoint(
                            credential_token=credential_token,
                            events=batch_events,
                            filename=filename,
                            end_offset=batch_end_offset,
                            last_event_id=last_event_id,
                        )
                        batches_submitted += 1
                        events_submitted += len(batch_events)
                        newly_persisted_count += res.get("newly_persisted_count", 0)
                        duplicate_count += res.get("duplicate_count", 0)
                        current_offset = batch_end_offset

                    # If file is from a past date and we are at EOF, mark completed
                    if filename < current_today_file:
                        self.checkpoint_manager.mark_completed(filename, current_offset)

            except AuthenticationError as auth_err:
                logger.error("uploader_authentication_failed", error=str(auth_err))
                return UploadCycleResult(
                    status="AUTH_ERROR",
                    batches_submitted=batches_submitted,
                    events_submitted=events_submitted,
                    newly_persisted_count=newly_persisted_count,
                    duplicate_count=duplicate_count,
                    error_message=f"Device credentials invalid or revoked: {auth_err}",
                )
            except (RateLimitError, ServerError, NetworkError, ConflictError, UploaderClientError) as client_err:
                logger.warning("uploader_transient_error", error=str(client_err))
                return UploadCycleResult(
                    status="ERROR",
                    batches_submitted=batches_submitted,
                    events_submitted=events_submitted,
                    newly_persisted_count=newly_persisted_count,
                    duplicate_count=duplicate_count,
                    error_message=f"Upload stopped due to backend/network error: {client_err}",
                )
            except Exception as unexp_err:
                logger.error("uploader_unexpected_error", error=str(unexp_err))
                return UploadCycleResult(
                    status="ERROR",
                    batches_submitted=batches_submitted,
                    events_submitted=events_submitted,
                    newly_persisted_count=newly_persisted_count,
                    duplicate_count=duplicate_count,
                    error_message=f"Unexpected error: {unexp_err}",
                )

        if events_submitted == 0:
            return UploadCycleResult(status="EMPTY")

        return UploadCycleResult(
            status="SUCCESS",
            batches_submitted=batches_submitted,
            events_submitted=events_submitted,
            newly_persisted_count=newly_persisted_count,
            duplicate_count=duplicate_count,
        )

    def _send_and_checkpoint(
        self,
        credential_token: str,
        events: List[Dict[str, Any]],
        filename: str,
        end_offset: int,
        last_event_id: Optional[str],
    ) -> Dict[str, Any]:
        """Dispatch a single batch to backend and atomically advance checkpoint on acknowledgment."""
        batch_id = uuid.uuid4()
        res = self.client.upload_batch(
            credential_token=credential_token,
            events=events,
            batch_id=batch_id,
        )
        # Advance checkpoint atomically only upon 200 or 201 response
        self.checkpoint_manager.update_checkpoint(
            filename=filename,
            byte_offset=end_offset,
            last_event_id=last_event_id,
        )
        return res


class UploaderDaemon:
    """Managed background worker thread for automatic periodic telemetry uploads."""

    def __init__(self, uploader: Optional[TelemetryUploader] = None, settings: Optional[Settings] = None):
        self.uploader = uploader or TelemetryUploader(settings=settings)
        self.settings = self.uploader.settings
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        """Start the background uploader worker thread."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="telemetry-uploader-daemon",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "uploader_daemon_started",
            interval_seconds=self.settings.upload_interval_seconds,
            server_url=self.settings.telemetry_server_url,
        )

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the background worker thread to terminate cleanly and wait for it."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("uploader_daemon_stopped")

    def is_alive(self) -> bool:
        """Check if background worker thread is currently running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def _worker_loop(self) -> None:
        """Periodic background sweep loop."""
        interval = max(1.0, float(self.settings.upload_interval_seconds))

        # Initial small delay to let collector startup finish
        if self._stop_event.wait(timeout=2.0):
            return

        while self._running and not self._stop_event.is_set():
            try:
                res = self.uploader.upload_cycle()
                if res.status == "SUCCESS":
                    logger.info(
                        "upload_cycle_success",
                        batches=res.batches_submitted,
                        events=res.events_submitted,
                        persisted=res.newly_persisted_count,
                        duplicates=res.duplicate_count,
                    )
                elif res.status in ("AUTH_ERROR", "ERROR"):
                    logger.warning("upload_cycle_issue", status=res.status, error=res.error_message)
            except Exception as ex:
                logger.error("uploader_daemon_unhandled_exception", error=str(ex))

            # Sleep for interval or until stopped
            if self._stop_event.wait(timeout=interval):
                break
