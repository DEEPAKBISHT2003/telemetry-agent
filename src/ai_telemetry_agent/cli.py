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
from typing import Optional, Union

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
    resolve_data_dir,
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
from ai_telemetry_agent.accounting.aggregator import AccountingAggregator
from ai_telemetry_agent.accounting.models import AIOperationRecord, sum_credits
from ai_telemetry_agent.accounting.registry import HookRegistry
from ai_telemetry_agent.accounting.store import AccountingStore


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


def _get_active_store(settings: Optional[Settings] = None, data_dir: Optional[Union[str, Path]] = None) -> EventStore:
    """Resolve active local telemetry store: JSONLEventStore."""
    if data_dir:
        return JSONLEventStore(data_dir)
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None

    target_dir = resolve_data_dir(settings=settings)
    return JSONLEventStore(target_dir)


def _get_attribution_store(settings: Optional[Settings] = None, data_dir: Optional[Union[str, Path]] = None) -> AttributionStore:
    """Resolve active persistent attribution store."""
    if data_dir:
        return AttributionStore(data_dir)
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None

    target_dir = resolve_data_dir(settings=settings)
    return AttributionStore(target_dir)


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


def _event_source_label(ev) -> str:
    """Determine the displayed SOURCE label for a telemetry event.

    Distinguishes the telemetry provider (kiro_ide / kiro_cli / antigravity)
    from the capture method. Kiro IDE/CLI session-file events use ai_*
    event types with non-`kiro_` capture methods (ide_session_files,
    messages_jsonl, session_json) and must still be labeled ``kiro`` --
    not the ``antigravity`` catch-all fallback that the previous
    event_type-only logic produced.
    """
    p = ev.payload or {}
    etype = ev.event_type
    if etype.startswith("git_"):
        return "git"
    provider = p.get("provider")
    if provider in ("kiro_ide", "kiro_cli"):
        return "kiro"
    # Kiro events captured via hooks use capture_method="kiro_hooks"; the
    # kiro_detected detection event is also Kiro-originated.
    if etype == "kiro_detected" or p.get("capture_method", "").startswith("kiro_"):
        return "kiro"
    if provider == "antigravity":
        return "antigravity"
    # Legacy fallback: an AI event without an explicit provider. Kept so
    # historical records written before providers were populated still
    # display a reasonable label instead of "system".
    if etype.startswith("ai_") or etype in {"tool_call", "agent_started", "agent_completed"}:
        return "antigravity"
    return "system"


def cmd_events(args: argparse.Namespace) -> None:
    """Inspect locally collected telemetry events."""
    try:
        settings = load_settings(env_file=args.env_file)
    except Exception:
        settings = None

    target_data_dir = getattr(args, "data_dir", None)
    store = _get_active_store(settings, data_dir=target_data_dir)
    events = store.get_events(
        limit=args.last,
        event_type=args.type,
        member_id=args.member,
        repository_name=args.repo,
        session_id=args.session,
        descending=False if args.asc else True,
    )

    # Filter by displayed source (provider-based) when --source is given.
    # Native filtering is by event_type/repo; source is a derived label, so
    # we apply it client-side after retrieval.
    source_filter = getattr(args, "source", None)
    if source_filter:
        events = [e for e in events if _event_source_label(e) == source_filter]

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
        p = ev.payload or {}

        # Determine the displayed source from the authoritative provider field
        # (with event-type fallbacks for non-AI telemetry).
        source = _event_source_label(ev)

        if source == "git":
            details = f"repo={p.get('repository', '')} branch={p.get('branch', '')}"
            if "commit_sha" in p:
                details += f" sha={p.get('commit_sha')[:7]}"
        elif source in ("kiro", "antigravity"):
            details = f"provider={p.get('provider')} model={p.get('model')}"
            if p.get("tool_name"):
                details = f"tool={p.get('tool_name')} " + details
            if p.get("credits") is not None:
                details += f" credits={p.get('credits')}"
            if ev.context.repository and ev.context.repository.name:
                details += f" repo={ev.context.repository.name}"
        else:
            details = f"status={p.get('status', p.get('process_status', ''))}"
            if "uptime_seconds" in p:
                details += f" uptime={p['uptime_seconds']}s"

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
    _dur = s.get("duration_seconds")
    print(f"Duration   : {_dur}s" if _dur is not None else "Duration   : unknown (no measured AI operation)")
    _span = s.get("session_activity_span_seconds")
    print(f"Activity Span: {_span}s (first-to-last event wall-clock)" if _span is not None else "Activity Span: unknown")
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
        _dur = s.get("duration_seconds")
        duration = f"{_dur}s" if _dur is not None else "unknown"

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

    target_data_dir = getattr(args, "data_dir", None)
    store = _get_active_store(settings, data_dir=target_data_dir)
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

    target_data_dir = getattr(args, "data_dir", None)
    attr_store = _get_attribution_store(settings, data_dir=target_data_dir)
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
        hook_registry = _get_hook_registry(settings)
        aggregator = TicketAggregator(event_store, attr_store, jira_adapter, hook_registry=hook_registry)
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
            if s.hook_executions_count > 0:
                print(f"Hook Execs    : {s.hook_executions_count}")
            if s.has_credit_data:
                print(f"Total Credits : {s.total_credits:.4f} {s.credit_unit or 'credit'}s")
            else:
                print(f"Total Credits : unavailable")
            if s.has_estimated_credits:
                print(f"Est. Credits  : {s.estimated_credits:.4f} (allocated estimate)")
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


def _get_accounting_store(settings: Optional[Settings] = None, data_dir: Optional[Union[str, Path]] = None) -> AccountingStore:
    """Resolve active accounting store."""
    if data_dir:
        return AccountingStore(data_dir)
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None
    target_dir = resolve_data_dir(settings=settings)
    return AccountingStore(target_dir)


def _get_hook_registry(settings: Optional[Settings] = None, data_dir: Optional[Union[str, Path]] = None) -> HookRegistry:
    """Resolve active hook registry."""
    if data_dir:
        return HookRegistry(data_dir)
    if settings is None:
        try:
            settings = load_settings()
        except Exception:
            settings = None
    target_dir = resolve_data_dir(settings=settings)
    return HookRegistry(target_dir)


def cmd_accounting(args: argparse.Namespace) -> None:
    """Handle AI credit expenditure accounting commands."""
    try:
        settings = load_settings(env_file=getattr(args, "env_file", None))
    except Exception:
        settings = None

    target_data_dir = getattr(args, "data_dir", None)
    store = _get_accounting_store(settings, data_dir=target_data_dir)
    hook_registry = _get_hook_registry(settings, data_dir=target_data_dir)
    aggregator = AccountingAggregator(store, hook_registry=hook_registry)
    action = getattr(args, "accounting_command", None)

    if not action:
        _safe_print("Error: Accounting action required. Run 'telemetry-agent accounting --help' for available commands.")
        sys.exit(1)

    if action == "summary":
        if getattr(args, "estimate_hooks", False) and hook_registry:
            hook_registry.allocate_estimated_credits(store)
        _handle_accounting_summary(args, aggregator)
    elif action == "operations":
        _handle_accounting_operations(args, store)
    elif action == "operation":
        _handle_accounting_operation(args, store)
    elif action == "reconciliation":
        _handle_accounting_reconciliation(args, aggregator)
    elif action == "hooks":
        _handle_accounting_hooks(args, settings)
    elif action == "import-events":
        _handle_accounting_import_events(args, store, settings)


def _handle_accounting_summary(args: argparse.Namespace, aggregator: AccountingAggregator) -> None:
    summary = aggregator.get_summary(
        date_from=getattr(args, "date_from", None),
        date_to=getattr(args, "date_to", None),
        jira_key=getattr(args, "ticket", None),
        repository_name=getattr(args, "repo", None),
        provider=getattr(args, "provider", None),
    )

    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
        return

    _safe_print("=" * 70)
    _safe_print("AI CREDIT EXPENDITURE SUMMARY")
    _safe_print("=" * 70)
    _safe_print(f"Total Operations Analyzed : {summary['total_operations_count']}")
    
    meas_cnt = summary.get("measured_operations_count")
    unmeas_cnt = summary.get("unmeasured_operations_count")
    if meas_cnt is not None and unmeas_cnt is not None:
        _safe_print(f"Operational Coverage      : {meas_cnt} measured ops, {unmeas_cnt} unmeasured ops")

    _safe_print(f"Total Measured Credits    : {summary['total_measured_credits_exact']} credits (Kiro CLI & IDE only)")

    inter = summary["interactive_vs_automated"]["interactive"]
    auto = summary["interactive_vs_automated"]["automated"]
    _safe_print(f"Interactive Expenditure   : {inter['credits_exact']} credits ({inter['count']} ops: user prompts & queries)")
    _safe_print(f"Automated Expenditure     : {auto['credits_exact']} credits ({auto['count']} ops: tool calls & subagents)")

    sub = summary["subagent_expenditure"]
    _safe_print(f"Subagent Expenditure      : {sub['credits_exact']} credits ({sub['count']} ops)")

    failed = summary["failed_and_retried"]["failed"]
    retried = summary["failed_and_retried"]["retried"]
    _safe_print(f"Failed Operations         : {failed['credits_exact']} credits ({failed['count']} ops)")
    _safe_print(f"Retried Operations        : {retried['credits_exact']} credits ({retried['count']} ops)")

    if summary.get("cross_provider_coverage_note"):
        _safe_print(f"\n[Coverage Note] {summary['cross_provider_coverage_note']}")

    by_prov = getattr(args, "by_provider", False)
    by_trig = getattr(args, "by_trigger", False)
    by_hook = getattr(args, "by_hook", False)
    by_tick = getattr(args, "by_ticket", False)
    by_model = getattr(args, "by_model", False)

    show_all = not any([by_prov, by_trig, by_hook, by_tick, by_model])

    if by_prov or show_all:
        _safe_print("\n--- By Provider ---")
        for prov, data in summary["by_provider"].items():
            unmeas = f", unmeasured: {data['unmeasured_count']}" if data.get("unmeasured_count") else ""
            _safe_print(f"  {prov:<16}: {data['credits_exact']} credits ({data['count']} ops{unmeas})")

    if show_all:
        _safe_print("\n--- By Operation Type ---")
        for op_t, data in summary["by_operation_type"].items():
            _safe_print(f"  {op_t:<16}: {data['credits_exact']} credits ({data['count']} ops)")

    if by_trig or show_all:
        _safe_print("\n--- By Trigger Type ---")
        for trig, data in summary["by_trigger_type"].items():
            _safe_print(f"  {trig:<16}: {data['credits_exact']} credits ({data['count']} ops)")

    if by_tick and by_hook:
        _safe_print("\n--- Jira Ticket Attribution by Hook ---")
        tickets = summary["jira_attribution"]["attributed_tickets"]
        if tickets:
            for t_key, t_data in tickets.items():
                est_sec = f", est: {t_data['hook_estimated_credits_exact']}" if t_data.get("hook_estimated_credits") is not None else ""
                _safe_print(f"\n  Ticket: {t_key} (Total: {t_data['credits_exact']} credits, {t_data['count']} ops, {t_data.get('hook_count', 0)} hooks{est_sec})")
                t_hooks = t_data.get("by_hook", {})
                if t_hooks:
                    for h_name, h_data in t_hooks.items():
                        est_str = f", est: {h_data['estimated_credits_exact']}" if h_data.get("estimated_credits") is not None else ""
                        _safe_print(f"    - {h_name:<20}: {h_data['count']} execs, actual credits: {h_data['credits_exact']}{est_str}")
                else:
                    _safe_print("    No hook executions for this ticket.")
        else:
            _safe_print("  No attributed tickets found.")
        unassigned = summary["jira_attribution"]["unassigned"]
        if unassigned["count"] > 0 or unassigned.get("hook_count", 0) > 0:
            est_sec = f", est: {unassigned['hook_estimated_credits_exact']}" if unassigned.get("hook_estimated_credits") is not None else ""
            _safe_print(f"\n  Ticket: UNASSIGNED (Total: {unassigned['credits_exact']} credits, {unassigned['count']} ops, {unassigned.get('hook_count', 0)} hooks{est_sec})")
            u_hooks = unassigned.get("by_hook", {})
            if u_hooks:
                for h_name, h_data in u_hooks.items():
                    est_str = f", est: {h_data['estimated_credits_exact']}" if h_data.get("estimated_credits") is not None else ""
                    _safe_print(f"    - {h_name:<20}: {h_data['count']} execs, actual credits: {h_data['credits_exact']}{est_str}")
    elif by_tick or show_all:
        _safe_print("\n--- Jira Ticket Attribution ---")
        tickets = summary["jira_attribution"]["attributed_tickets"]
        if tickets:
            for t_key, data in tickets.items():
                h_cnt = f", {data['hook_count']} hooks" if data.get("hook_count") else ""
                est_str = f", est: {data['hook_estimated_credits_exact']}" if data.get("hook_estimated_credits") is not None else ""
                _safe_print(f"  {t_key:<16}: {data['credits_exact']} credits ({data['count']} ops, {data['sessions_count']} sessions{h_cnt}{est_str})")
        else:
            _safe_print("  No attributed tickets found.")
        unassigned = summary["jira_attribution"]["unassigned"]
        if unassigned["count"] > 0:
            h_cnt = f", {unassigned['hook_count']} hooks" if unassigned.get("hook_count") else ""
            est_str = f", est: {unassigned['hook_estimated_credits_exact']}" if unassigned.get("hook_estimated_credits") is not None else ""
            _safe_print(f"  UNASSIGNED      : {unassigned['credits_exact']} credits ({unassigned['count']} ops{h_cnt}{est_str})")

    if by_model or show_all:
        _safe_print("\n--- By Model ---")
        for mdl, data in summary["by_model"].items():
            _safe_print(f"  {mdl:<25}: {data['credits_exact']} credits ({data['count']} ops)")

    if by_hook and not by_tick:
        _safe_print("\n--- By Hook ---")
        hooks = summary["by_hook"]
        if hooks:
            for h_name, data in hooks.items():
                est_str = f", est: {data['estimated_credits_exact']}" if data.get("estimated_credits") is not None else ""
                _safe_print(f"  {h_name:<25}: {data['credits_exact']} credits ({data['count']} ops, trigger: {data['trigger']}{est_str})")
    diag = summary.get("hook_diagnostics")
    if diag and (by_hook or getattr(args, "estimate_hooks", False)):
        _safe_print("\n--- Hook Estimation Diagnostics ---")
        _safe_print(f"  Eligible Hooks   : {diag['eligible_count']}")
        _safe_print(f"  Ineligible Hooks : {diag['ineligible_count']}")
        _safe_print(f"  Ambiguous Hooks  : {diag['ambiguous_count']}")
        _safe_print(f"  Estimated Credits: {diag['total_estimated_credits_exact']} credits")
        if diag.get("by_reason"):
            reasons_str = ", ".join(f"{r}: {cnt}" for r, cnt in sorted(diag["by_reason"].items()))
            _safe_print(f"  Unallocated Reasons: {reasons_str}")
        if diag.get("parent_operation_ids"):
            parents_str = ", ".join(diag["parent_operation_ids"][:5])
            if len(diag["parent_operation_ids"]) > 5:
                parents_str += f" (+{len(diag['parent_operation_ids']) - 5} more)"
            _safe_print(f"  Correlated Parents: {parents_str}")

    _safe_print("=" * 70)


def _handle_accounting_operations(args: argparse.Namespace, store: AccountingStore) -> None:
    ops = store.get_operations(
        session_id=getattr(args, "session", None),
        jira_key=getattr(args, "ticket", None),
        provider=getattr(args, "provider", None),
        operation_type=getattr(args, "type", None),
        trigger_type=getattr(args, "trigger", None),
        measurement_status=getattr(args, "status", None),
        limit=getattr(args, "limit", 50),
    )

    if getattr(args, "json", False):
        print(json.dumps([op.to_dict() for op in ops], indent=2))
        return

    if not ops:
        _safe_print("No accounting operation records found matching filters.")
        return

    _safe_print("=" * 115)
    _safe_print(f"{'TIMESTAMP':<20} | {'OPERATION ID':<24} | {'PROVIDER':<10} | {'TYPE':<12} | {'TRIGGER':<15} | {'CREDITS':<10} | {'STATUS':<10} | {'JIRA'}")
    _safe_print("-" * 115)
    for op in ops:
        ts = (op.completed_at or op.started_at or "")[:19]
        op_id = op.operation_id[:24]
        prov = op.provider[:10]
        op_t = op.operation_type[:12]
        trig = op.trigger_type[:15]
        creds = str(op.credits_used) if op.credits_used is not None else "-"
        status = op.measurement_status[:10]
        jira = op.jira_key or "UNASSIGNED"
        _safe_print(f"{ts:<20} | {op_id:<24} | {prov:<10} | {op_t:<12} | {trig:<15} | {creds:<10} | {status:<10} | {jira}")
    _safe_print("=" * 115)


def _handle_accounting_operation(args: argparse.Namespace, store: AccountingStore) -> None:
    op_id = getattr(args, "id", None)
    if not op_id:
        _safe_print("Error: --id <OPERATION_ID> is required.")
        sys.exit(1)

    op = store.get_operation(op_id)
    if not op:
        _safe_print(f"Error: Operation record with ID '{op_id}' not found.")
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(op.to_dict(), indent=2))
        return

    _safe_print("=" * 60)
    _safe_print(f"AI OPERATION RECORD: {op.operation_id}")
    _safe_print("=" * 60)
    _safe_print(f"Provider             : {op.provider}")
    _safe_print(f"Source               : {op.source}")
    _safe_print(f"Operation Type       : {op.operation_type}")
    _safe_print(f"Trigger Type         : {op.trigger_type}")
    if op.hook_name:
        _safe_print(f"Hook Name            : {op.hook_name} ({op.hook_id})")
    _safe_print(f"Session ID           : {op.session_id}")
    if op.parent_operation_id:
        _safe_print(f"Parent Operation ID  : {op.parent_operation_id}")
    if op.parent_session_id:
        _safe_print(f"Parent Session ID    : {op.parent_session_id}")
    if op.request_id:
        _safe_print(f"Request ID           : {op.request_id}")
    _safe_print(f"Model Requested      : {op.model_requested or 'none'}")
    _safe_print(f"Model Resolved       : {op.model_resolved or 'none'}")
    _safe_print(f"Started At           : {op.started_at or 'none'}")
    _safe_print(f"Completed At         : {op.completed_at or 'none'}")
    _safe_print(f"Credits Used         : {op.credits_used if op.credits_used is not None else 'unavailable'}")
    if op.credit_source:
        _safe_print(f"Credit Source        : {op.credit_source}")
    _safe_print(f"Measurement Scope    : {op.measurement_scope}")
    _safe_print(f"Measurement Status   : {op.measurement_status}")
    if op.token_usage:
        _safe_print(f"Token Usage          : {op.token_usage}")
    if op.cost_estimate_usd is not None:
        _safe_print(f"Cost Estimate (USD)  : ${op.cost_estimate_usd:.6f}")
    _safe_print(f"Jira Key             : {op.jira_key or 'UNASSIGNED'}")
    if op.repository:
        _safe_print(f"Repository           : {op.repository.get('name')}")
    if op.branch:
        _safe_print(f"Branch               : {op.branch}")
    _safe_print(f"Developer ID         : {op.developer_id or 'none'}")
    _safe_print(f"Device ID            : {op.device_id or 'none'}")
    _safe_print(f"Execution Status     : {op.execution_status}")
    _safe_print(f"Deduplication Key    : {op.deduplication_key}")
    if op.metadata:
        _safe_print(f"Metadata             : {json.dumps(op.metadata, indent=2)}")
    _safe_print("=" * 60)


def _handle_accounting_reconciliation(args: argparse.Namespace, aggregator: AccountingAggregator) -> None:
    session_id = getattr(args, "session", None)
    if session_id:
        res = aggregator.reconcile_session(session_id)
        if getattr(args, "json", False):
            print(json.dumps(res, indent=2))
            return

        _safe_print("=" * 60)
        _safe_print(f"SESSION RECONCILIATION: {session_id}")
        _safe_print("=" * 60)
        _safe_print(f"Operations Count     : {res['operation_count']}")
        _safe_print(f"Operation Sum Credits: {res['operation_credits_exact']} credits")
        _safe_print(f"Session Scope Credits: {res['session_scope_credits_exact'] or 'none'}")
        _safe_print(f"Discrepancy          : {res['discrepancy_exact'] or '0'} credits")
        _safe_print(f"Status               : {'RECONCILED' if res['reconciled'] else 'VARIANCE_DETECTED'}")
        _safe_print("=" * 60)
    else:
        summary = aggregator.get_summary()
        recon = summary["reconciliation"]
        if getattr(args, "json", False):
            print(json.dumps(recon, indent=2))
            return

        _safe_print("=" * 60)
        _safe_print("ACCOUNT & PROVIDER RECONCILIATION")
        _safe_print("=" * 60)
        _safe_print(f"Total Operation Credits : {recon['operation_sum_credits_exact']} credits")
        _safe_print(f"Total Session Credits   : {recon['session_sum_credits_exact']} credits")
        _safe_print(f"Latest Account Credits  : {recon['latest_account_checkpoint_credits_exact'] or 'none'}")
        _safe_print(f"Reconciliation Variance : {recon['reconciliation_discrepancy_exact'] or 'none'}")
        _safe_print("=" * 60)


def _handle_accounting_hooks(args: argparse.Namespace, settings: Optional[Settings]) -> None:
    target_data_dir = getattr(args, "data_dir", None)
    registry = _get_hook_registry(settings, data_dir=target_data_dir)
    if getattr(args, "allocate_estimates", False):
        store = _get_accounting_store(settings, data_dir=target_data_dir)
        registry.allocate_estimated_credits(store)
    hooks = registry.get_registered_hooks()
    execs = registry.get_executions(limit=getattr(args, "limit", 50))

    if getattr(args, "json", False):
        diag = registry.get_diagnostics() if hasattr(registry, "get_diagnostics") else {}
        print(json.dumps({
            "registered_hooks": [h.to_dict() for h in hooks],
            "executions": [e.to_dict() for e in execs],
            "diagnostics": diag,
        }, indent=2))
        return

    _safe_print("=" * 115)
    _safe_print("REGISTERED HOOKS")
    _safe_print("=" * 115)
    if hooks:
        for h in hooks:
            ai_flag = "AI-Initiating" if h.is_ai_powered else "Shell-Only"
            sched = f" (cadence: {h.schedule_expression})" if h.schedule_expression else ""
            _safe_print(f"  {h.hook_name:<20} | Type: {h.trigger_type:<15}{sched} | Class: {ai_flag}")
    else:
        _safe_print("  No explicitly registered hooks in registry.")

    _safe_print("\n" + "=" * 115)
    _safe_print("RECENT HOOK EXECUTIONS")
    _safe_print("=" * 115)
    _safe_print(f"{'EXECUTED AT':<20} | {'HOOK NAME':<16} | {'TRIGGER':<12} | {'TICKET':<10} | {'CREDITS':<8} | {'ESTIMATE':<8} | {'PARENT OP':<16} | {'STATUS'}")
    _safe_print("-" * 115)
    if execs:
        for e in execs:
            ts = e.executed_at[:19]
            name = e.hook_name[:16]
            trig = e.trigger_type[:12]
            t_key = (e.jira_key or "UNASSIGNED")[:10]
            creds = str(e.credits_used) if e.credits_used is not None else "unavail"
            est = str(e.estimated_credits) if e.estimated_credits is not None else "-"
            parent_col = (e.parent_operation_id or "-")[:16]
            stat_col = e.correlation_status or e.outcome
            _safe_print(f"{ts:<20} | {name:<16} | {trig:<12} | {t_key:<10} | {creds:<8} | {est:<8} | {parent_col:<16} | {stat_col}")
        
        measured_creds = sum_credits([e.credits_used for e in execs])
        est_creds = sum_credits([e.estimated_credits for e in execs])
        _safe_print("-" * 115)
        _safe_print(f"Total: {len(execs)} executions | Measured Credits: {measured_creds} | Estimated Credits: {est_creds}")
        diag = registry.get_diagnostics() if hasattr(registry, "get_diagnostics") else {}
        if diag.get("eligible_count") or diag.get("ineligible_count") or diag.get("ambiguous_count"):
            _safe_print(f"Diagnostics: Eligible: {diag['eligible_count']} | Ineligible: {diag['ineligible_count']} | Ambiguous: {diag['ambiguous_count']}")
            if diag.get("by_reason"):
                _safe_print(f"Reasons: {', '.join(f'{k}: {v}' for k, v in sorted(diag['by_reason'].items()))}")
    else:
        _safe_print("  No hook executions recorded.")
    _safe_print("=" * 115)


def _handle_accounting_import_events(
    args: argparse.Namespace,
    store: AccountingStore,
    settings: Optional[Settings],
) -> None:
    """Import historical telemetry events from telemetry-*.jsonl files into the accounting store."""
    from ai_telemetry_agent.core.collector import TelemetryCollector

    collector = TelemetryCollector(settings=settings)
    stats = store.backfill_from_event_store(collector._ai_event_to_operation_record)

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return

    _safe_print("=" * 60)
    _safe_print("ACCOUNTING EVENT IMPORT SUMMARY")
    _safe_print("=" * 60)
    _safe_print(f"Files Scanned       : {stats['files_scanned']}")
    _safe_print(f"Events Scanned      : {stats['events_scanned']}")
    _safe_print(f"Operations Created  : {stats['records_created']}")
    _safe_print(f"Operations Saved    : {stats['records_saved']}")
    _safe_print(f"Duplicates Skipped  : {stats['records_skipped']}")
    _safe_print(f"Conversion Errors   : {stats['conversion_errors']}")
    _safe_print("=" * 60)


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
    parser.add_argument("--data-dir", type=str, default=None, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")

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
    p_events.add_argument("--source", type=str, default=None, choices=["antigravity", "kiro", "git", "system"], help="Filter by displayed telemetry source/provider")
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
    p_jira.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    jira_subparsers = p_jira.add_subparsers(dest="jira_command", help="Jira subcommands")

    # jira select <ticket>
    p_jsel = jira_subparsers.add_parser("select", help="Select or assign a Jira ticket to current repository (validates issue existence with Jira)")
    p_jsel.add_argument("ticket", type=str, help="Jira issue key (e.g. PROJ-148)")
    p_jsel.add_argument("--repo", type=str, default=None, help="Target repository path")
    p_jsel.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
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

    # Accounting commands
    p_acc = subparsers.add_parser("accounting", help="Comprehensive AI credit expenditure accounting and reconciliation")
    p_acc.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    acc_subparsers = p_acc.add_subparsers(dest="accounting_command", help="Accounting subcommands")

    # accounting summary
    p_asum = acc_subparsers.add_parser("summary", help="Show AI credit expenditure summary across dimensions")
    p_asum.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    p_asum.add_argument("--by-provider", action="store_true", help="Group expenditure by AI provider")
    p_asum.add_argument("--by-trigger", action="store_true", help="Group expenditure by trigger mechanism")
    p_asum.add_argument("--by-hook", action="store_true", help="Group expenditure by agent hook")
    p_asum.add_argument("--by-ticket", action="store_true", help="Group expenditure by Jira ticket")
    p_asum.add_argument("--by-repo", dest="by_repo", action="store_true", help="Group expenditure by repository")
    p_asum.add_argument("--by-model", action="store_true", help="Group expenditure by AI model")
    p_asum.add_argument("--ticket", type=str, default=None, help="Filter by Jira issue key")
    p_asum.add_argument("--repo", type=str, default=None, help="Filter by repository name")
    p_asum.add_argument("--provider", type=str, default=None, help="Filter by AI provider")
    p_asum.add_argument("--from", dest="date_from", type=str, default=None, help="Filter start date (YYYY-MM-DD)")
    p_asum.add_argument("--to", dest="date_to", type=str, default=None, help="Filter end date (YYYY-MM-DD)")
    p_asum.add_argument("--estimate-hooks", "--estimate", dest="estimate_hooks", action="store_true", help="Allocate and display estimated credits for unmeasured hook executions")
    p_asum.add_argument("--json", action="store_true", help="Output raw JSON summary")

    # accounting operations
    p_aops = acc_subparsers.add_parser("operations", help="List individual granular AI operation records")
    p_aops.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    p_aops.add_argument("--limit", type=int, default=50, help="Max operations to display (default: 50)")
    p_aops.add_argument("--session", type=str, default=None, help="Filter by session ID")
    p_aops.add_argument("--ticket", type=str, default=None, help="Filter by Jira issue key")
    p_aops.add_argument("--provider", type=str, default=None, help="Filter by AI provider")
    p_aops.add_argument("--type", dest="type", type=str, default=None, help="Filter by operation type")
    p_aops.add_argument("--trigger", type=str, default=None, help="Filter by trigger type")
    p_aops.add_argument("--status", type=str, default=None, help="Filter by measurement or execution status")
    p_aops.add_argument("--json", action="store_true", help="Output raw JSON array")

    # accounting operation
    p_aop = acc_subparsers.add_parser("operation", help="Display full provenance and audit trail for a single operation")
    p_aop.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    p_aop.add_argument("--id", type=str, required=True, help="Operation ID")
    p_aop.add_argument("--json", action="store_true", help="Output raw JSON record")

    # accounting reconciliation
    p_arec = acc_subparsers.add_parser("reconciliation", help="Reconcile granular operation expenditure against session/account totals")
    p_arec.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    p_arec.add_argument("--session", type=str, default=None, help="Optional session ID to reconcile")
    p_arec.add_argument("--json", action="store_true", help="Output raw JSON reconciliation report")

    # accounting hooks
    p_ahook = acc_subparsers.add_parser("hooks", help="Inspect registered hooks and recent hook execution lifecycle events")
    p_ahook.add_argument("--data-dir", type=str, default=argparse.SUPPRESS, help="Custom data storage directory (overrides TELEMETRY_DATA_DIR)")
    p_ahook.add_argument("--limit", type=int, default=50, help="Max executions to display (default: 50)")
    p_ahook.add_argument("--allocate-estimates", "--estimate", dest="allocate_estimates", action="store_true", help="Allocate estimated credits to unmeasured hook executions from correlated parent runs")
    p_ahook.add_argument("--json", action="store_true", help="Output raw JSON registry data")

    # accounting import-events
    p_aimport = acc_subparsers.add_parser("import-events", help="Safely import historical telemetry events into the accounting ledger")
    p_aimport.add_argument("--json", action="store_true", help="Output raw JSON import summary")

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
    elif args.command == "accounting":
        cmd_accounting(args)
    elif args.command == "stop":
        cmd_stop(args)


if __name__ == "__main__":
    cli_entrypoint()
