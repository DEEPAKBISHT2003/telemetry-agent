"""Windows Task Scheduler Persistent Background Service Manager.

Manages registration, removal, inspection, and lifecycle of the persistent
AI Telemetry Uploader background task in Windows Task Scheduler.
"""

from dataclasses import dataclass
import csv
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple
import xml.sax.saxutils

from ai_telemetry_agent.config.settings import Settings, get_user_agent_home, load_settings
from ai_telemetry_agent.logging.structured import get_logger
from ai_telemetry_agent.uploader.credentials import CredentialStore
from ai_telemetry_agent.uploader.uploader import is_daemon_running

logger = get_logger("telemetry.service")

DEFAULT_TASK_NAME = "AITelemetryUploader"


class ServiceError(Exception):
    """Raised when service operations fail."""
    pass


class WindowsTaskSchedulerManager:
    """Manages persistent uploader execution via Windows Task Scheduler."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        custom_home: Optional[Path] = None,
        task_name: Optional[str] = None,
    ):
        self.home = (custom_home or get_user_agent_home()).resolve()
        self.task_name = task_name or DEFAULT_TASK_NAME
        self.cred_store = CredentialStore(custom_home=self.home)
        if settings is not None:
            self.settings = settings
        else:
            cfg_file = self.home / "config.env"
            if cfg_file.is_file():
                self.settings = load_settings(env_file=str(cfg_file))
            else:
                self.settings = load_settings()

    def resolve_pythonw(self) -> Path:
        """Resolve absolute path to pythonw.exe (windowless GUI interpreter on Windows)."""
        exe_dir = Path(sys.executable).parent
        pythonw_cand = exe_dir / "pythonw.exe"
        if pythonw_cand.is_file():
            return pythonw_cand.resolve()

        # Check virtualenv Scripts folder if sys.executable is inside a venv
        scripts_cand = exe_dir / "Scripts" / "pythonw.exe"
        if scripts_cand.is_file():
            return scripts_cand.resolve()

        # Fallback to sys.executable
        return Path(sys.executable).resolve()

    def verify_package_import(self, python_exe: Path) -> Tuple[bool, str]:
        """Verify that the target Python interpreter can import ai_telemetry_agent."""
        try:
            res = subprocess.run(
                [str(python_exe), "-c", "import ai_telemetry_agent; print('OK')"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if res.returncode == 0 and "OK" in res.stdout:
                return True, "OK"
            err = (res.stderr or res.stdout).strip()
            return False, err or f"Process returned exit code {res.returncode}"
        except Exception as ex:
            return False, str(ex)

    def check_prerequisites(self) -> Tuple[bool, str]:
        """Validate prerequisites before installing persistent Task Scheduler task.

        Checks:
        1. Operating system is Windows.
        2. Device is enrolled.
        3. Device credentials can be decrypted by current user (validates DPAPI).
        4. Uploading is enabled in configuration.
        5. Central server URL is configured.
        6. Python interpreter exists and can import ai_telemetry_agent.
        """
        if platform.system() != "Windows":
            return False, f"Windows Task Scheduler is only supported on Windows (detected: {platform.system()})."

        if not self.cred_store.has_credential():
            return False, "Device is not enrolled. Run 'telemetry-agent install' or 'telemetry-agent enroll' first."

        token = self.cred_store.load_credential()
        if not token:
            return False, "Device credential cannot be decrypted by current user."

        cfg_file = self.home / "config.env"
        if cfg_file.is_file():
            try:
                self.settings = load_settings(env_file=str(cfg_file))
            except Exception:
                pass

        if not self.settings.upload_enabled:
            return False, "Uploading is disabled (TELEMETRY_UPLOAD_ENABLED=false). Enable it in config.env."


        server_url = self.settings.telemetry_server_url
        if not server_url:
            meta = self.cred_store.load_metadata()
            if meta and meta.server_url:
                server_url = meta.server_url
        if not server_url:
            return False, "TELEMETRY_SERVER_URL is not configured."

        python_exe = self.resolve_pythonw()
        if not python_exe.is_file():
            return False, f"Python interpreter '{python_exe}' does not exist."

        ok, msg = self.verify_package_import(python_exe)
        if not ok:
            return False, f"Python environment cannot import ai_telemetry_agent: {msg}"

        return True, "Prerequisites satisfied."

    def build_task_xml(self, python_exe: Path) -> str:
        """Construct secure, schema-compliant Windows Task Scheduler XML.

        Configured for:
        - InteractiveToken logon trigger under the current user (DPAPI requirement).
        - LeastPrivilege run level (does not require administrator rights).
        - Unlimited execution time (<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>).
        - Hidden window execution.
        - IgnoreNew multiple instances policy.
        - Restart on failure policy.
        """
        user_domain = os.environ.get("USERDOMAIN", "")
        username = os.environ.get("USERNAME", "")
        full_user = f"{user_domain}\\{username}" if user_domain and username else (username or "CURRENT_USER")

        escaped_user = xml.sax.saxutils.escape(full_user)
        escaped_exe = xml.sax.saxutils.escape(str(python_exe))
        escaped_workdir = xml.sax.saxutils.escape(str(self.home))

        return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>AI Telemetry Agent background uploader daemon</Description>
    <URI>\\{self.task_name}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escaped_user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escaped_user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escaped_exe}</Command>
      <Arguments>-m ai_telemetry_agent.cli upload --daemon</Arguments>
      <WorkingDirectory>{escaped_workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

    def install(self, start: bool = False) -> Tuple[bool, str]:
        """Register the background uploader task in Windows Task Scheduler."""
        ok, msg = self.check_prerequisites()
        if not ok:
            return False, msg

        python_exe = self.resolve_pythonw()
        xml_content = self.build_task_xml(python_exe)

        temp_xml = self.home / f"{self.task_name}_{os.getpid()}.xml"
        try:
            temp_xml.write_text(xml_content, encoding="utf-16")

            cmd = ["schtasks.exe", "/Create", "/TN", self.task_name, "/XML", str(temp_xml), "/F"]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )

            if res.returncode != 0:
                err_detail = (res.stderr or res.stdout).strip()
                return False, f"Failed to register scheduled task: {err_detail}"

            logger.info("service_installed", task_name=self.task_name, python=str(python_exe))

            if start:
                start_ok, start_msg = self.start()
                if not start_ok:
                    return True, f"Service '{self.task_name}' installed successfully, but start failed: {start_msg}"
                return True, f"Service '{self.task_name}' installed and started successfully."

            return True, f"Service '{self.task_name}' installed successfully."
        except subprocess.TimeoutExpired:
            return False, "Registration timed out while calling schtasks.exe."
        except Exception as ex:
            return False, f"Unexpected error during task installation: {ex}"
        finally:
            temp_xml.unlink(missing_ok=True)

    def uninstall(self) -> Tuple[bool, str]:
        """Remove the background uploader task from Windows Task Scheduler."""
        if platform.system() != "Windows":
            return False, f"Windows Task Scheduler is only supported on Windows (detected: {platform.system()})."

        # Attempt to stop if currently running
        try:
            self.stop()
        except Exception:
            pass

        try:
            cmd = ["schtasks.exe", "/Delete", "/TN", self.task_name, "/F"]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )

            out_combined = f"{res.stdout} {res.stderr}".lower()
            if res.returncode != 0:
                if "cannot find the file specified" in out_combined or "does not exist" in out_combined:
                    return True, f"Service '{self.task_name}' is not registered."
                return False, f"Failed to delete scheduled task: {res.stderr or res.stdout}"

            logger.info("service_uninstalled", task_name=self.task_name)
            return True, f"Service '{self.task_name}' successfully uninstalled."
        except subprocess.TimeoutExpired:
            return False, "Deletion timed out while calling schtasks.exe."
        except Exception as ex:
            return False, f"Unexpected error during task uninstallation: {ex}"

    def status(self) -> Dict[str, Any]:
        """Query diagnostic status from Windows Task Scheduler and daemon process lock."""
        daemon_alive = is_daemon_running(self.home)

        if platform.system() != "Windows":
            return {
                "platform": platform.system(),
                "supported": False,
                "installed": False,
                "registered": False,
                "task_name": self.task_name,
                "is_daemon_running": daemon_alive,
                "state": "UNSUPPORTED_PLATFORM",
            }

        try:
            cmd = ["schtasks.exe", "/Query", "/TN", self.task_name, "/FO", "CSV", "/V"]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )

            if res.returncode != 0:
                return {
                    "platform": "Windows",
                    "supported": True,
                    "installed": False,
                    "registered": False,
                    "task_name": self.task_name,
                    "is_daemon_running": daemon_alive,
                    "state": "NOT_INSTALLED",
                }

            # Parse CSV output from schtasks
            reader = csv.DictReader(io.StringIO(res.stdout))
            row = next(reader, None)
            if not row:
                return {
                    "platform": "Windows",
                    "supported": True,
                    "installed": True,
                    "registered": True,
                    "task_name": self.task_name,
                    "is_daemon_running": daemon_alive,
                    "state": "UNKNOWN",
                }

            task_status = row.get("Status") or row.get("Scheduled Task State") or "Unknown"
            next_run = row.get("Next Run Time")
            last_run = row.get("Last Run Time")
            last_result = row.get("Last Result")
            author = row.get("Author")

            enabled = task_status.lower() != "disabled"

            return {
                "platform": "Windows",
                "supported": True,
                "installed": True,
                "registered": True,
                "task_name": self.task_name,
                "enabled": enabled,
                "state": task_status,
                "next_run_time": next_run if next_run and next_run != "N/A" else None,
                "last_run_time": last_run if last_run and last_run != "N/A" else None,
                "last_result": last_result if last_result and last_result != "N/A" else None,
                "author": author if author and author != "N/A" else None,
                "is_daemon_running": daemon_alive,
            }

        except subprocess.TimeoutExpired:
            return {
                "platform": "Windows",
                "supported": True,
                "installed": False,
                "registered": False,
                "task_name": self.task_name,
                "is_daemon_running": daemon_alive,
                "state": "TIMEOUT",
                "error": "Query timed out calling schtasks.exe",
            }
        except Exception as ex:
            return {
                "platform": "Windows",
                "supported": True,
                "installed": False,
                "registered": False,
                "task_name": self.task_name,
                "is_daemon_running": daemon_alive,
                "state": "ERROR",
                "error": str(ex),
            }

    def start(self) -> Tuple[bool, str]:
        """Manually trigger the scheduled task to run immediately."""
        if platform.system() != "Windows":
            return False, "Windows Task Scheduler is only supported on Windows."

        try:
            cmd = ["schtasks.exe", "/Run", "/TN", self.task_name]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )

            if res.returncode != 0:
                return False, f"Failed to start task: {res.stderr or res.stdout}"

            logger.info("service_started", task_name=self.task_name)
            return True, f"Service '{self.task_name}' started successfully."
        except subprocess.TimeoutExpired:
            return False, "Start command timed out calling schtasks.exe."
        except Exception as ex:
            return False, f"Unexpected error starting task: {ex}"

    def stop(self) -> Tuple[bool, str]:
        """Manually terminate the running scheduled task."""
        if platform.system() != "Windows":
            return False, "Windows Task Scheduler is only supported on Windows."

        try:
            cmd = ["schtasks.exe", "/End", "/TN", self.task_name]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )

            if res.returncode != 0:
                err = f"{res.stderr} {res.stdout}".lower()
                if "not running" in err:
                    return True, f"Service '{self.task_name}' is not currently running."
                return False, f"Failed to stop task: {res.stderr or res.stdout}"

            logger.info("service_stopped", task_name=self.task_name)
            return True, f"Service '{self.task_name}' stopped successfully."
        except subprocess.TimeoutExpired:
            return False, "Stop command timed out calling schtasks.exe."
        except Exception as ex:
            return False, f"Unexpected error stopping task: {ex}"

    def restart(self) -> Tuple[bool, str]:
        """Stop and restart the scheduled task."""
        self.stop()
        import time
        time.sleep(1.0)
        return self.start()
