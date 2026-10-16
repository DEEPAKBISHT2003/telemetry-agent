"""Event-driven upload trigger for AI Telemetry Agent.

Coordinates non-blocking, detached background upload sweeps after Antigravity AI runs complete.
Ensures race-safety, trigger cooldowns, and concurrency locking so that rapid consecutive Stop events
do not create competing upload processes or process storms.
"""

import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from ai_telemetry_agent.config.settings import (
    PROJECT_ROOT,
    Settings,
    get_user_agent_home,
    load_settings,
)
import logging
from ai_telemetry_agent.uploader.credentials import CredentialStore
from ai_telemetry_agent.uploader.uploader import UploaderFileLock, UploaderLockError

logger = logging.getLogger("telemetry.uploader.trigger")


def spawn_detached_uploader(
    python_exe: Optional[str] = None,
    custom_home: Optional[Path] = None,
) -> Optional[subprocess.Popen]:
    """Spawn an independent, detached subprocess running `telemetry-agent upload`.

    Detaches completely from parent process and standard streams so it never blocks.
    """
    py = python_exe or sys.executable
    cmd = [py, "-m", "ai_telemetry_agent.cli", "upload"]

    env = os.environ.copy()
    if custom_home:
        env["TELEMETRY_AGENT_HOME"] = str(custom_home)
        env["TELEMETRY_DATA_DIR"] = str(custom_home / "data")

    # Ensure PYTHONPATH includes project root and src directory
    src_dir = PROJECT_ROOT / "src"
    paths = [str(src_dir), str(PROJECT_ROOT)]
    existing_pp = env.get("PYTHONPATH")
    if existing_pp:
        paths.append(existing_pp)
    env["PYTHONPATH"] = os.pathsep.join(paths)

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "close_fds": True,
    }

    if sys.platform == "win32":
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **kwargs)
        return proc
    except Exception as ex:
        logger.warning("upload_trigger_spawn_failed", error=str(ex))
        return None


def maybe_trigger_upload(
    settings: Optional[Settings] = None,
    custom_home: Optional[Path] = None,
    cooldown_seconds: Optional[float] = None,
    python_exe: Optional[str] = None,
) -> bool:
    """Evaluate conditions and trigger a non-blocking background upload sweep.

    Conditions checked:
    1. settings.upload_enabled is True.
    2. Valid enrollment credentials exist in CredentialStore.
    3. Server URL is configured (in settings or enrollment metadata).
    4. Not currently within trigger cooldown window.
    5. An uploader process is not currently actively executing (UploaderFileLock).

    Returns:
        bool: True if an upload process was successfully spawned; False otherwise.
    """
    try:
        cfg = settings or load_settings()
        home = (custom_home or cfg.app_data_dir).resolve()
        home.mkdir(parents=True, exist_ok=True)

        # 1. Check if uploading is enabled
        if not cfg.upload_enabled:
            return False

        # 2. Check if device is enrolled
        cred_store = CredentialStore(custom_home=home)
        if not cred_store.has_credential():
            return False

        # 3. Check if server URL is configured
        server_url = cfg.telemetry_server_url
        if not server_url:
            meta = cred_store.load_metadata()
            if meta and meta.server_url:
                server_url = meta.server_url
        if not server_url:
            return False

        # 4. Check cooldown and concurrency
        cooldown = cooldown_seconds
        if cooldown is None:
            cooldown = getattr(cfg, "upload_trigger_cooldown_seconds", 5.0)

        trigger_lock_file = home / "uploader_trigger.lock"
        trigger_stamp_file = home / "uploader_trigger.timestamp"
        uploader_lock_file = home / "uploader.lock"

        # Serialize trigger checks using an advisory lock
        trigger_lock = UploaderFileLock(trigger_lock_file)
        try:
            trigger_lock.acquire()
        except UploaderLockError:
            # Another trigger evaluation is currently in progress
            return False

        try:
            now = time.time()
            if cooldown > 0 and trigger_stamp_file.is_file():
                try:
                    last_str = trigger_stamp_file.read_text(encoding="utf-8").strip()
                    if last_str:
                        last_ts = float(last_str)
                        if (now - last_ts) < cooldown:
                            return False
                except Exception:
                    pass

            # Check if an uploader process is already executing
            uploader_lock = UploaderFileLock(uploader_lock_file)
            try:
                uploader_lock.acquire()
                uploader_lock.release()
            except UploaderLockError:
                # Uploader is already running and actively uploading
                return False

            # Record trigger timestamp
            try:
                trigger_stamp_file.write_text(str(now), encoding="utf-8")
            except Exception:
                pass

        finally:
            trigger_lock.release()

        # 5. Spawn detached process
        proc = spawn_detached_uploader(
            python_exe=python_exe,
            custom_home=home,
        )
        return proc is not None

    except Exception as ex:
        logger.warning("maybe_trigger_upload_error", error=str(ex))
        return False
