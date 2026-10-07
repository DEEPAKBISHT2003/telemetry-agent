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

from ai_telemetry_agent.config.settings import (
    ConfigurationError,
    Settings,
    get_or_create_device_id,
    get_user_agent_home,
    load_settings,
)
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.identity import (
    DeveloperIdentity,
    detect_hostname,
    detect_os_username,
    enroll,
    get_identity,
    is_enrolled,
)
from ai_telemetry_agent.integrations.hooks.installer import (
    get_global_hook_status,
    get_global_hooks_path,
    install_global_hooks,
    uninstall_global_hooks,
)
from ai_telemetry_agent.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS
from ai_telemetry_agent.storage.base import EventStore
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore


def _safe_print(text: str = "", **kwargs) -> None:
    """Print text safely across platforms and Windows code pages without crashing."""
    try:
        print(text, **kwargs)
    except UnicodeEncodeError:
        ascii_text = text.replace("✓", "[OK]").replace("✗", "[X]")
        try:
            print(ascii_text, **kwargs)
        except Exception:
            encoded = text.encode("ascii", errors="replace").decode("ascii")
            print(encoded, **kwargs)


def _get_active_store(settings: Optional[Settings] = None) -> EventStore:
    """Resolve active local telemetry store: JSONLEventStore."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None

    data_dir = settings.data_dir if settings else (get_user_agent_home() / "data").resolve()
    return JSONLEventStore(data_dir)


def cmd_install(args: argparse.Namespace) -> None:
    """Setup, enroll, and initialize telemetry collection on the local machine."""
    user_home = get_user_agent_home()
    existing_ident = get_identity(custom_home=user_home)

    _safe_print("\n" + "=" * 40)
    _safe_print("       AI Telemetry Agent Setup")
    _safe_print("=" * 40)

    if existing_ident:
        _safe_print("✓ Telemetry Agent already configured")
        _safe_print(f"Developer: {existing_ident.member_name}")
        _safe_print(f"Device: {existing_ident.hostname}")
        _safe_print(f"Device ID: {existing_ident.device_id}")
        _safe_print(f"Member ID: {existing_ident.member_id}\n")
    else:
        # First-time enrollment
        _safe_print("")
        name_input = getattr(args, "name", None)
        if name_input and name_input.strip():
            member_name = name_input.strip()
            _safe_print(f"Enter your name: {member_name}")
        else:
            if not sys.stdin.isatty():
                default_name = detect_os_username() or "Developer"
                member_name = default_name.strip()
                _safe_print(f"Enter your name: {member_name}")
            else:
                while True:
                    try:
                        raw_name = input("Enter your name: ")
                    except (EOFError, KeyboardInterrupt):
                        _safe_print("\nSetup cancelled.")
                        sys.exit(1)
                    if raw_name and raw_name.strip():
                        member_name = raw_name.strip()
                        break
                    _safe_print("Error: Name cannot be empty. Please enter a valid developer name.")

        hostname = detect_hostname()
        device_id = getattr(args, "device", None) or get_or_create_device_id(custom_home=user_home)

        _safe_print("\nDetected device:")
        _safe_print(f"  Hostname: {hostname}")
        _safe_print(f"  Device ID: {device_id}")

        _safe_print("\nDeveloper:")
        _safe_print(f"  {member_name}\n")

        # Confirmation
        auto_yes = getattr(args, "yes", False) or not sys.stdin.isatty() or getattr(args, "name", None) is not None
        if not auto_yes:
            try:
                confirm = input("Save this identity? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                _safe_print("\nSetup cancelled.")
                sys.exit(1)
            if confirm and confirm not in ("y", "yes"):
                _safe_print("Setup cancelled by user.")
                sys.exit(1)

        try:
            ident = enroll(
                member_name=member_name,
                device_id=device_id,
                hostname=hostname,
                custom_home=user_home,
            )
            _safe_print("✓ Developer identity saved")
        except Exception as e:
            _safe_print(f"ERROR: Failed to save developer identity: {e}", file=sys.stderr)
            sys.exit(1)

    # Initialize configuration & storage
    try:
        settings = load_settings(
            env_file=getattr(args, "env_file", None),
            member_id=getattr(args, "member", None),
            device_id=getattr(args, "device", None),
        )
        store = JSONLEventStore(settings.data_dir)
    except Exception as e:
        _safe_print(f"\nERROR: Failed to initialize local storage: {e}", file=sys.stderr)
        sys.exit(1)

    # Install global Antigravity hook
    target_hooks_file = Path(args.config) if getattr(args, "config", None) else None
    success, msg, backup_path, other_count = install_global_hooks(
        hooks_file=target_hooks_file,
        python_exe=getattr(args, "python", None),
    )

    if not success:
        _safe_print(f"ERROR: Failed to configure hooks: {msg}", file=sys.stderr)
        sys.exit(1)

    _safe_print("✓ Antigravity hooks installed")
    _safe_print("✓ Telemetry agent configured\n")


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

    data_dir = settings.data_dir if settings else (get_user_agent_home() / "data").resolve()
    pid_file = data_dir / "collector.pid"

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
        if settings.member_name:
            print(f"Developer Name     : {settings.member_name}")
        print(f"Configured Member  : {settings.member_id}")
        print(f"Configured Device  : {settings.device_id}")
        if settings.hostname:
            print(f"Hostname           : {settings.hostname}")
        print(f"Collector Version  : {settings.collector_version}")
    print(f"Storage Directory  : {data_dir}")
    print(f"Storage Format     : Date-based JSONL (telemetry-YYYY-MM-DD.jsonl)")
    print(f"Antigravity Status : {ANTIGRAVITY_AI_TELEMETRY_STATUS}")

    store = _get_active_store(settings)
    total_events = store.get_count()
    print(f"Total Stored Events: {total_events}")
    recent = store.get_events(limit=1, descending=True)
    if recent:
        print(f"Last Event Time    : {recent[0].timestamp} ({recent[0].event_type})")
    print("=" * 50 + "\n")


def cmd_events(args: argparse.Namespace) -> None:
    """Inspect locally collected telemetry events."""
    try:
        settings = load_settings(env_file=args.env_file)
    except Exception:
        settings = None

    store = _get_active_store(settings)
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
    except Exception:
        settings = None

    store = _get_active_store(settings)
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
    except Exception:
        settings = None

    store = _get_active_store(settings)
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
    except Exception:
        settings = None

    store = _get_active_store(settings)
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
        data_dir = settings.data_dir
    except Exception:
        data_dir = Path.home() / ".telemetry_agent" / "data"

    pid_file = data_dir / "collector.pid"
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

    # Install command
    p_install = subparsers.add_parser("install", help="Setup, enroll developer, and prepare machine for AI telemetry collection")
    p_install.add_argument("--name", "-n", type=str, default=None, help="Developer name for non-interactive enrollment")
    p_install.add_argument("-y", "--yes", action="store_true", help="Automatic yes to enrollment confirmation")
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
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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
