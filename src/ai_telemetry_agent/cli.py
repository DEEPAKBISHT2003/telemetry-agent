"""CLI Interface for AI Telemetry Agent.

Provides commands to start, inspect, configure, and install the local AI telemetry collector.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Optional

from ai_telemetry_agent.config.settings import ConfigurationError, load_settings
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.integrations.hooks.installer import (
    get_global_hook_status,
    get_global_hooks_path,
    install_global_hooks,
    uninstall_global_hooks,
)
from ai_telemetry_agent.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS
from ai_telemetry_agent.storage.sqlite_store import SQLiteEventStore


def cmd_install(args: argparse.Namespace) -> None:
    """Setup and initialize telemetry collection on the local machine."""
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name
    os_name = platform.system() + " " + platform.release()

    print("\n" + "=" * 60)
    print("AI Telemetry Agent — Machine Setup & Installation")
    print("=" * 60)
    print(f"Host / OS      : {platform.node()} ({os_name})")
    print(f"Current User   : {user_name}")
    print(f"Python Runtime : {sys.executable} (v{platform.python_version()})")

    # 1. Initialize configuration and persistent identity
    try:
        settings = load_settings(
            env_file=getattr(args, "env_file", None),
            member_id=getattr(args, "member", None),
            device_id=getattr(args, "device", None),
        )
    except ConfigurationError as ce:
        print(f"\nERROR: Failed to load configuration: {ce}", file=sys.stderr)
        sys.exit(1)

    print(f"Device ID      : {settings.device_id}")
    print(f"Member ID      : {settings.member_id}")
    print(f"App Data Path  : {settings.app_data_dir}")
    print(f"Storage Path   : {settings.db_path}")

    # 2. Initialize local storage
    try:
        store = SQLiteEventStore(settings.db_path)
        print("Local Storage  : INITIALIZED (SQLite)")
    except Exception as e:
        print(f"\nERROR: Failed to initialize local storage: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Install global Antigravity hook
    target_hooks_file = Path(args.config) if getattr(args, "config", None) else None
    print("\nConfiguring Global Antigravity Lifecycle Hooks...")
    success, msg, backup_path, other_count = install_global_hooks(
        hooks_file=target_hooks_file,
        python_exe=getattr(args, "python", None),
    )

    if not success:
        print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    if backup_path:
        print(f"Backup created : {backup_path}")
    print(f"Hook Status    : {msg}")
    print(f"Other Hooks    : {other_count} preserved")

    print("\n" + "=" * 60)
    print("Installation Complete!")
    print("Telemetry is now configured to capture Antigravity sessions globally.")
    print("You can verify status anytime with: telemetry-agent status")
    print("=" * 60 + "\n")


def cmd_start(args: argparse.Namespace) -> None:
    """Start the telemetry collector."""
    try:
        settings = load_settings(
            env_file=args.env_file,
            member_id=args.member,
            device_id=args.device,
        )
    except ConfigurationError as ce:
        print(f"Error: {ce}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 50)
    print(f"Telemetry Collector v{settings.collector_version}")
    print("=" * 50)
    print(f"Member ID  : {settings.member_id}")
    print(f"Device ID  : {settings.device_id}")
    print(f"Storage    : {settings.db_path}")
    print(f"Antigravity: {ANTIGRAVITY_AI_TELEMETRY_STATUS}")
    print(f"Status     : RUNNING")
    print("=" * 50 + "\n")
    print("Press Ctrl+C to stop the collector.\n")

    collector = TelemetryCollector(settings)
    collector.run_loop(poll_interval_seconds=1.0)


def cmd_status(args: argparse.Namespace) -> None:
    """Show collector status and event statistics."""
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigurationError:
        settings = None

    db_path = Path(settings.local_database_path if settings else str(Path.home() / ".telemetry_agent" / "data" / "telemetry.db")).resolve()
    pid_file = db_path.parent / "collector.pid"

    is_running = False
    pid = None
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            is_running = True
        except Exception:
            pass

    print("\n" + "=" * 50)
    print("Telemetry Collector Status")
    print("=" * 50)
    print(f"Process Status     : {'RUNNING (PID ' + str(pid) + ')' if is_running else 'STOPPED'}")
    if settings:
        print(f"Configured Member  : {settings.member_id}")
        print(f"Configured Device  : {settings.device_id}")
        print(f"Collector Version  : {settings.collector_version}")
    print(f"Database Path      : {db_path}")
    print(f"Antigravity Status : {ANTIGRAVITY_AI_TELEMETRY_STATUS}")

    if db_path.is_file():
        store = SQLiteEventStore(str(db_path))
        total_events = store.get_count()
        print(f"Total Stored Events: {total_events}")
        recent = store.get_events(limit=1, descending=True)
        if recent:
            print(f"Last Event Time    : {recent[0].timestamp} ({recent[0].event_type})")
    else:
        print("Total Stored Events: 0 (Database not created yet)")
    print("=" * 50 + "\n")


def cmd_events(args: argparse.Namespace) -> None:
    """Inspect locally collected telemetry events."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path.home() / ".telemetry_agent" / "data" / "telemetry.db")

    if not Path(db_path).is_file():
        # Fallback to local ./data/telemetry.db if exists
        fallback = Path("./data/telemetry.db").resolve()
        if fallback.is_file():
            db_path = str(fallback)
        else:
            print("No telemetry database found. Start the collector or run 'telemetry-agent install' first.")
            return

    store = SQLiteEventStore(db_path)
    events = store.get_events(
        limit=args.last,
        event_type=args.type,
        member_id=args.member,
        repository_name=args.repo,
        session_id=args.session,
        descending=False if args.asc else True,
    )

    if not events:
        print("No events found matching query criteria.")
        return

    if args.json:
        print(json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False))
        return

    # Table format output
    print("\n" + f"{'TIME':<22} | {'EVENT':<26} | {'MEMBER':<8} | {'DEVICE':<10} | {'SOURCE':<12} | {'DETAILS'}")
    print("-" * 110)

    for ev in events:
        ts = ev.timestamp
        etype = ev.event_type
        mid = ev.identity.member_id
        dev = ev.collector.device_id

        # Determine source
        if etype.startswith("git_"):
            source = "git"
            details = f"repo={ev.payload.get('repository', '')} branch={ev.payload.get('branch', '')}"
            if "commit_sha" in ev.payload:
                details += f" sha={ev.payload.get('commit_sha')[:7]}"
        elif etype.startswith("ai_") or etype in {"tool_call", "agent_started", "agent_completed"}:
            source = "antigravity"
            details = f"provider={ev.payload.get('provider')} model={ev.payload.get('model')}"
            if ev.payload.get("tool_name"):
                details = f"tool={ev.payload.get('tool_name')} " + details
            if ev.context.repository and ev.context.repository.name:
                details += f" repo={ev.context.repository.name}"
        else:
            source = "system"
            details = f"status={ev.payload.get('status', ev.payload.get('process_status', ''))}"
            if "uptime_seconds" in ev.payload:
                details += f" uptime={ev.payload['uptime_seconds']}s"

        print(f"{ts:<22} | {etype:<26} | {mid:<8} | {dev:<10} | {source:<12} | {details}")
    print("-" * 110 + "\n")


def cmd_session(args: argparse.Namespace) -> None:
    """Show detailed breakdown of a single session, including all models used and run counts."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path.home() / ".telemetry_agent" / "data" / "telemetry.db")

    if not Path(db_path).is_file():
        fallback = Path("./data/telemetry.db").resolve()
        if fallback.is_file():
            db_path = str(fallback)
        else:
            print("No telemetry database found.")
            return

    store = SQLiteEventStore(db_path)
    sessions = store.get_sessions(session_id=args.id, limit=1)

    if not sessions:
        print(f"No session found with ID: {args.id}")
        return

    s = sessions[0]

    if args.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return

    print("\n" + "=" * 50)
    print(f"Session: {s['session_id']}")
    print("=" * 50)
    print(f"Repository : {s['repository_name'] or 'unknown'}")
    print(f"Branch     : {s['branch'] or 'unknown'}")
    print(f"Member ID  : {s['member_id']}")
    print(f"Device ID  : {s['device_id']}")
    print(f"Started At : {s['started_at']}")
    print(f"Last Active: {s['last_activity_at']}")
    print(f"Duration   : {s['duration_seconds']}s")
    print(f"AI Runs    : {s['ai_runs_count']}")
    print(f"Tool Calls : {s['tool_calls_count']}\n")

    print("Models Used:")
    model_counts = s.get("model_counts", {})
    if model_counts:
        for model_name in sorted(model_counts.keys()):
            runs = model_counts[model_name]
            print(f"  {model_name:<30} {runs:>3} runs")
    elif s.get("models"):
        for model_name in s["models"]:
            print(f"  {model_name}")
    else:
        print("  none")

    print("\nTools Used:")
    tools = s.get("tools_used", [])
    if tools:
        print(f"  {', '.join(tools)}")
    else:
        print("  none")
    print("=" * 50 + "\n")


def cmd_sessions(args: argparse.Namespace) -> None:
    """List distinct Antigravity sessions and their repository correlation."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path.home() / ".telemetry_agent" / "data" / "telemetry.db")

    if not Path(db_path).is_file():
        fallback = Path("./data/telemetry.db").resolve()
        if fallback.is_file():
            db_path = str(fallback)
        else:
            print("No telemetry database found.")
            return

    store = SQLiteEventStore(db_path)
    sessions = store.get_sessions(repository_name=args.repo, member_id=args.member, limit=args.limit)

    if not sessions:
        print("No session records found.")
        return

    if args.json:
        print(json.dumps(sessions, indent=2, ensure_ascii=False))
        return

    print("\n" + f"{'SESSION ID':<38} | {'REPOSITORY':<16} | {'BRANCH':<12} | {'MODELS':<40} | {'AI RUNS':<7} | {'TOOLS':<6} | {'DURATION'}")
    print("-" * 135)

    for s in sessions:
        sid = s["session_id"]
        repo = s["repository_name"] or "unknown"
        branch = s["branch"] or "unknown"
        models_str = ", ".join(s.get("models", [])) if s.get("models") else (s.get("model") or "unknown")
        ai_runs = s["ai_runs_count"]
        tools = s["tool_calls_count"]
        duration = f"{s['duration_seconds']}s"

        print(f"{sid:<38} | {repo:<16} | {branch:<12} | {models_str:<40} | {ai_runs:<7} | {tools:<6} | {duration}")
    print("-" * 135 + "\n")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show aggregated session, AI run, and tool usage summary grouped by repository."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path.home() / ".telemetry_agent" / "data" / "telemetry.db")

    if not Path(db_path).is_file():
        fallback = Path("./data/telemetry.db").resolve()
        if fallback.is_file():
            db_path = str(fallback)
        else:
            print("No telemetry database found.")
            return

    store = SQLiteEventStore(db_path)
    summary = store.get_repository_summary(repository_name=args.repo)

    if not summary:
        print("No telemetry records found for summary.")
        return

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    print("\n" + "=" * 60)
    print("REPOSITORY TELEMETRY SUMMARY")
    print("=" * 60)

    for rname, data in summary.items():
        print(f"\nRepository    : {rname}")
        print(f"Total Sessions: {data['total_sessions']}")
        print(f"Total AI Runs : {data['total_ai_runs']}")
        print(f"Tool Calls    : {data['total_tool_calls']}")
        print(f"Branches      : {', '.join(data['branches']) if data['branches'] else 'none'}")
        
        # Format models with run counts if available
        model_counts = data.get("model_counts", {})
        if model_counts:
            models_display = ", ".join(f"{m} ({cnt})" for m, cnt in sorted(model_counts.items()))
        elif data.get("models_used"):
            models_display = ", ".join(data["models_used"])
        else:
            models_display = "none"
        print(f"Models Used   : {models_display}")

        print(f"Tools Used    : {', '.join(data['tools_used']) if data['tools_used'] else 'none'}")
        print(f"Last Activity : {data['last_active']}")
        print("-" * 40)
    print("=" * 60 + "\n")


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running collector instance via pid file."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = settings.db_path
    except Exception:
        db_path = Path.home() / ".telemetry_agent" / "data" / "telemetry.db"

    pid_file = db_path.parent / "collector.pid"
    if not pid_file.exists():
        # check local ./data
        pid_file = Path("./data/collector.pid").resolve()

    if not pid_file.exists():
        print("No running collector detected.")
        return

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        if sys.platform == "win32":
            os.system(f"taskkill /PID {pid} /F >nul 2>&1")
        else:
            os.kill(pid, 15)
        pid_file.unlink(missing_ok=True)
        print(f"Stopped collector process (PID {pid}).")
    except Exception as ex:
        print(f"Failed to stop collector: {ex}")


def cmd_hook_status(args: argparse.Namespace) -> None:
    """Display global Antigravity hook configuration and verification status."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    status = get_global_hook_status(hooks_file=target_file)

    # Check if collector process is running
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = settings.db_path
    except Exception:
        db_path = Path.home() / ".telemetry_agent" / "data" / "telemetry.db"

    pid_file = db_path.parent / "collector.pid"
    collector_running = pid_file.is_file()

    print("\n" + "=" * 50)
    print("Global Antigravity Hook Status")
    print("=" * 50)
    print(f"Config: \n{status['config_path']}\n")
    print(f"Config exists           : {'YES' if status['config_exists'] else 'NO'}")
    print(f"JSON valid              : {'YES' if status['json_valid'] else 'NO'}")
    print(f"Telemetry hook installed: {'YES' if status['telemetry_hook_installed'] else 'NO'}")
    print(f"Telemetry handler       : {'VALID' if status['handler_valid'] else 'INVALID'}")
    print(f"Handler Path            : {status['handler_path']}")
    print(f"Python Executable       : {status['python_executable']}")
    print(f"Other hooks detected    : {status['other_hooks_count']}")
    print(f"Collector               : {'RUNNING' if collector_running else 'STOPPED'}")
    print("=" * 50 + "\n")


def cmd_install_hook(args: argparse.Namespace) -> None:
    """Install or update global Antigravity lifecycle hooks."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    pre_status = get_global_hook_status(hooks_file=target_file)
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Hook Installer")
    print("=" * 50)
    print(f"User        : {user_name}")
    print(f"Agent       : ai-telemetry-agent")
    print(f"Global hooks: {pre_status['config_path']}")
    print(f"Existing hooks detected         : {pre_status['other_hooks_count']}")
    print(f"Telemetry hooks already installed: {'Yes' if pre_status['telemetry_hook_installed'] else 'No'}\n")

    print("Installing telemetry hooks...")
    success, msg, backup_path, other_count = install_global_hooks(
        hooks_file=target_file,
        python_exe=args.python if getattr(args, "python", None) else None,
    )

    if not success:
        print(f"\nERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    if backup_path:
        print(f"Backup created:\n{backup_path}\n")

    print("Installation successful.\n")
    print("Telemetry will now capture Antigravity activity across workspaces.")
    print("=" * 50 + "\n")


def cmd_uninstall_hook(args: argparse.Namespace) -> None:
    """Uninstall telemetry hooks from global Antigravity configuration."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name
    hooks_path = target_file if target_file else get_global_hooks_path()

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Hook Uninstaller")
    print("=" * 50)
    print(f"User        : {user_name}")
    print(f"Global hooks: {hooks_path}\n")
    print("Removing telemetry hooks...")

    success, msg, backup_path = uninstall_global_hooks(hooks_file=target_file)
    if not success:
        print(f"\nERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    if backup_path:
        print(f"Backup created:\n{backup_path}\n")

    print(f"{msg}")
    print("=" * 50 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry-agent",
        description="Local AI and Engineering Telemetry Agent v0.1.0",
    )
    parser.add_argument("--env-file", type=str, default=None, help="Path to .env configuration file")

    subparsers = parser.add_subparsers(dest="command", help="Agent actions")

    # Install command (Step 5)
    p_install = subparsers.add_parser("install", help="Setup and prepare machine for AI telemetry collection")
    p_install.add_argument("--member", type=str, default=None, help="Configure member identity (default: system username)")
    p_install.add_argument("--device", type=str, default=None, help="Configure device ID (default: persistent UUID)")
    p_install.add_argument("--config", type=str, default=None, help="Custom hooks.json path")
    p_install.add_argument("--python", type=str, default=None, help="Custom python executable path")

    # Start command
    p_start = subparsers.add_parser("start", help="Start the telemetry collector")
    p_start.add_argument("--member", type=str, default=None, help="Override MEMBER_ID")
    p_start.add_argument("--device", type=str, default=None, help="Override DEVICE_ID")

    # Status command
    subparsers.add_parser("status", help="Display collector running status and stats")

    # Events command
    p_events = subparsers.add_parser("events", help="Inspect collected events")
    p_events.add_argument("--last", type=int, default=20, help="Number of recent events to display (default: 20)")
    p_events.add_argument("--type", type=str, default=None, help="Filter by event type")
    p_events.add_argument("--member", type=str, default=None, help="Filter by member ID")
    p_events.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_events.add_argument("--session", type=str, default=None, help="Filter by session ID")
    p_events.add_argument("--asc", action="store_true", help="Sort in ascending order (oldest first)")
    p_events.add_argument("--json", action="store_true", help="Output raw JSON array")

    # Single session detail command
    p_single_sess = subparsers.add_parser("session", help="Show detailed breakdown of a single session")
    p_single_sess.add_argument("--id", type=str, required=True, help="Session ID to inspect")
    p_single_sess.add_argument("--json", action="store_true", help="Output raw JSON")

    # Sessions command
    p_sess = subparsers.add_parser("sessions", help="List Antigravity sessions and correlated repositories")
    p_sess.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_sess.add_argument("--member", type=str, default=None, help="Filter by member ID")
    p_sess.add_argument("--limit", type=int, default=50, help="Max sessions to display (default: 50)")
    p_sess.add_argument("--json", action="store_true", help="Output raw JSON array")

    # Summary command
    p_sum = subparsers.add_parser("summary", help="Show summary metrics aggregated by repository")
    p_sum.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_sum.add_argument("--json", action="store_true", help="Output raw JSON")

    # Hook management commands
    p_hstatus = subparsers.add_parser("hook-status", help="Display global Antigravity hook status")
    p_hstatus.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    p_hinstall = subparsers.add_parser("install-hook", help="Install global Antigravity lifecycle hooks")
    p_hinstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")
    p_hinstall.add_argument("--python", type=str, default=None, help="Custom python executable path")

    p_huninstall = subparsers.add_parser("uninstall-hook", help="Uninstall global Antigravity lifecycle hooks")
    p_huninstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    # Stop command
    subparsers.add_parser("stop", help="Stop the running collector")

    return parser


def cli_entrypoint() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "install":
        cmd_install(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "events":
        cmd_events(args)
    elif args.command == "session":
        cmd_session(args)
    elif args.command == "sessions":
        cmd_sessions(args)
    elif args.command == "summary":
        cmd_summary(args)
    elif args.command == "hook-status":
        cmd_hook_status(args)
    elif args.command == "install-hook":
        cmd_install_hook(args)
    elif args.command == "uninstall-hook":
        cmd_uninstall_hook(args)
    elif args.command == "stop":
        cmd_stop(args)


if __name__ == "__main__":
    cli_entrypoint()
