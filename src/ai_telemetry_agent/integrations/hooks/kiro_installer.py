"""Kiro IDE Lifecycle Hook Installer and Management Module.

Provides safe, idempotent installation, uninstallation, and status verification for
Kiro IDE lifecycle hooks configured in ~/.kiro/hooks/telemetry.json or workspace .kiro/hooks/telemetry.json.

Preserves existing developer hooks, performs automatic timestamped backups before
modifications, and never exposes private or sensitive information.
"""

import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

KIRO_HOOK_PREFIX = "Telemetry - "
SUPPORTED_TRIGGERS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionEnd",
    "PostFileSave",
    "PostFileCreate",
    "PostFileDelete",
    "PreTaskExec",
    "PostTaskExec",
]


def get_kiro_hooks_path(custom_home: Optional[Path] = None, workspace_root: Optional[Path] = None) -> Path:
    """Return the absolute path to the Kiro hooks.json file.

    Defaults to: Path.home() / ".kiro" / "hooks" / "telemetry.json"
    """
    if workspace_root is not None:
        return (workspace_root / ".kiro" / "hooks" / "telemetry.json").resolve()
    home = custom_home if custom_home is not None else Path.home()
    return (home / ".kiro" / "hooks" / "telemetry.json").resolve()


def get_kiro_hook_handler_path() -> Path:
    """Return the absolute path to the kiro_hook_handler.py script."""
    return (Path(__file__).resolve().parent / "kiro_hook_handler.py").resolve()


def get_python_executable() -> str:
    """Return the absolute path to the active Python interpreter."""
    return sys.executable


def format_command(python_exe: str, handler_path: Path, trigger_name: str) -> str:
    """Format a Windows/POSIX safe command line string with proper quoting."""
    py_str = f'"{python_exe}"' if " " in str(python_exe) else str(python_exe)
    h_str = f'"{handler_path}"' if " " in str(handler_path) else str(handler_path)
    return f"{py_str} {h_str} {trigger_name}"


def build_kiro_hook_config(
    python_exe: Optional[str] = None,
    handler_path: Optional[Path] = None,
    timeout: int = 10,
    triggers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Construct the standard Kiro hook configuration dictionary."""
    py = python_exe or get_python_executable()
    hp = handler_path or get_kiro_hook_handler_path()
    selected_triggers = triggers or SUPPORTED_TRIGGERS

    hooks_list = []
    for trigger in selected_triggers:
        hooks_list.append({
            "name": f"{KIRO_HOOK_PREFIX}{trigger}",
            "trigger": trigger,
            "action": {
                "type": "command",
                "command": format_command(py, hp, trigger),
            },
            "timeout": timeout,
        })

    return {
        "version": "v1",
        "hooks": hooks_list,
    }


def _create_backup(file_path: Path, suffix: str = "backup") -> Path:
    """Create a timestamped backup of the given file in the same directory."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = file_path.parent / f"{file_path.name}.{suffix}-{ts}"
    backup_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def install_kiro_hooks(
    hooks_file: Optional[Path] = None,
    python_exe: Optional[str] = None,
    handler_path: Optional[Path] = None,
    triggers: Optional[List[str]] = None,
) -> Tuple[bool, str, Optional[Path], int]:
    """Install or update Kiro lifecycle hooks in the target hooks file.

    Returns:
        (success, message, backup_path, other_hooks_count)
    """
    target = hooks_file if hooks_file is not None else get_kiro_hooks_path()
    py = python_exe or get_python_executable()
    hp = handler_path or get_kiro_hook_handler_path()

    if not hp.is_file():
        return False, f"Hook handler file not found at: {hp}", None, 0

    desired_config = build_kiro_hook_config(py, hp, triggers=triggers)
    desired_hooks = desired_config["hooks"]
    other_hooks_count = 0
    backup_path = None

    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        try:
            content = target.read_text(encoding="utf-8")
            existing_data = json.loads(content) if content.strip() else {}
        except Exception as e:
            malformed_backup = _create_backup(target, suffix="malformed")
            return (
                False,
                f"Existing Kiro hooks file contains malformed JSON ({e}). Preserved backup at {malformed_backup}",
                malformed_backup,
                0,
            )

        if not isinstance(existing_data, dict):
            malformed_backup = _create_backup(target, suffix="malformed")
            return (
                False,
                f"Existing Kiro hooks root is not a JSON object. Preserved backup at {malformed_backup}",
                malformed_backup,
                0,
            )

        existing_hooks = existing_data.get("hooks", [])
        if not isinstance(existing_hooks, list):
            existing_hooks = []

        other_hooks = [h for h in existing_hooks if isinstance(h, dict) and not str(h.get("name", "")).startswith(KIRO_HOOK_PREFIX)]
        other_hooks_count = len(other_hooks)

        # Check idempotency: if telemetry hooks match desired hooks exactly
        installed_telemetry_hooks = [
            h for h in existing_hooks if isinstance(h, dict) and str(h.get("name", "")).startswith(KIRO_HOOK_PREFIX)
        ]
        if installed_telemetry_hooks == desired_hooks:
            return True, "Kiro telemetry hooks already installed and up to date.", None, other_hooks_count

        # Backup before making changes
        backup_path = _create_backup(target, suffix="backup")

        # Merge desired telemetry hooks with other hooks
        merged_hooks = other_hooks + desired_hooks
        target_data = {
            "version": existing_data.get("version", "v1"),
            "hooks": merged_hooks,
        }
    else:
        target_data = desired_config

    try:
        target.write_text(json.dumps(target_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True, "Kiro hook installation successful.", backup_path, other_hooks_count
    except Exception as e:
        return False, f"Failed to write Kiro hooks file: {e}", backup_path, other_hooks_count


def uninstall_kiro_hooks(
    hooks_file: Optional[Path] = None,
) -> Tuple[bool, str, Optional[Path]]:
    """Remove only telemetry hooks from the Kiro hooks configuration file, preserving unrelated hooks.

    Returns:
        (success, message, backup_path)
    """
    target = hooks_file if hooks_file is not None else get_kiro_hooks_path()

    if not target.exists():
        return True, "No Kiro hooks file found; nothing to uninstall.", None

    try:
        content = target.read_text(encoding="utf-8")
        existing_data = json.loads(content) if content.strip() else {}
    except Exception as e:
        return False, f"Cannot uninstall: Kiro hooks file contains invalid JSON ({e}).", None

    if not isinstance(existing_data, dict):
        return True, "Kiro telemetry hooks were not installed.", None

    existing_hooks = existing_data.get("hooks", [])
    if not isinstance(existing_hooks, list):
        return True, "Kiro telemetry hooks were not installed.", None

    retained_hooks = [
        h for h in existing_hooks if isinstance(h, dict) and not str(h.get("name", "")).startswith(KIRO_HOOK_PREFIX)
    ]

    if len(retained_hooks) == len(existing_hooks):
        return True, "Kiro telemetry hooks were not installed.", None

    # Backup before modification
    backup_path = _create_backup(target, suffix="backup")

    if not retained_hooks:
        # File only contained telemetry hooks; safely remove file
        try:
            target.unlink()
            return True, "Kiro telemetry hooks file removed (no other hooks present).", backup_path
        except Exception as e:
            return False, f"Failed to remove hooks file: {e}", backup_path
    else:
        existing_data["hooks"] = retained_hooks
        try:
            target.write_text(json.dumps(existing_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return True, "Kiro telemetry hooks removed successfully (preserved other hooks).", backup_path
        except Exception as e:
            return False, f"Failed to write updated Kiro hooks file: {e}", backup_path


def get_kiro_hook_status(
    hooks_file: Optional[Path] = None,
    handler_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect and return comprehensive status of Kiro hooks configuration."""
    target = hooks_file if hooks_file is not None else get_kiro_hooks_path()
    hp = handler_path or get_kiro_hook_handler_path()
    py = get_python_executable()

    status: Dict[str, Any] = {
        "config_path": str(target),
        "config_exists": target.exists(),
        "json_valid": False,
        "telemetry_hook_installed": False,
        "handler_path": str(hp),
        "handler_valid": hp.is_file(),
        "python_executable": py,
        "telemetry_hooks_count": 0,
        "other_hooks_count": 0,
        "installed_triggers": [],
    }

    if not target.exists():
        return status

    try:
        content = target.read_text(encoding="utf-8")
        data = json.loads(content) if content.strip() else {}
        status["json_valid"] = isinstance(data, dict)
        if status["json_valid"]:
            hooks_list = data.get("hooks", [])
            if isinstance(hooks_list, list):
                telemetry_hooks = [
                    h for h in hooks_list if isinstance(h, dict) and str(h.get("name", "")).startswith(KIRO_HOOK_PREFIX)
                ]
                status["telemetry_hook_installed"] = len(telemetry_hooks) > 0
                status["telemetry_hooks_count"] = len(telemetry_hooks)
                status["other_hooks_count"] = len(hooks_list) - len(telemetry_hooks)
                status["installed_triggers"] = [h.get("trigger") for h in telemetry_hooks if h.get("trigger")]
    except Exception:
        status["json_valid"] = False

    return status
