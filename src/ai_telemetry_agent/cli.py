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

# Prevent sys.path[0] package directory from shadowing Python standard library 'logging'
_pkg_dir = str(Path(__file__).resolve().parent)
_src_dir = str(Path(__file__).resolve().parent.parent)
while _pkg_dir in sys.path:
    sys.path.remove(_pkg_dir)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from ai_telemetry_agent.config.settings import (
    ConfigurationError,
    Settings,
    get_or_create_device_id,
    get_user_agent_home,
    load_settings,
)
from ai_telemetry_agent.core.collector import (
    TelemetryCollector,
    extract_telemetry_source_from_cmdline,
    get_collector_status,
    get_running_process_cmdline,
    read_collector_metadata,
)
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
from ai_telemetry_agent.integrations.hooks.kiro_installer import (
    get_kiro_hook_status,
    install_kiro_hooks,
    uninstall_kiro_hooks,
)
from ai_telemetry_agent.attribution.aggregator import TicketAggregator
from ai_telemetry_agent.attribution.detector import detect_branch_ticket, validate_ticket_key
from ai_telemetry_agent.attribution.jira_adapter import GenericJiraRestAdapter, NullJiraAdapter
from ai_telemetry_agent.attribution.models import (
    JiraIssueNotFoundError,
    JiraUnconfiguredError,
    JiraUnreachableError,
)
from ai_telemetry_agent.attribution.store import AttributionStore
from ai_telemetry_agent.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS
from ai_telemetry_agent.sources.kiro import KIRO_AI_TELEMETRY_STATUS
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


def _get_attribution_store(settings: Optional[Settings] = None) -> AttributionStore:
    """Resolve active persistent attribution store."""
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None

    data_dir = settings.data_dir if settings else (get_user_agent_home() / "data").resolve()
    return AttributionStore(data_dir)


def _get_jira_adapter(settings: Optional[Settings] = None):
    """Instantiate configured Jira API client adapter or NullJiraAdapter."""
    if settings and settings.jira_base_url:
        return GenericJiraRestAdapter(
            base_url=settings.jira_base_url,
            auth_type=settings.jira_auth_type,
            api_token=settings.jira_api_token,
            username=settings.jira_username,
        )
    base_url = os.environ.get("JIRA_BASE_URL")
    if base_url:
        return GenericJiraRestAdapter(
            base_url=base_url,
            auth_type=os.environ.get("JIRA_AUTH_TYPE", "pat"),
            api_token=os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_PAT"),
            username=os.environ.get("JIRA_USERNAME"),
        )
    return NullJiraAdapter()


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
    overrides = {}
    if getattr(args, "source", None):
        overrides["TELEMETRY_SOURCE"] = args.source

    try:
        settings = load_settings(
            env_file=args.env_file,
            member_id=args.member,
            device_id=args.device,
            overrides=overrides if overrides else None,
        )
    except ConfigurationError as ce:
        print(f"Error: {ce}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 50)
    print(f"Telemetry Collector v{settings.collector_version}")
    print("=" * 50)
    print(f"Member ID  : {settings.member_id}")
    print(f"Device ID  : {settings.device_id}")
    print(f"Storage    : {settings.data_dir}")
    print(f"Source Mode: {settings.telemetry_source}")
    print(f"Antigravity: {ANTIGRAVITY_AI_TELEMETRY_STATUS}")
    print(f"Kiro AI    : {KIRO_AI_TELEMETRY_STATUS}")
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
    is_running, pid = get_collector_status(data_dir, cleanup_stale=True)

    print("\n" + "=" * 50)
    print("Telemetry Collector Status")
    print("=" * 50)
    print(f"Process Status     : {'RUNNING (PID ' + str(pid) + ')' if is_running else 'STOPPED'}")

    # Determine effective telemetry source:
    # 1. If collector is running, check metadata file written by running instance.
    # 2. Check running process command line flags (--source).
    # 3. Fall back to settings.telemetry_source (configuration file or default).
    effective_source = getattr(settings, "telemetry_source", "antigravity") if settings else "antigravity"
    if is_running and pid:
        meta = read_collector_metadata(data_dir)
        if meta.get("telemetry_source"):
            effective_source = meta["telemetry_source"]
        else:
            proc_cmd = get_running_process_cmdline(pid)
            extracted = extract_telemetry_source_from_cmdline(proc_cmd)
            if extracted:
                effective_source = extracted

    if settings:
        if settings.member_name:
            print(f"Developer Name     : {settings.member_name}")
        print(f"Configured Member  : {settings.member_id}")
        print(f"Configured Device  : {settings.device_id}")
        if settings.hostname:
            print(f"Hostname           : {settings.hostname}")
        print(f"Collector Version  : {settings.collector_version}")
        print(f"Telemetry Source   : {effective_source}")
    print(f"Storage Directory  : {data_dir}")
    print(f"Storage Format     : Date-based JSONL (telemetry-YYYY-MM-DD.jsonl)")
    print(f"Antigravity Status : {ANTIGRAVITY_AI_TELEMETRY_STATUS}")
    print(f"Kiro AI Status     : {KIRO_AI_TELEMETRY_STATUS}")

    attr_store = _get_attribution_store(settings)
    active_attr = attr_store.get_active_attribution()
    proposed = attr_store.get_proposed_switch()
    if active_attr:
        print(f"Active Jira Ticket : {active_attr.issue_key} ({active_attr.confirmation_state}, source={active_attr.source})")
    else:
        print(f"Active Jira Ticket : UNASSIGNED")
    if proposed:
        print(f"Proposed Switch    : {proposed} (pending confirmation via 'jira confirm')")

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
        elif etype == "kiro_detected" or (ev.payload and ev.payload.get("capture_method", "").startswith("kiro_")):
            source = "kiro"
            details = f"provider={ev.payload.get('provider')} model={ev.payload.get('model')}"
            if ev.payload.get("tool_name"):
                details = f"tool={ev.payload.get('tool_name')} " + details
            if ev.payload.get("credits") is not None:
                details += f" credits={ev.payload.get('credits')}"
            if ev.context.repository and ev.context.repository.name:
                details += f" repo={ev.context.repository.name}"
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
    attr_store = _get_attribution_store(settings)
    sessions = store.get_sessions(session_id=args.id, limit=1)

    if not sessions:
        print(f"No session found with ID: {args.id}")
        return

    s = sessions[0]
    rname = s.get("repository_name")
    started = s.get("started_at")
    attr = attr_store.find_attribution_for_event(repo_name=rname, timestamp=started)
    ticket_key = attr.issue_key if attr else "UNASSIGNED"
    s["attributed_ticket"] = ticket_key

    if args.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return

    print("\n" + "=" * 50)
    print(f"Session: {s['session_id']}")
    print("=" * 50)
    print(f"Repository : {s['repository_name'] or 'unknown'}")
    print(f"Branch     : {s['branch'] or 'unknown'}")
    print(f"Jira Ticket: {ticket_key}")
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

    if s.get("has_credit_data"):
        print("\nCredit Usage (Kiro Metering):")
        unit = s.get("credit_unit") or "credit"
        print(f"  Total Credits         : {s.get('total_credits', 0.0):.4f} {unit}s")

    if s.get("has_token_data"):
        print("\nToken Usage (Authoritative):")
        print(f"  Uncached Input Tokens : {s.get('input_tokens', 0):,}")
        print(f"  Cached Read Tokens    : {s.get('cache_read_tokens', 0):,}")
        print(f"  Output Tokens         : {s.get('output_tokens', 0):,}")
        print(f"  Total Tokens          : {s.get('total_tokens', 0):,}")

    print("=" * 50 + "\n")


def cmd_sessions(args: argparse.Namespace) -> None:
    """List distinct AI sessions and their repository correlation."""
    try:
        settings = load_settings(env_file=args.env_file)
    except Exception:
        settings = None

    store = _get_active_store(settings)
    attr_store = _get_attribution_store(settings)
    sessions = store.get_sessions(repository_name=args.repo, member_id=args.member, limit=args.limit)

    # Attach attributed ticket to each session and apply ticket filter if specified
    ticket_filter = getattr(args, "ticket", None)
    filtered = []
    for s in sessions:
        rname = s.get("repository_name")
        started = s.get("started_at")
        attr = attr_store.find_attribution_for_event(repo_name=rname, timestamp=started)
        ticket_key = attr.issue_key if attr else "UNASSIGNED"
        s["attributed_ticket"] = ticket_key
        if ticket_filter and ticket_key.upper() != ticket_filter.upper():
            continue
        filtered.append(s)
    sessions = filtered

    if not sessions:
        print("No session records found.")
        return

    if args.json:
        print(json.dumps(sessions, indent=2, ensure_ascii=False))
        return

    print("\n" + f"{'SESSION ID':<38} | {'TICKET':<10} | {'REPOSITORY':<16} | {'BRANCH':<12} | {'MODELS':<30} | {'RUNS':<5} | {'CREDITS':<10} | {'DURATION'}")
    print("-" * 150)

    for s in sessions:
        sid = s["session_id"]
        tkt = s.get("attributed_ticket") or "-"
        repo = s["repository_name"] or "unknown"
        branch = s["branch"] or "unknown"
        models_str = ", ".join(s.get("models", [])) if s.get("models") else (s.get("model") or "unknown")
        ai_runs = s["ai_runs_count"]
        credits_str = f"{s.get('total_credits', 0.0):.2f}" if s.get("has_credit_data") else "-"
        duration = f"{s['duration_seconds']}s"

        print(f"{sid:<38} | {tkt:<10} | {repo:<16} | {branch:<12} | {models_str:<30} | {ai_runs:<5} | {credits_str:<10} | {duration}")
    print("-" * 150 + "\n")


def cmd_summary(args: argparse.Namespace) -> None:
    """Show aggregated session, AI run, tool usage, and credit summary grouped by repository or Jira ticket."""
    if getattr(args, "by_ticket", False) or getattr(args, "ticket", None):
        args.jira_command = "summary"
        cmd_jira(args)
        return

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
        if data.get("has_credit_data"):
            print(f"Total Credits : {data['total_credits']:.4f}")
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
def cmd_jira(args: argparse.Namespace) -> None:
    """Handle Jira ticket attribution, synchronization, and reporting commands."""
    try:
        settings = load_settings(env_file=getattr(args, "env_file", None))
    except Exception:
        settings = None

    attr_store = _get_attribution_store(settings)
    action = getattr(args, "jira_command", None)

    if not action:
        _safe_print("Error: Jira action required. Run 'telemetry-agent jira --help' for available commands.")
        sys.exit(1)

    repo_arg = getattr(args, "repo", None)

    if action == "select":
        jira_adapter = _get_jira_adapter(settings)
        allow_unverified = getattr(args, "allow_unverified", False)

        try:
            attr = attr_store.select_ticket(
                args.ticket,
                repo_path=repo_arg,
                source="manual",
                jira_adapter=jira_adapter,
                allow_unverified=allow_unverified,
            )
            val_status = getattr(attr, "validation_status", "unverified")
            if val_status == "verified":
                _safe_print(f"\n✓ Jira ticket '{attr.issue_key}' verified and selected for repository '{attr.repository_name}'.")
            elif allow_unverified:
                if jira_adapter.is_configured():
                    _safe_print(f"\n! Warning: Jira ticket '{attr.issue_key}' could not be verified against Jira.")
                else:
                    _safe_print(f"\n! Warning: Jira integration is unconfigured. Ticket '{attr.issue_key}' cannot be verified and may not exist.")
                _safe_print(f"  Proceeding under explicit offline policy (--allow-unverified).")
                _safe_print(f"\n✓ Jira ticket '{attr.issue_key}' selected for repository '{attr.repository_name}'.")
            else:
                _safe_print(f"\n✓ Jira ticket '{attr.issue_key}' selected for repository '{attr.repository_name}'.")

            _safe_print(f"  Repository Root: {attr.repository_root}")
            _safe_print(f"  Branch         : {attr.branch or 'unknown'}")
            _safe_print(f"  Validation     : {val_status}{' (' + attr.validation_message + ')' if getattr(attr, 'validation_message', None) else ''}")
            if getattr(attr, "issue_title", None):
                _safe_print(f"  Issue Title    : {attr.issue_title}")
            if getattr(attr, "issue_status", None):
                _safe_print(f"  Issue Status   : {attr.issue_status}")
            _safe_print(f"  Confirmation   : {attr.confirmation_state} (user selected)")
            _safe_print(f"  Source         : {attr.source}")
            _safe_print(f"  Active Since   : {attr.start_time}\n")

        except JiraIssueNotFoundError as infe:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Jira ticket selection rejected: Issue '{infe.key}' was not found on Jira (HTTP 404 Not Found).", file=sys.stderr)
            _safe_print(f"  Reason: The issue does not exist or your account lacks permission to view it.", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)

        except JiraUnconfiguredError as juc:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Jira ticket selection rejected: Jira integration is unconfigured.", file=sys.stderr)
            _safe_print(f"  JIRA_BASE_URL is not set. Cannot verify whether issue '{juc.key}' exists.", file=sys.stderr)
            _safe_print(f"\n  Offline Policy:", file=sys.stderr)
            _safe_print(f"  To attribute telemetry to an unverified ticket while working offline, pass:", file=sys.stderr)
            _safe_print(f"    --allow-unverified (or --offline)", file=sys.stderr)
            _safe_print(f"  Example: telemetry-agent jira select {juc.key} --allow-unverified\n", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)

        except JiraUnreachableError as jue:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Jira validation failed ({jue.status}): {jue.reason}", file=sys.stderr)
            _safe_print(f"  Cannot verify whether issue '{jue.key}' exists on the configured Jira instance.", file=sys.stderr)
            _safe_print(f"\n  Offline Policy:", file=sys.stderr)
            _safe_print(f"  To attribute telemetry to an unverified ticket while offline or disconnected, pass:", file=sys.stderr)
            _safe_print(f"    --allow-unverified (or --offline)", file=sys.stderr)
            _safe_print(f"  Example: telemetry-agent jira select {jue.key} --allow-unverified\n", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)

        except ValueError as ve:
            _safe_print(f"\nError: {ve}\n", file=sys.stderr)
            sys.exit(1)

    elif action == "status":
        active, proposed = attr_store.sync_branch(repo_arg)
        detection = detect_branch_ticket(repo_arg)
        repo_key, repo_name, current_branch = attr_store._resolve_repo(repo_arg)

        if getattr(args, "json", False):
            payload = {
                "repository": repo_name,
                "repository_root": repo_key,
                "current_branch": current_branch,
                "active_ticket": active.to_dict() if active else None,
                "proposed_switch": proposed,
                "branch_detection": {
                    "keys": detection.keys,
                    "status": detection.status,
                    "suggested_key": detection.suggested_key,
                },
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        print("\n" + "=" * 50)
        print("Active Jira Ticket Association")
        print("=" * 50)
        print(f"Repository     : {repo_name}")
        print(f"Current Branch : {current_branch or 'unknown'}")
        if active:
            val_status = getattr(active, "validation_status", "unverified")
            print(f"Active Ticket  : {active.issue_key} ({active.confirmation_state}, {val_status})")
            if getattr(active, "issue_title", None):
                print(f"Issue Title    : {active.issue_title}")
            if getattr(active, "issue_status", None):
                print(f"Issue Status   : {active.issue_status}")
            print(f"Source         : {active.source}")
            print(f"Active Since   : {active.start_time}")
        else:
            print(f"Active Ticket  : UNASSIGNED")

        if proposed:
            print(f"Proposed Switch: {proposed} (detected on branch '{current_branch}')")
            print("Action Required: Run 'telemetry-agent jira confirm' to switch active ticket.")
        elif not active:
            print("Action         : Run 'telemetry-agent jira select <ISSUE-KEY>' to assign work.")
        print("=" * 50 + "\n")

    elif action == "clear":
        attr_store.clear_ticket(repo_arg)
        _safe_print("\n✓ Cleared active Jira ticket association. Work is now marked as UNASSIGNED.\n")

    elif action == "preview":
        detection = detect_branch_ticket(repo_arg)
        active = attr_store.get_active_attribution(repo_arg)
        repo_key, repo_name, current_branch = attr_store._resolve_repo(repo_arg)

        if getattr(args, "json", False):
            payload = {
                "repository": repo_name,
                "branch": current_branch,
                "detected_keys": detection.keys,
                "status": detection.status,
                "suggested_key": detection.suggested_key,
                "active_ticket": active.issue_key if active else None,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        print("\n" + "=" * 50)
        print("Branch Issue Key Preview")
        print("=" * 50)
        print(f"Repository     : {repo_name}")
        print(f"Current Branch : {current_branch or 'unknown'}")
        print(f"Detected Keys  : {', '.join(detection.keys) if detection.keys else 'none'}")
        print(f"Status         : {detection.status}")
        if detection.status == "single":
            print(f"Suggested Key  : {detection.suggested_key}")
            if active and active.issue_key == detection.suggested_key:
                print(f"Alignment      : Agrees with active ticket '{active.issue_key}'")
            elif active:
                print(f"Alignment      : Differs from active ticket '{active.issue_key}' (requires confirmation to switch)")
        elif detection.status == "conflict":
            print("Alignment      : Multiple conflicting issue keys detected in branch name; manual selection required.")
        else:
            print("Alignment      : No issue key found in branch name.")
        print("=" * 50 + "\n")

    elif action == "confirm":
        jira_adapter = _get_jira_adapter(settings)
        allow_unverified = getattr(args, "allow_unverified", False)
        try:
            confirmed = attr_store.confirm_switch(
                repo_arg,
                jira_adapter=jira_adapter,
                allow_unverified=allow_unverified,
            )
            if confirmed:
                val_status = getattr(confirmed, "validation_status", "unverified")
                val_str = f" ({val_status})" if val_status != "unverified" else ""
                _safe_print(f"\n✓ Confirmed switch to Jira ticket '{confirmed.issue_key}'{val_str} for repository '{confirmed.repository_name}'.")
                _safe_print(f"  Branch         : {confirmed.branch or 'unknown'}")
                _safe_print(f"  Validation     : {val_status}")
                if getattr(confirmed, "issue_title", None):
                    _safe_print(f"  Issue Title    : {confirmed.issue_title}")
                _safe_print(f"  Confirmation   : {confirmed.confirmation_state}")
                _safe_print(f"  Active Since   : {confirmed.start_time}\n")
            else:
                _safe_print("\nNo proposed ticket switch is currently pending confirmation.\n")
        except JiraIssueNotFoundError as infe:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Proposed Jira ticket confirmation rejected: Issue '{infe.key}' was not found on Jira (HTTP 404 Not Found).", file=sys.stderr)
            _safe_print(f"  Reason: The issue does not exist or your account lacks permission to view it.", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)
        except JiraUnconfiguredError as juc:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Proposed Jira ticket confirmation rejected: Jira integration is unconfigured.", file=sys.stderr)
            _safe_print(f"  JIRA_BASE_URL is not set. Cannot verify whether proposed issue '{juc.key}' exists.", file=sys.stderr)
            _safe_print(f"\n  Offline Policy:", file=sys.stderr)
            _safe_print(f"  To confirm an unverified ticket while working offline, pass:", file=sys.stderr)
            _safe_print(f"    --allow-unverified (or --offline)", file=sys.stderr)
            _safe_print(f"  Example: telemetry-agent jira confirm --allow-unverified\n", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)
        except JiraUnreachableError as jue:
            active_attr = attr_store.get_active_attribution(repo_arg)
            active_display = active_attr.issue_key if active_attr else "UNASSIGNED"
            _safe_print(f"\n[ERROR] Jira validation failed ({jue.status}): {jue.reason}", file=sys.stderr)
            _safe_print(f"  Cannot verify whether proposed issue '{jue.key}' exists.", file=sys.stderr)
            _safe_print(f"  Pass --allow-unverified to confirm under offline policy.\n", file=sys.stderr)
            _safe_print(f"  Active ticket remains unchanged: {active_display}\n", file=sys.stderr)
            sys.exit(1)

    elif action == "recent":
        limit = getattr(args, "limit", 10)
        recent = attr_store.get_recent_tickets(repo_path=repo_arg, limit=limit)
        if getattr(args, "json", False):
            print(json.dumps(recent, indent=2, ensure_ascii=False))
            return

        if not recent:
            print("\nNo recent Jira ticket associations found.\n")
            return

        print("\n" + f"{'TICKET':<12} | {'REPOSITORY':<20} | {'BRANCH':<24} | {'SOURCE':<10} | {'ACTIVE':<8} | {'LAST ACTIVE'}")
        print("-" * 95)
        for r in recent:
            act_str = "YES" if r.get("is_active") else "NO"
            print(f"{r['issue_key']:<12} | {str(r.get('repository') or 'unknown'):<20} | {str(r.get('branch') or '-'):<24} | {r.get('source', '-'):<10} | {act_str:<8} | {r.get('last_active', '-')}")
        print("-" * 95 + "\n")

    elif action == "summary":
        event_store = _get_active_store(settings)
        jira_adapter = _get_jira_adapter(settings)
        aggregator = TicketAggregator(event_store, attr_store, jira_adapter)
        summaries = aggregator.get_ticket_summaries(
            repository_name=repo_arg,
            ticket_key=getattr(args, "ticket", None),
            date_from=getattr(args, "date_from", None),
            date_to=getattr(args, "date_to", None),
        )

        if getattr(args, "json", False):
            print(json.dumps({k: v.to_dict() for k, v in summaries.items()}, indent=2, ensure_ascii=False))
            return

        if not summaries:
            print("No telemetry records found for ticket summary.")
            return

        print("\n" + "=" * 70)
        print("JIRA TICKET TELEMETRY SUMMARY")
        print("=" * 70)

        total_sessions = 0
        total_runs = 0
        total_credits = 0.0
        attributed_sessions = 0
        attributed_credits = 0.0

        all_session_ids: set = set()
        any_credit_data = False

        for key, s in sorted(summaries.items(), key=lambda x: (x[0] == "UNASSIGNED", x[0])):
            total_sessions += s.sessions_count
            total_runs += s.ai_runs_count
            total_credits = round(total_credits + s.total_credits, 4)
            all_session_ids.update(s.session_ids)
            if s.has_credit_data:
                any_credit_data = True
            if key != "UNASSIGNED":
                attributed_sessions += s.sessions_count
                attributed_credits = round(attributed_credits + s.total_credits, 4)

            print(f"\nTicket        : {s.issue_key}")
            if s.title:
                print(f"Title         : {s.title}")
            if s.status:
                print(f"Status        : {s.status}")
            print(f"Repositories  : {', '.join(s.repositories) if s.repositories else 'none'}")
            print(f"Branches      : {', '.join(s.branches) if s.branches else 'none'}")
            print(f"Sessions      : {s.sessions_count}")
            print(f"AI Runs       : {s.ai_runs_count}")
            if s.has_credit_data:
                print(f"Total Credits : {s.total_credits:.4f} {s.credit_unit or 'credit'}s")
            else:
                print(f"Total Credits : unavailable")
            if s.has_token_data:
                in_tok = f"{s.input_tokens:,}" if s.input_tokens is not None else "-"
                out_tok = f"{s.output_tokens:,}" if s.output_tokens is not None else "-"
                c_tok = f"{s.cache_read_tokens:,}" if s.cache_read_tokens is not None else "-"
                tot_tok = f"{s.total_tokens:,}" if s.total_tokens is not None else "-"
                print(f"Tokens        : Input: {in_tok} | Output: {out_tok} | Cache: {c_tok} | Total: {tot_tok}")
            print(f"Duration      : {s.duration_seconds}s (tracked session duration, not active coding time)")
            print(f"Source        : {s.source} ({s.confirmation_state})")
            if s.session_ids:
                print(f"Session IDs   : {', '.join(s.session_ids[:5])}{' (and ' + str(len(s.session_ids)-5) + ' more)' if len(s.session_ids) > 5 else ''}")
            print("-" * 50)

        unassigned_s = summaries.get("UNASSIGNED")
        unassigned_count = unassigned_s.sessions_count if unassigned_s else 0
        unassigned_cred = unassigned_s.total_credits if unassigned_s else 0.0

        print("\nReconciliation:")
        print(f"  Total Unique Sessions: {len(all_session_ids)} (Attributed Engagements: {attributed_sessions} | Unassigned: {unassigned_count})")
        if any_credit_data:
            print(f"  Attributed Credits   : {attributed_credits:.4f} | Unassigned: {unassigned_cred:.4f} | Total: {total_credits:.4f}")
        else:
            print(f"  Credits Status       : unavailable (no measured credit telemetry in selected scope)")
        print("=" * 70 + "\n")

    elif action == "sessions":
        args.limit = getattr(args, "limit", 50)
        cmd_sessions(args)

    elif action == "test-connection":
        jira_adapter = _get_jira_adapter(settings)
        if isinstance(jira_adapter, NullJiraAdapter) or not jira_adapter.is_configured():
            _safe_print("\n[ERROR] Jira connection test failed: Jira API is unconfigured.", file=sys.stderr)
            _safe_print("  Reason: JIRA_BASE_URL is not set in environment or .env file.\n", file=sys.stderr)
            sys.exit(1)

        result = jira_adapter.test_connection()
        issue_key_arg = getattr(args, "issue", None) or getattr(args, "ticket", None)
        issue_result = None
        if issue_key_arg and result.get("status") == "success":
            issue_result = getattr(jira_adapter, "test_issue", lambda k: None)(issue_key_arg)

        if getattr(args, "json", False):
            payload = {
                "connection": result,
                "issue_lookup": issue_result,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if result.get("status") != "success" or (issue_result and issue_result.get("status") != "success"):
                sys.exit(1)
            return

        print("\n" + "=" * 55)
        print("          JIRA API CONNECTION TEST")
        print("=" * 55)
        print(f"Base URL       : {result.get('base_url', settings.jira_base_url if settings else 'unknown')}")
        print(f"Auth Type      : {result.get('auth_type', 'unknown')}")
        print(f"Status         : {result.get('status', 'unknown').upper()}")
        print(f"Message        : {result.get('message', '')}")

        if result.get("status") == "success":
            if result.get("display_name"):
                print(f"Account Name   : {result.get('display_name')}")
            if result.get("email"):
                print(f"Account Email  : {result.get('email')}")
            if result.get("account_type"):
                print(f"Account Type   : {result.get('account_type')}")
            if result.get("time_zone"):
                print(f"Time Zone      : {result.get('time_zone')}")
            act_str = "YES" if result.get("active") else "NO"
            print(f"Account Active : {act_str}")

            if issue_result:
                print("-" * 55)
                print(f"Issue Lookup   : {issue_result.get('key')}")
                print(f"Lookup Status  : {issue_result.get('status', 'unknown').upper()}")
                print(f"Lookup Message : {issue_result.get('message', '')}")
                if issue_result.get("title"):
                    print(f"Issue Title    : {issue_result.get('title')}")
                if issue_result.get("issue_status"):
                    print(f"Issue State    : {issue_result.get('issue_status')}")
                if issue_result.get("url"):
                    print(f"Issue URL      : {issue_result.get('url')}")
            print("=" * 55 + "\n")
        else:
            print("=" * 55 + "\n", file=sys.stderr)
            sys.exit(1)


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop a running collector instance via pid file with stale PID protection."""
    try:
        settings = load_settings(env_file=args.env_file)
        data_dir = settings.data_dir
    except Exception:
        data_dir = (get_user_agent_home() / "data").resolve()

    pid_file = data_dir / "collector.pid"
    if not pid_file.exists():
        # check local ./data
        pid_file = Path("./data/collector.pid").resolve()

    if not pid_file.exists():
        print("No running collector detected.")
        return

    is_running, pid = get_collector_status(data_dir, cleanup_stale=False)
    if not is_running:
        try:
            stale_pid = int(pid_file.read_text(encoding="utf-8").strip())
            pid_file.unlink(missing_ok=True)
            print(f"Removed stale collector PID file (PID {stale_pid} is not running). No processes were terminated.")
        except Exception:
            pid_file.unlink(missing_ok=True)
            print("Removed invalid collector PID file.")
        return

    try:
        if sys.platform == "win32":
            os.system(f"taskkill /PID {pid} /F >nul 2>&1")
        else:
            os.kill(pid, 15)
        pid_file.unlink(missing_ok=True)
        meta_file = pid_file.parent / "collector_meta.json"
        meta_file.unlink(missing_ok=True)
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
        data_dir = settings.data_dir
    except Exception:
        data_dir = (get_user_agent_home() / "data").resolve()

    collector_running, _ = get_collector_status(data_dir, cleanup_stale=True)

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


def cmd_kiro_hook_status(args: argparse.Namespace) -> None:
    """Display Kiro IDE hook configuration and verification status."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    status = get_kiro_hook_status(hooks_file=target_file)

    print("\n" + "=" * 50)
    print("Kiro IDE Telemetry Hook Status")
    print("=" * 50)
    print(f"Config Path             : {status['config_path']}")
    print(f"Config exists           : {'YES' if status['config_exists'] else 'NO'}")
    print(f"JSON valid              : {'YES' if status['json_valid'] else 'NO'}")
    print(f"Telemetry hook installed: {'YES' if status['telemetry_hook_installed'] else 'NO'}")
    print(f"Telemetry handler       : {'VALID' if status['handler_valid'] else 'INVALID'}")
    print(f"Handler Path            : {status['handler_path']}")
    print(f"Python Executable       : {status['python_executable']}")
    print(f"Other hooks detected    : {status['other_hooks_count']}")
    print("=" * 50 + "\n")


def cmd_install_kiro_hook(args: argparse.Namespace) -> None:
    """Install or update Kiro IDE lifecycle hooks."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Kiro Hook Installer")
    print("=" * 50)
    print(f"User        : {user_name}")
    print(f"Agent       : ai-telemetry-agent")

    success, msg, backup_path, other_count = install_kiro_hooks(
        hooks_file=target_file,
        python_exe=args.python if getattr(args, "python", None) else None,
    )

    if not success:
        print(f"\nERROR: {msg}", file=sys.stderr)
        sys.exit(1)

    if backup_path:
        print(f"Backup created:\n{backup_path}\n")

    print(f"{msg}")
    print("Telemetry will now capture Kiro IDE activity.")
    print("=" * 50 + "\n")


def cmd_uninstall_kiro_hook(args: argparse.Namespace) -> None:
    """Uninstall telemetry hooks from Kiro IDE configuration."""
    target_file = Path(args.config) if getattr(args, "config", None) else None
    user_name = os.environ.get("USERNAME") or os.environ.get("USER") or Path.home().name

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Kiro Hook Uninstaller")
    print("=" * 50)
    print(f"User        : {user_name}")
    print("Removing Kiro telemetry hooks...")

    success, msg, backup_path = uninstall_kiro_hooks(hooks_file=target_file)
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
        description="Local AI and Engineering Telemetry Agent v0.1.2",
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
    p_start.add_argument("--source", "--telemetry-source", dest="source", type=str, default=None, choices=["antigravity", "kiro_cli", "kiro_ide", "auto"], help="Override TELEMETRY_SOURCE")

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
    p_sess = subparsers.add_parser("sessions", help="List AI sessions and correlated repositories")
    p_sess.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_sess.add_argument("--ticket", type=str, default=None, help="Filter by Jira issue key")
    p_sess.add_argument("--member", type=str, default=None, help="Filter by member ID")
    p_sess.add_argument("--limit", type=int, default=50, help="Max sessions to display (default: 50)")
    p_sess.add_argument("--json", action="store_true", help="Output raw JSON array")

    # Summary command
    p_sum = subparsers.add_parser("summary", help="Show summary metrics aggregated by repository or Jira ticket")
    p_sum.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_sum.add_argument("--ticket", type=str, default=None, help="Filter or aggregate by Jira issue key")
    p_sum.add_argument("--by-ticket", action="store_true", help="Group summary by Jira ticket instead of repository")
    p_sum.add_argument("--from", dest="date_from", type=str, default=None, help="Filter start date (YYYY-MM-DD)")
    p_sum.add_argument("--to", dest="date_to", type=str, default=None, help="Filter end date (YYYY-MM-DD)")
    p_sum.add_argument("--json", action="store_true", help="Output raw JSON")

    # Hook management commands
    p_hstatus = subparsers.add_parser("hook-status", help="Display global Antigravity hook status")
    p_hstatus.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    p_hinstall = subparsers.add_parser("install-hook", help="Install global Antigravity lifecycle hooks")
    p_hinstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")
    p_hinstall.add_argument("--python", type=str, default=None, help="Custom python executable path")

    p_huninstall = subparsers.add_parser("uninstall-hook", help="Uninstall global Antigravity lifecycle hooks")
    p_huninstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    # Kiro Hook management commands
    p_khstatus = subparsers.add_parser("kiro-hook-status", help="Display Kiro IDE hook status")
    p_khstatus.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    p_khinstall = subparsers.add_parser("install-kiro-hook", help="Install Kiro IDE lifecycle hooks")
    p_khinstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")
    p_khinstall.add_argument("--python", type=str, default=None, help="Custom python executable path")

    p_khuninstall = subparsers.add_parser("uninstall-kiro-hook", help="Uninstall Kiro IDE lifecycle hooks")
    p_khuninstall.add_argument("--config", type=str, default=None, help="Custom hooks.json path")

    # Jira ticket attribution commands
    p_jira = subparsers.add_parser("jira", help="Manage Jira ticket attribution and reporting")
    p_jira.add_argument("--repo", type=str, default=None, help="Target repository path (default: current directory)")
    jira_subparsers = p_jira.add_subparsers(dest="jira_command", help="Jira subcommands")

    # jira select <ticket>
    p_jsel = jira_subparsers.add_parser("select", help="Select or assign a Jira ticket to current repository (validates issue existence with Jira)")
    p_jsel.add_argument("ticket", type=str, help="Jira issue key (e.g. PROJ-148)")
    p_jsel.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jsel.add_argument("--allow-unverified", "--offline", dest="allow_unverified", action="store_true", help="Allow selecting an unverified issue when Jira is unconfigured or unreachable")

    # jira status
    p_jstat = jira_subparsers.add_parser("status", help="Show active Jira ticket association")
    p_jstat.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jstat.add_argument("--json", action="store_true", help="Output raw JSON")

    # jira recent
    p_jrec = jira_subparsers.add_parser("recent", help="List recent or previously associated Jira tickets")
    p_jrec.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jrec.add_argument("--limit", type=int, default=10, help="Max tickets to display (default: 10)")
    p_jrec.add_argument("--json", action="store_true", help="Output raw JSON")

    # jira clear
    p_jclr = jira_subparsers.add_parser("clear", help="Clear current association and mark work as unassigned")
    p_jclr.add_argument("--repo", type=str, default=None, help="Target repository path")

    # jira preview
    p_jprev = jira_subparsers.add_parser("preview", help="Preview branch-detected issue key without changing attribution")
    p_jprev.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jprev.add_argument("--json", action="store_true", help="Output raw JSON")

    # jira confirm
    p_jconf = jira_subparsers.add_parser("confirm", help="Confirm a proposed ticket switch (validates issue existence with Jira)")
    p_jconf.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jconf.add_argument("--allow-unverified", "--offline", dest="allow_unverified", action="store_true", help="Allow confirming switch when Jira is unconfigured or unreachable")

    # jira summary
    p_jsum = jira_subparsers.add_parser("summary", help="Show ticket-level aggregated usage report")
    p_jsum.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_jsum.add_argument("--ticket", type=str, default=None, help="Filter by Jira issue key")
    p_jsum.add_argument("--from", dest="date_from", type=str, default=None, help="Filter start date (YYYY-MM-DD)")
    p_jsum.add_argument("--to", dest="date_to", type=str, default=None, help="Filter end date (YYYY-MM-DD)")
    p_jsum.add_argument("--json", action="store_true", help="Output raw JSON")

    # jira sessions
    p_jsess = jira_subparsers.add_parser("sessions", help="List AI sessions with attributed Jira tickets")
    p_jsess.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_jsess.add_argument("--ticket", type=str, default=None, help="Filter by Jira issue key")
    p_jsess.add_argument("--member", type=str, default=None, help="Filter by member ID")
    p_jsess.add_argument("--limit", type=int, default=50, help="Max sessions to display (default: 50)")
    p_jsess.add_argument("--json", action="store_true", help="Output raw JSON array")

    # jira test-connection
    p_jtest = jira_subparsers.add_parser("test-connection", help="Perform read-only Jira API authentication and connectivity verification")
    p_jtest.add_argument("--issue", "--ticket", dest="issue", type=str, default=None, help="Optional Jira issue key to test read access (e.g. SCRUM-1)")
    p_jtest.add_argument("--json", action="store_true", help="Output raw JSON diagnostic result")

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
    elif args.command == "kiro-hook-status":
        cmd_kiro_hook_status(args)
    elif args.command == "install-kiro-hook":
        cmd_install_kiro_hook(args)
    elif args.command == "uninstall-kiro-hook":
        cmd_uninstall_kiro_hook(args)
    elif args.command == "jira":
        cmd_jira(args)
    elif args.command == "stop":
        cmd_stop(args)


if __name__ == "__main__":
    cli_entrypoint()
