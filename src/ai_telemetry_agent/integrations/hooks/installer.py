"""Global Antigravity Lifecycle Hook Installer and Management Module.

Provides safe, idempotent installation, uninstallation, and status verification for
Antigravity lifecycle hooks configured globally in %USERPROFILE%/.gemini/config/hooks.json.

Preserves existing developer hooks, performs automatic timestamped backups before
modifications, and never exposes private or sensitive information.
"""

import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Tuple

TELEMETRY_HOOK_KEY = "telemetry-lifecycle"


def get_global_hooks_path(custom_home: Optional[Path] = None) -> Path:
    """Return the absolute path to the global Antigravity hooks.json.

    Defaults to: Path.home() / ".gemini" / "config" / "hooks.json"
    """
    home = custom_home if custom_home is not None else Path.home()
    return (home / ".gemini" / "config" / "hooks.json").resolve()


def get_hook_handler_path() -> Path:
    """Return the absolute path to the hook_handler.py script."""
    return (Path(__file__).resolve().parent / "hook_handler.py").resolve()


def get_python_executable() -> str:
    """Return the absolute path to the active Python interpreter."""
    return sys.executable


def format_command(python_exe: str, handler_path: Path, hook_name: str) -> str:
    """Format a Windows/POSIX safe command line string with proper quoting."""
    py_str = f'"{python_exe}"' if " " in str(python_exe) else str(python_exe)
    h_str = f'"{handler_path}"' if " " in str(handler_path) else str(handler_path)
    return f"{py_str} {h_str} {hook_name}"


def build_telemetry_hook_config(
    python_exe: Optional[str] = None,
    handler_path: Optional[Path] = None,
    timeout: int = 5,
) -> Dict[str, Any]:
    """Construct the standard telemetry lifecycle hook configuration dictionary."""
    py = python_exe or get_python_executable()
    hp = handler_path or get_hook_handler_path()

    return {
        "enabled": True,
        "PreInvocation": [
            {
                "type": "command",
                "command": format_command(py, hp, "PreInvocation"),
                "timeout": timeout,
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": format_command(py, hp, "Stop"),
                "timeout": timeout,
            }
        ],
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": format_command(py, hp, "PreToolUse"),
                        "timeout": timeout,
                    }
                ],
            }
        ],
    }


def _create_backup(file_path: Path, suffix: str = "backup") -> Path:
    """Create a timestamped backup of the given file in the same directory."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = file_path.parent / f"{file_path.name}.{suffix}-{ts}"
    backup_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def install_global_hooks(
    hooks_file: Optional[Path] = None,
    python_exe: Optional[str] = None,
    handler_path: Optional[Path] = None,
) -> Tuple[bool, str, Optional[Path], int]:
    """Install or update telemetry lifecycle hooks in global hooks.json.

    Returns:
        (success, message, backup_path, other_hooks_count)
    """
    target = hooks_file if hooks_file is not None else get_global_hooks_path()
    py = python_exe or get_python_executable()
    hp = handler_path or get_hook_handler_path()

    if not hp.is_file():
        return False, f"Hook handler file not found at: {hp}", None, 0

    desired_config = build_telemetry_hook_config(py, hp)
    other_hooks_count = 0
    backup_path = None

    # Ensure parent directory exists (~/.gemini/config)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        try:
            content = target.read_text(encoding="utf-8")
            existing_data = json.loads(content) if content.strip() else {}
        except Exception as e:
            # Malformed JSON: create emergency backup and fail safely
            malformed_backup = _create_backup(target, suffix="malformed")
            return (
                False,
                f"Existing hooks.json is malformed JSON ({e}). Preserved backup at {malformed_backup}",
                malformed_backup,
                0,
            )

        if not isinstance(existing_data, dict):
            malformed_backup = _create_backup(target, suffix="malformed")
            return (
                False,
                f"Existing hooks.json root is not a JSON object. Preserved backup at {malformed_backup}",
                malformed_backup,
                0,
            )

        other_hooks_count = len([k for k in existing_data.keys() if k != TELEMETRY_HOOK_KEY])

        # Check idempotency: if already installed with same config
        if existing_data.get(TELEMETRY_HOOK_KEY) == desired_config:
            return True, "Telemetry hooks already installed and up to date.", None, other_hooks_count

        # Backup before making changes
        backup_path = _create_backup(target, suffix="backup")

        # Merge telemetry hooks while preserving all other keys
        existing_data[TELEMETRY_HOOK_KEY] = desired_config
        target_data = existing_data
    else:
        target_data = {TELEMETRY_HOOK_KEY: desired_config}

    # Write formatted JSON safely
    try:
        target.write_text(json.dumps(target_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True, "Installation successful.", backup_path, other_hooks_count
    except Exception as e:
        return False, f"Failed to write hooks.json: {e}", backup_path, other_hooks_count


def uninstall_global_hooks(
    hooks_file: Optional[Path] = None,
) -> Tuple[bool, str, Optional[Path]]:
    """Remove only telemetry hooks from global hooks.json, preserving unrelated hooks.

    Returns:
        (success, message, backup_path)
    """
    target = hooks_file if hooks_file is not None else get_global_hooks_path()

    if not target.exists():
        return True, "No global hooks file found; nothing to uninstall.", None

    try:
        content = target.read_text(encoding="utf-8")
        existing_data = json.loads(content) if content.strip() else {}
    except Exception as e:
        return False, f"Cannot uninstall: hooks.json contains invalid JSON ({e}).", None

    if not isinstance(existing_data, dict) or TELEMETRY_HOOK_KEY not in existing_data:
        return True, "Telemetry hooks were not installed.", None

    # Create backup before uninstall modification
    backup_path = _create_backup(target, suffix="backup")

    # Remove only telemetry hook
    del existing_data[TELEMETRY_HOOK_KEY]

    # Write remaining hooks (or empty object if no other hooks exist)
    try:
        target.write_text(json.dumps(existing_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True, "Telemetry hooks removed successfully.", backup_path
    except Exception as e:
        return False, f"Failed to write updated hooks.json: {e}", backup_path


def get_global_hook_status(
    hooks_file: Optional[Path] = None,
    handler_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect and return comprehensive status of global Antigravity hooks configuration."""
    target = hooks_file if hooks_file is not None else get_global_hooks_path()
    hp = handler_path or get_hook_handler_path()
    py = get_python_executable()

    status: Dict[str, Any] = {
        "config_path": str(target),
        "config_exists": target.exists(),
        "json_valid": False,
        "telemetry_hook_installed": False,
        "handler_path": str(hp),
        "handler_valid": hp.is_file(),
        "python_executable": py,
        "other_hooks_count": 0,
        "hook_details": None,
    }

    if not target.exists():
        return status

    try:
        content = target.read_text(encoding="utf-8")
        data = json.loads(content) if content.strip() else {}
        status["json_valid"] = isinstance(data, dict)
        if status["json_valid"]:
            status["telemetry_hook_installed"] = TELEMETRY_HOOK_KEY in data
            status["other_hooks_count"] = len([k for k in data.keys() if k != TELEMETRY_HOOK_KEY])
            if status["telemetry_hook_installed"]:
                status["hook_details"] = data.get(TELEMETRY_HOOK_KEY)
    except Exception:
        status["json_valid"] = False

    return status
