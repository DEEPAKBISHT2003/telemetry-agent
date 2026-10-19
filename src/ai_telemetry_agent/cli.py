"""CLI Interface for AI Telemetry Agent.

Provides commands to start, inspect, configure, and install the local AI telemetry collector.
"""

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Optional
import urllib.parse

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
    save_user_config,
)
from ai_telemetry_agent.core.collector import TelemetryCollector
from ai_telemetry_agent.core.identity import (
    DeveloperIdentity,
    detect_hostname,
    detect_os_username,
    enroll,
    get_identity,
    is_enrolled,
    resolve_git_developer_name,
)
from ai_telemetry_agent.integrations.hooks.installer import (
    get_global_hook_status,
    get_global_hooks_path,
    install_global_hooks,
    uninstall_global_hooks,
)
from ai_telemetry_agent.sources.antigravity import ANTIGRAVITY_AI_TELEMETRY_STATUS
from ai_telemetry_agent.logging.structured import setup_daemon_logging
from ai_telemetry_agent.storage.base import EventStore
from ai_telemetry_agent.storage.jsonl_store import JSONLEventStore
from ai_telemetry_agent.uploader.checkpoint import CheckpointManager
from ai_telemetry_agent.uploader.client import BackendClient, UploaderClientError
from ai_telemetry_agent.uploader.credentials import CredentialStore, EnrollmentMetadata
from ai_telemetry_agent.uploader.service import WindowsTaskSchedulerManager, ServiceError
from ai_telemetry_agent.uploader.uploader import TelemetryUploader, UploaderDaemon, is_daemon_running



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
    """Setup, enroll, and configure AI telemetry collection and uploading on the local machine."""
    custom_home = Path(args.custom_home).resolve() if getattr(args, "custom_home", None) else None
    user_home = custom_home if custom_home is not None else get_user_agent_home()
    user_home.mkdir(parents=True, exist_ok=True)

    cred_store = CredentialStore(custom_home=user_home)
    existing_ident = get_identity(custom_home=user_home)
    meta = cred_store.load_metadata()
    already_enrolled = cred_store.has_credential()

    _safe_print("\n" + "=" * 55)
    _safe_print("       AI Telemetry Agent — Installation")
    _safe_print("=" * 55)

    auto_yes = bool(getattr(args, "yes", False))
    re_enroll = True

    # 1. Existing enrollment check (safe & idempotent)
    if already_enrolled:
        _safe_print("\n✓ Device is already enrolled with a telemetry server:")
        if meta and meta.server_url:
            _safe_print(f"  Server URL      : {meta.server_url}")
        if meta and meta.organization_id:
            _safe_print(f"  Organization ID : {meta.organization_id}")
        if meta and meta.developer_id:
            _safe_print(f"  Developer ID    : {meta.developer_id}")
        if meta and meta.device_id:
            _safe_print(f"  Device ID       : {meta.device_id}")
        if existing_ident and existing_ident.member_name:
            _safe_print(f"  Developer Name  : {existing_ident.member_name}")
        _safe_print("")

        explicit_token = getattr(args, "token", None)
        if explicit_token:
            re_enroll = True
        elif auto_yes:
            re_enroll = False
            _safe_print("Retaining existing enrollment credentials (-y/--yes specified).")
        else:
            if not sys.stdin.isatty():
                re_enroll = False
                _safe_print("Non-interactive terminal: Retaining existing enrollment credentials.")
            else:
                try:
                    choice = input("Do you want to re-enroll this device with a new token? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    _safe_print("\nInstallation cancelled by user.")
                    sys.exit(1)
                if choice in ("y", "yes"):
                    re_enroll = True
                else:
                    re_enroll = False
                    _safe_print("Retaining existing enrollment credentials.")

    # Step A: Determine developer identity
    name_arg = getattr(args, "name", None)
    if name_arg is not None:
        if not name_arg.strip():
            _safe_print("Error: Developer name cannot be empty.", file=sys.stderr)
            sys.exit(1)
        developer_name = name_arg.strip()
    else:
        # Precedence: 1. Existing identity (if retaining) -> 2. Git local user.name -> 3. Git global user.name -> 4. "team member"
        if existing_ident and existing_ident.member_name and not re_enroll:
            default_name = existing_ident.member_name
        else:
            default_name = resolve_git_developer_name()

        if not sys.stdin.isatty() or auto_yes:
            developer_name = default_name
        else:
            prompt_str = f"Enter your developer name [{default_name}]: "
            try:
                raw_name = input(prompt_str).strip()
            except (EOFError, KeyboardInterrupt):
                _safe_print("\nInstallation cancelled by user.")
                sys.exit(1)
            developer_name = raw_name if raw_name else default_name

    if not developer_name or not developer_name.strip():
        _safe_print("Error: Developer name cannot be empty.", file=sys.stderr)
        sys.exit(1)

    developer_name = developer_name.strip()
    _safe_print(f"Developer Name: {developer_name}")

    # Steps B & C: Server URL & Token (if enrolling / re-enrolling)
    allow_insecure = bool(getattr(args, "allow_insecure_http", False))
    if re_enroll:
        # Step B: Ask for central server URL
        server_arg = getattr(args, "server", None)
        if server_arg is not None:
            if not server_arg.strip():
                _safe_print("Error: Server URL cannot be empty.", file=sys.stderr)
                sys.exit(1)
            server_url = server_arg.strip()
        else:
            default_url = meta.server_url if meta and meta.server_url else None
            prompt_str = f"Enter central telemetry server URL{' [' + default_url + ']' if default_url else ''}: "
            if not sys.stdin.isatty() or auto_yes:
                if default_url:
                    server_url = default_url
                else:
                    _safe_print("Error: --server URL is required in non-interactive mode.", file=sys.stderr)
                    sys.exit(1)
            else:
                try:
                    raw_url = input(prompt_str).strip()
                except (EOFError, KeyboardInterrupt):
                    _safe_print("\nInstallation cancelled by user.")
                    sys.exit(1)
                server_url = raw_url if raw_url else (default_url or "")

        if not server_url:
            _safe_print("Error: Server URL cannot be empty.", file=sys.stderr)
            sys.exit(1)

        server_url = server_url.rstrip("/")
        parsed = urllib.parse.urlparse(server_url)
        if not parsed.scheme or not parsed.netloc or not parsed.hostname:
            _safe_print(f"Error: Invalid server URL syntax: '{server_url}'. Must include scheme and hostname (e.g. https://telemetry.yourorg.com)", file=sys.stderr)
            sys.exit(1)

        if parsed.scheme.lower() not in ("http", "https"):
            _safe_print(f"Error: Unsupported URL scheme '{parsed.scheme}'. Must be 'https' (or 'http' with --allow-insecure-http).", file=sys.stderr)
            sys.exit(1)

        if parsed.scheme.lower() == "http" and not allow_insecure:
            if sys.stdin.isatty() and not auto_yes:
                try:
                    confirm = input("Warning: Server URL uses unencrypted HTTP. Allow insecure HTTP for testing? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    _safe_print("\nInstallation cancelled by user.")
                    sys.exit(1)
                if confirm in ("y", "yes"):
                    allow_insecure = True
                else:
                    _safe_print("Error: Insecure HTTP rejected. Centralized telemetry requires HTTPS.", file=sys.stderr)
                    sys.exit(1)
            else:
                _safe_print("Error: Insecure HTTP rejected. Use HTTPS or pass --allow-insecure-http.", file=sys.stderr)
                sys.exit(1)

        _safe_print(f"Server URL: {server_url}")

        # Step C: Ask for one-time enrollment token
        token_arg = getattr(args, "token", None)
        if token_arg is not None:
            if not token_arg.strip():
                _safe_print("Error: Enrollment token cannot be empty.", file=sys.stderr)
                sys.exit(1)
            enrollment_token = token_arg.strip()
        else:
            if not sys.stdin.isatty() or auto_yes:
                _safe_print("Error: --token is required in non-interactive mode.", file=sys.stderr)
                sys.exit(1)
            try:
                enrollment_token = getpass.getpass("Enter one-time enrollment token (input hidden): ").strip()
            except (EOFError, KeyboardInterrupt):
                _safe_print("\nInstallation cancelled by user.")
                sys.exit(1)

        if not enrollment_token:
            _safe_print("Error: Enrollment token cannot be empty.", file=sys.stderr)
            sys.exit(1)

        # Step D: Enroll the device
        _safe_print("\nSubmitting enrollment claim...")
        client = BackendClient(server_url=server_url, allow_insecure_http=allow_insecure)
        hostname = detect_hostname()
        platform_name = f"{platform.system()} {platform.release()}"
        hardware_uuid = getattr(args, "device", None) or get_or_create_device_id(custom_home=user_home)

        try:
            claim_resp = client.claim_enrollment(
                enrollment_token=enrollment_token,
                hardware_uuid=hardware_uuid,
                hostname=hostname,
                platform_name=platform_name,
                agent_version="0.1.4",
            )
        except UploaderClientError as uce:
            _safe_print(f"\nEnrollment claim failed: {uce}", file=sys.stderr)
            sys.exit(1)
        except Exception as ex:
            _safe_print(f"\nUnexpected error during enrollment claim: {ex}", file=sys.stderr)
            sys.exit(1)

        credential_token = claim_resp.get("device_credential") or claim_resp.get("credential_token")
        if not credential_token:
            _safe_print("\nError: Server response missing device credential token.", file=sys.stderr)
            sys.exit(1)

        device_info = claim_resp.get("device") if isinstance(claim_resp.get("device"), dict) else {}
        dev_info = claim_resp.get("developer") if isinstance(claim_resp.get("developer"), dict) else {}
        org_id = str(claim_resp.get("organization_id") or dev_info.get("organization_id", ""))
        dev_id = str(claim_resp.get("developer_id") or dev_info.get("developer_id", ""))
        device_id = str(claim_resp.get("device_id") or device_info.get("device_id", ""))
        token_prefix = str(claim_resp.get("token_prefix", ""))
        enrolled_at = str(claim_resp.get("enrolled_at") or claim_resp.get("claimed_at") or datetime.now(timezone.utc).isoformat())
        expires_at = claim_resp.get("expires_at")

        metadata = EnrollmentMetadata(
            server_url=server_url,
            organization_id=org_id,
            developer_id=dev_id,
            device_id=device_id,
            token_prefix=token_prefix,
            enrolled_at=enrolled_at,
            expires_at=expires_at,
            hardware_uuid=hardware_uuid,
        )

        cred_store.save_credential(token=credential_token, metadata=metadata)

        _safe_print("Verifying device credentials...")
        try:
            client.verify_device_identity(credential_token)
            _safe_print("✓ Device credential verified successfully.")
        except Exception as ex:
            _safe_print(f"Warning: Identity verification warning: {ex}", file=sys.stderr)

        resolved_member_id = dev_id or None
        final_server_url = server_url
    else:
        # Retaining existing enrollment
        hardware_uuid = getattr(args, "device", None) or get_or_create_device_id(custom_home=user_home)
        hostname = detect_hostname()
        resolved_member_id = meta.developer_id if meta and meta.developer_id else (existing_ident.member_id if existing_ident else None)
        final_server_url = meta.server_url if meta and meta.server_url else None

    # Persist Developer Identity locally
    try:
        ident = enroll(
            member_name=developer_name,
            member_id=resolved_member_id,
            device_id=hardware_uuid,
            hostname=hostname,
            custom_home=user_home,
        )
        _safe_print("✓ Developer identity saved locally.")
    except Exception as e:
        _safe_print(f"Error: Failed to save developer identity: {e}", file=sys.stderr)
        sys.exit(1)

    # Step E: Install Antigravity Lifecycle Hooks
    _safe_print("Installing global Antigravity lifecycle hooks...")
    hooks_file = Path(args.config) if getattr(args, "config", None) else None
    success, msg, backup_path, other_count = install_global_hooks(
        hooks_file=hooks_file,
        python_exe=getattr(args, "python", None) or sys.executable,
    )
    if not success:
        _safe_print(f"Error: Failed to install lifecycle hooks: {msg}", file=sys.stderr)
        sys.exit(1)
    _safe_print("✓ Antigravity lifecycle hooks installed.")

    # Step F: Enable uploads
    config_dict = {
        "TELEMETRY_UPLOAD_ENABLED": "true",
        "MEMBER_ID": resolved_member_id or (ident.member_id if 'ident' in locals() else ""),
        "DEVICE_ID": hardware_uuid,
    }
    if final_server_url:
        config_dict["TELEMETRY_SERVER_URL"] = final_server_url
    if allow_insecure:
        config_dict["TELEMETRY_ALLOW_INSECURE_HTTP"] = "true"

    try:
        saved_cfg_path = save_user_config(config_dict, custom_home=user_home)
        _safe_print(f"✓ Upload configuration enabled and persisted to {saved_cfg_path.name}.")
    except Exception as ex:
        _safe_print(f"Error: Failed to persist upload configuration: {ex}", file=sys.stderr)
        sys.exit(1)

    # Optional background service installation
    install_service_flag = bool(getattr(args, "install_service", False))
    should_install_service = False
    if install_service_flag:
        should_install_service = True
    elif sys.stdin.isatty() and not auto_yes and platform.system() == "Windows":
        try:
            choice = input("\nDo you want to install and start the persistent background uploader service? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                should_install_service = True
        except (EOFError, KeyboardInterrupt, StopIteration):
            pass

    service_installed_ok = False
    if should_install_service:
        _safe_print("\nConfiguring persistent Windows Task Scheduler background uploader...")
        mgr = WindowsTaskSchedulerManager(custom_home=user_home)
        ok, msg = mgr.install(start=True)
        if ok:
            service_installed_ok = True
            _safe_print("✓ Persistent background uploader service installed and started.")
        else:
            _safe_print(f"\nWarning: Failed to install background service: {msg}", file=sys.stderr)
            _safe_print("Installation succeeded partially (credentials, hooks, and configuration were saved).", file=sys.stderr)
            _safe_print("You can retry installing the background service anytime using:", file=sys.stderr)
            _safe_print("  telemetry-agent service install --start\n", file=sys.stderr)

    _safe_print("\n" + "=" * 55)
    _safe_print("✓ Installation Complete!")
    _safe_print(f"  Developer : {developer_name}")
    if final_server_url:
        _safe_print(f"  Server    : {final_server_url}")
    _safe_print(f"  Device ID : {hardware_uuid}")
    _safe_print("  Hooks     : Installed in ~/.gemini/config/hooks.json")
    _safe_print("  Uploads   : Configured and enabled")
    if service_installed_ok:
        _safe_print("  Service   : Background scheduled task registered and running")
    else:
        _safe_print("  Note      : To register the persistent background service, run:")
        _safe_print("              telemetry-agent service install --start")
    _safe_print("=" * 55 + "\n")


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
    print(f"Storage    : {settings.data_dir}")
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

    if s.get("has_token_data"):
        print("\nToken Usage (Authoritative):")
        print(f"  Uncached Input Tokens : {s.get('input_tokens', 0):,}")
        print(f"  Cached Read Tokens    : {s.get('cache_read_tokens', 0):,}")
        print(f"  Output Tokens         : {s.get('output_tokens', 0):,}")
        print(f"  Total Tokens          : {s.get('total_tokens', 0):,}")

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
        data_dir = settings.data_dir
    except Exception:
        data_dir = (get_user_agent_home() / "data").resolve()

    pid_file = data_dir / "collector.pid"
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


def cmd_enroll(args: argparse.Namespace) -> None:
    """Enroll device with centralized backend using single-use enrollment token."""
    server_url = (getattr(args, "server", None) or "").strip()
    enrollment_token = (getattr(args, "token", None) or "").strip()
    allow_insecure = bool(getattr(args, "allow_insecure_http", False))

    if not server_url:
        print("Error: --server URL is required (e.g. https://telemetry.yourorg.com)", file=sys.stderr)
        sys.exit(1)
    if not enrollment_token:
        print("Error: --token is required", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Device Enrollment")
    print("=" * 50)
    print(f"Server URL: {server_url}")

    user_home = get_user_agent_home()
    cred_store = CredentialStore(custom_home=user_home)

    client = BackendClient(
        server_url=server_url,
        allow_insecure_http=allow_insecure,
    )

    hostname = detect_hostname()
    platform_name = f"{platform.system()} {platform.release()}"
    hardware_uuid = get_or_create_device_id(custom_home=user_home)

    print(f"Hostname   : {hostname}")
    print(f"Platform   : {platform_name}")
    print(f"Device UUID: {hardware_uuid}")
    print("\nSubmitting enrollment claim...")

    try:
        claim_resp = client.claim_enrollment(
            enrollment_token=enrollment_token,
            hardware_uuid=hardware_uuid,
            hostname=hostname,
            platform_name=platform_name,
            agent_version="0.1.4",
        )
    except UploaderClientError as uce:
        print(f"\nEnrollment claim failed: {uce}", file=sys.stderr)
        sys.exit(1)
    except Exception as ex:
        print(f"\nUnexpected error claiming enrollment: {ex}", file=sys.stderr)
        sys.exit(1)

    credential_token = claim_resp.get("device_credential") or claim_resp.get("credential_token")
    if not credential_token:
        print("\nError: Server response missing device credential token.", file=sys.stderr)
        sys.exit(1)

    device_info = claim_resp.get("device") if isinstance(claim_resp.get("device"), dict) else {}
    dev_info = claim_resp.get("developer") if isinstance(claim_resp.get("developer"), dict) else {}
    org_id = str(claim_resp.get("organization_id") or dev_info.get("organization_id", ""))
    dev_id = str(claim_resp.get("developer_id") or dev_info.get("developer_id", ""))
    device_id = str(claim_resp.get("device_id") or device_info.get("device_id", ""))
    token_prefix = str(claim_resp.get("token_prefix", ""))
    enrolled_at = str(claim_resp.get("enrolled_at") or claim_resp.get("claimed_at") or datetime.now(timezone.utc).isoformat())
    expires_at = claim_resp.get("expires_at")

    metadata = EnrollmentMetadata(
        server_url=server_url,
        organization_id=org_id,
        developer_id=dev_id,
        device_id=device_id,
        token_prefix=token_prefix,
        enrolled_at=enrolled_at,
        expires_at=expires_at,
        hardware_uuid=hardware_uuid,
    )

    cred_store.save_credential(token=credential_token, metadata=metadata)

    # Verify device authentication against GET /api/v1/devices/me
    print("Verifying device credentials...")
    try:
        client.verify_device_identity(credential_token)
        print("✓ Verification succeeded.")
    except Exception as ex:
        print(f"Warning: Identity verification failed: {ex}", file=sys.stderr)

    print("\n" + "=" * 50)
    print("✓ Device Enrollment Complete!")
    print(f"Developer ID : {dev_id}")
    print(f"Device ID    : {device_id}")
    print(f"Token Prefix : {token_prefix}")
    print(f"Status       : Enrolled and Securely Persisted")
    print("=" * 50 + "\n")


def cmd_upload(args: argparse.Namespace) -> None:
    """Upload pending telemetry to central server."""
    is_daemon = bool(getattr(args, "daemon", False))
    override_server = getattr(args, "server", None)
    allow_insecure = bool(getattr(args, "allow_insecure_http", False))

    settings = load_settings()
    if override_server:
        object.__setattr__(settings, "telemetry_server_url", override_server)
    if allow_insecure:
        object.__setattr__(settings, "allow_insecure_http", True)

    uploader = TelemetryUploader(settings=settings)

    if is_daemon:
        setup_daemon_logging(level_name=settings.log_level)
        print("\n" + "=" * 50)
        print("AI Telemetry Agent — Starting Background Uploader")
        print("=" * 50)
        print(f"Interval: {settings.upload_interval_seconds}s")
        print("Press Ctrl+C to terminate cleanly.\n")

        daemon = UploaderDaemon(uploader=uploader)
        started = daemon.start()
        if not started:
            if daemon.status == "ALREADY_RUNNING":
                print("Notice: Telemetry uploader daemon is already running (instance lock held). Exiting.")
                sys.exit(0)
            else:
                print(f"Error: Failed to start uploader daemon: {daemon.status}")
                sys.exit(1)

        def _handle_shutdown(signum, frame):
            print("\nShutting down uploader daemon...")
            daemon.stop()
            sys.exit(0)

        import signal
        try:
            signal.signal(signal.SIGINT, _handle_shutdown)
            signal.signal(signal.SIGTERM, _handle_shutdown)
        except Exception:
            pass

        try:
            while daemon.is_alive():
                time.sleep(1.0)
        except KeyboardInterrupt:
            daemon.stop()
    else:
        print("\n" + "=" * 50)
        print("AI Telemetry Agent — Telemetry Upload (Single Pass)")
        print("=" * 50)
        res = uploader.upload_cycle(force=True)
        print(f"Status           : {res.status}")
        print(f"Batches Sent     : {res.batches_submitted}")
        print(f"Events Uploaded  : {res.events_submitted}")
        print(f"Newly Persisted  : {res.newly_persisted_count}")
        print(f"Duplicates       : {res.duplicate_count}")
        if res.error_message:
            print(f"Message/Error    : {res.error_message}")
        print("=" * 50 + "\n")
        if res.status in ("AUTH_ERROR", "ERROR"):
            sys.exit(1)


def cmd_upload_status(args: argparse.Namespace) -> None:
    """Display upload synchronization, enrollment, and background service status."""
    custom_home = Path(args.custom_home).resolve() if getattr(args, "custom_home", None) else None
    user_home = custom_home if custom_home is not None else get_user_agent_home()

    settings = load_settings()
    uploader = TelemetryUploader(settings=settings)
    status = uploader.get_status()

    daemon_active = is_daemon_running(custom_home=user_home)
    status["daemon_running"] = daemon_active

    svc_manager = WindowsTaskSchedulerManager(custom_home=user_home)
    try:
        svc_info = svc_manager.status()
    except Exception as ex:
        svc_info = {
            "platform": platform.system(),
            "supported": platform.system() == "Windows",
            "installed": False,
            "registered": False,
            "error": str(ex),
        }
    status["background_service"] = svc_info

    if getattr(args, "json", False):
        print(json.dumps(status, indent=2))
        return

    print("\n" + "=" * 50)
    print("AI Telemetry Agent — Upload & Enrollment Status")
    print("=" * 50)
    print(f"Automatic Upload Enabled : {'Yes' if status['upload_enabled'] else 'No'}")
    print(f"Device Enrolled          : {'Yes' if status['enrolled'] else 'No'}")
    print(f"Server URL               : {status['server_url'] or '(not configured)'}")
    print(f"Device ID                : {status['device_id']}")
    print(f"Member ID                : {status['member_id']}")
    print(f"Pending Events Estimate  : {status['estimated_pending_events']}")
    print(f"Tracked JSONL Files      : {status['files_tracked']}")
    print(f"Daemon Process Running   : {'Yes' if daemon_active else 'No'}")
    if svc_info.get("supported"):
        print(f"Background Task (Service):")
        print(f"  Registered             : {'Yes' if svc_info.get('registered') or svc_info.get('installed') else 'No'}")
        print(f"  State                  : {svc_info.get('state', 'Unknown')}")
        if svc_info.get("last_run_time"):
            print(f"  Last Run Time          : {svc_info.get('last_run_time')}")
        if svc_info.get("last_result"):
            print(f"  Last Run Result        : {svc_info.get('last_result')}")
    else:
        print(f"Background Task (Service): Not supported on {platform.system()}")
    print("=" * 50 + "\n")


def cmd_service(args: argparse.Namespace) -> None:
    """Manage persistent Windows background uploader service."""
    action = getattr(args, "service_action", None)
    if not action:
        _safe_print("Error: service action required (install, uninstall, status, start, stop, restart)", file=sys.stderr)
        sys.exit(1)

    custom_home = Path(args.custom_home).resolve() if getattr(args, "custom_home", None) else None
    user_home = custom_home if custom_home is not None else get_user_agent_home()

    manager = WindowsTaskSchedulerManager(custom_home=user_home)

    if action == "install":
        start_after = bool(getattr(args, "start", False))
        _safe_print("Installing Windows background uploader task...")
        ok, msg = manager.install(start=start_after)
        if not ok:
            _safe_print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        _safe_print(f"✓ {msg}")

    elif action == "uninstall":
        _safe_print("Uninstalling Windows background uploader task...")
        ok, msg = manager.uninstall()
        if not ok:
            _safe_print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        _safe_print(f"✓ {msg}")

    elif action == "status":
        svc_status = manager.status()
        if getattr(args, "json", False):
            print(json.dumps(svc_status, indent=2))
        else:
            _safe_print("\n" + "=" * 50)
            _safe_print("AI Telemetry Agent — Windows Background Service Status")
            _safe_print("=" * 50)
            _safe_print(f"Task Name              : {svc_status.get('task_name', manager.task_name)}")
            _safe_print(f"Platform Supported     : {'Yes' if svc_status.get('supported') else 'No'}")
            _safe_print(f"Registered             : {'Yes' if svc_status.get('registered') or svc_status.get('installed') else 'No'}")
            _safe_print(f"State                  : {svc_status.get('state', 'Unknown')}")
            _safe_print(f"Enabled                : {'Yes' if svc_status.get('enabled') else 'No'}")
            _safe_print(f"Daemon Process Running : {'Yes' if svc_status.get('is_daemon_running') else 'No'}")
            if svc_status.get("last_run_time"):
                _safe_print(f"Last Run Time          : {svc_status.get('last_run_time')}")
            if svc_status.get("last_result"):
                _safe_print(f"Last Run Result        : {svc_status.get('last_result')}")
            if svc_status.get("next_run_time"):
                _safe_print(f"Next Run Time          : {svc_status.get('next_run_time')}")
            if svc_status.get("error"):
                _safe_print(f"Error                  : {svc_status.get('error')}")
            _safe_print("=" * 50 + "\n")

    elif action == "start":
        _safe_print(f"Starting task '{manager.task_name}'...")
        ok, msg = manager.start()
        if not ok:
            _safe_print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        _safe_print(f"✓ {msg}")

    elif action == "stop":
        _safe_print(f"Stopping task '{manager.task_name}'...")
        ok, msg = manager.stop()
        if not ok:
            _safe_print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        _safe_print(f"✓ {msg}")

    elif action == "restart":
        _safe_print(f"Restarting task '{manager.task_name}'...")
        ok, msg = manager.restart()
        if not ok:
            _safe_print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)
        _safe_print(f"✓ {msg}")

    else:
        _safe_print(f"Error: Unknown service action '{action}'.", file=sys.stderr)
        sys.exit(1)




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry-agent",
        description="Local AI and Engineering Telemetry Agent v0.1.4",
    )
    parser.add_argument("--env-file", type=str, default=None, help="Path to .env configuration file")

    subparsers = parser.add_subparsers(dest="command", help="Agent actions")

    # Install command
    p_install = subparsers.add_parser(
        "install",
        help="Setup, enroll developer and device, install hooks, and configure AI telemetry",
    )
    p_install.add_argument("--name", "-n", type=str, default=None, help="Developer display name")
    p_install.add_argument("--server", "-s", type=str, default=None, help="Central telemetry server URL (e.g. https://telemetry.yourorg.com)")
    p_install.add_argument(
        "--token", "-t", type=str, default=None,
        help="One-time enrollment token (WARNING: supplying secrets via CLI flags may be logged in shell history; prefer interactive masked prompt)",
    )
    p_install.add_argument("--allow-insecure-http", action="store_true", help="Allow unencrypted HTTP (dev/testing only)")
    p_install.add_argument("-y", "--yes", action="store_true", help="Automatic confirmation (accept defaults / retain existing enrollment)")
    p_install.add_argument("--install-service", action="store_true", help="Install and start persistent Windows background service after installation")
    p_install.add_argument("--member", type=str, default=None, help="Configure member identity (default: system username or assigned ID)")
    p_install.add_argument("--device", type=str, default=None, help="Configure device ID (default: persistent UUID)")
    p_install.add_argument("--config", type=str, default=None, help="Custom hooks.json path")
    p_install.add_argument("--python", type=str, default=None, help="Custom python executable path")
    p_install.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

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

    # Phase 5.8: Central telemetry upload & enrollment commands
    p_enroll = subparsers.add_parser("enroll", help="Enroll this device with the central telemetry backend")
    p_enroll.add_argument("--server", "-s", type=str, required=True, help="Central telemetry server URL (e.g. https://telemetry.internal)")
    p_enroll.add_argument("--token", "-t", type=str, required=True, help="Single-use enrollment token")
    p_enroll.add_argument("--allow-insecure-http", action="store_true", help="Allow unencrypted HTTP (dev/testing only)")

    p_upload = subparsers.add_parser("upload", help="Upload pending telemetry to central server")
    p_upload.add_argument("--once", action="store_true", help="Execute a single upload cycle and exit (default)")
    p_upload.add_argument("--daemon", action="store_true", help="Run uploader continuously in background loop")
    p_upload.add_argument("--server", type=str, default=None, help="Override server URL")
    p_upload.add_argument("--allow-insecure-http", action="store_true", help="Allow unencrypted HTTP")

    p_up_status = subparsers.add_parser("upload-status", help="Show upload synchronization and enrollment status")
    p_up_status.add_argument("--json", action="store_true", help="Output raw JSON")
    p_up_status.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    # Windows background service management commands
    p_service = subparsers.add_parser(
        "service",
        help="Manage persistent Windows Task Scheduler background uploader service",
    )
    p_service.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)
    svc_subparsers = p_service.add_subparsers(dest="service_action", help="Service management actions")

    p_svc_install = svc_subparsers.add_parser("install", help="Install Windows background scheduled task")
    p_svc_install.add_argument("--start", action="store_true", help="Start service immediately after installation")
    p_svc_install.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    p_svc_uninstall = svc_subparsers.add_parser("uninstall", help="Uninstall Windows background scheduled task")
    p_svc_uninstall.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    p_svc_status = svc_subparsers.add_parser("status", help="Show Windows background service status")
    p_svc_status.add_argument("--json", action="store_true", help="Output raw JSON")
    p_svc_status.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    p_svc_start = svc_subparsers.add_parser("start", help="Start background scheduled task")
    p_svc_start.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    p_svc_stop = svc_subparsers.add_parser("stop", help="Stop background scheduled task")
    p_svc_stop.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

    p_svc_restart = svc_subparsers.add_parser("restart", help="Restart background scheduled task")
    p_svc_restart.add_argument("--custom-home", type=str, default=None, help=argparse.SUPPRESS)

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
    elif args.command == "enroll":
        cmd_enroll(args)
    elif args.command == "upload":
        cmd_upload(args)
    elif args.command == "upload-status":
        cmd_upload_status(args)
    elif args.command == "service":
        cmd_service(args)


if __name__ == "__main__":
    cli_entrypoint()

