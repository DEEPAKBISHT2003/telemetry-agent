"""CLI Interface for AI Telemetry Collector."""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Optional

from src.config.settings import ConfigurationError, load_settings
from src.core.collector import TelemetryCollector
from src.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS
from src.storage.sqlite_store import SQLiteEventStore


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

    db_path = Path(settings.local_database_path if settings else "./data/telemetry.db").resolve()
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
        db_path = str(Path("./data/telemetry.db").resolve())

    if not Path(db_path).is_file():
        print("No telemetry database found. Start the collector first.")
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


def cmd_sessions(args: argparse.Namespace) -> None:
    """List distinct Antigravity sessions and their repository correlation."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path("./data/telemetry.db").resolve())

    if not Path(db_path).is_file():
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

    print("\n" + f"{'SESSION ID':<38} | {'REPOSITORY':<20} | {'BRANCH':<18} | {'MODEL':<18} | {'AI RUNS':<7} | {'TOOLS':<6} | {'DURATION'}")
    print("-" * 125)

    for s in sessions:
        sid = s["session_id"]
        repo = s["repository_name"] or "unknown"
        branch = s["branch"] or "unknown"
        model = s["model"] or "unknown"
        ai_runs = s["ai_runs_count"]
        tools = s["tool_calls_count"]
        duration = f"{s['duration_seconds']}s"

        print(f"{sid:<38} | {repo:<20} | {branch:<18} | {model:<18} | {ai_runs:<7} | {tools:<6} | {duration}")
    print("-" * 125 + "\n")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show aggregated session, AI run, and tool usage summary grouped by repository."""
    try:
        settings = load_settings(env_file=args.env_file)
        db_path = str(settings.db_path)
    except Exception:
        db_path = str(Path("./data/telemetry.db").resolve())

    if not Path(db_path).is_file():
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
        print(f"Models Used   : {', '.join(data['models_used']) if data['models_used'] else 'none'}")
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
        db_path = Path("./data/telemetry.db").resolve()

    pid_file = db_path.parent / "collector.pid"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry-agent",
        description="Local AI and Engineering Telemetry Collector v0.1.0",
    )
    parser.add_argument("--env-file", type=str, default=None, help="Path to .env configuration file")

    subparsers = parser.add_subparsers(dest="command", help="Collector actions")

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

    # Stop command
    subparsers.add_parser("stop", help="Stop the running collector")

    return parser


def cli_entrypoint() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "start":
        cmd_start(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "events":
        cmd_events(args)
    elif args.command == "sessions":
        cmd_sessions(args)
    elif args.command == "summary":
        cmd_summary(args)
    elif args.command == "stop":
        cmd_stop(args)


if __name__ == "__main__":
    cli_entrypoint()
