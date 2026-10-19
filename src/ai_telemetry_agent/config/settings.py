"""Configuration settings and environment variable parser for AI Telemetry Agent."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, List, Optional, Union
import uuid


class ConfigurationError(ValueError):
    """Raised when configuration values are missing or invalid."""
    pass


def get_user_agent_home() -> Path:
    """Return the persistent user-level telemetry agent directory (~/.telemetry_agent)."""
    override = os.environ.get("TELEMETRY_AGENT_HOME")
    if override:
        return Path(override).resolve()
    return (Path.home() / ".telemetry_agent").resolve()


def resolve_data_dir(
    custom_path: Optional[Union[str, Path]] = None,
    settings: Optional[Any] = None,
) -> Path:
    """Resolve the active data directory consistently across all stores and CLI.

    Priority:
    1. Explicit custom_path passed to constructor or CLI argument.
    2. settings.data_dir if Settings instance is provided.
    3. TELEMETRY_DATA_DIR environment variable.
    4. Default fallback: ~/.telemetry_agent/data.
    """
    if custom_path:
        p = Path(custom_path)
        return (PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
    if settings is not None and getattr(settings, "data_dir", None):
        return Path(settings.data_dir).resolve()
    override = os.environ.get("TELEMETRY_DATA_DIR")
    if override and override.strip():
        p = Path(override.strip())
        return (PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
    return (get_user_agent_home() / "data").resolve()


def get_or_create_device_id(custom_home: Optional[Path] = None) -> str:
    """Return persistent device ID from storage or generate and persist a new unique device ID.

    The device ID:
    - Is unique per machine / installation.
    - Persists across agent restarts in ~/.telemetry_agent/device_id.
    - Does not expose hostname or sensitive information.
    """
    home = custom_home or get_user_agent_home()
    dev_id_file = home / "device_id"

    if dev_id_file.is_file():
        try:
            val = dev_id_file.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass

    # Generate unique persistent device ID
    new_id = f"dev-{uuid.uuid4().hex[:12]}"
    try:
        home.mkdir(parents=True, exist_ok=True)
        dev_id_file.write_text(new_id + "\n", encoding="utf-8")
    except Exception:
        pass
    return new_id


def get_default_member_id(custom_home: Optional[Path] = None) -> str:
    """Resolve default human member ID from environment, persistent file, or OS user name."""
    # 1. Check persistent member_id file
    home = custom_home or get_user_agent_home()
    member_file = home / "member_id"
    if member_file.is_file():
        try:
            val = member_file.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass

    # 2. Check OS username
    os_user = os.environ.get("USERNAME") or os.environ.get("USER")
    if os_user and os_user.strip():
        return os_user.strip()

    return "developer"


def parse_dotenv(env_path: Path) -> dict:
    """Parse a simple .env file without external dependencies."""
    env_vars = {}
    if not env_path.is_file():
        return env_vars

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip quotes if present
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    env_vars[key] = val
    except Exception:
        pass
    return env_vars


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


VALID_TELEMETRY_SOURCES = {"antigravity", "kiro_ide", "kiro_cli", "auto"}


@dataclass(frozen=True)
class Settings:
    member_id: str
    device_id: str
    member_name: Optional[str] = None
    hostname: Optional[str] = None
    collector_version: str = "0.1.2"
    log_level: str = "INFO"
    heartbeat_interval_seconds: int = 30
    telemetry_data_dir: Optional[str] = None
    git_monitor_interval_seconds: int = 10
    git_monitor_paths: List[str] = field(default_factory=lambda: ["."])
    antigravity_monitor_enabled: bool = True
    telemetry_source: str = "antigravity"
    kiro_sessions_dir: Optional[str] = None
    kiro_monitor_enabled: bool = True
    jira_base_url: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_username: Optional[str] = None
    jira_auth_type: str = "pat"
    jira_key_pattern: Optional[str] = None

    @property
    def data_dir(self) -> Path:
        """Return the persistent directory storing date-based JSONL files (~/.telemetry_agent/data)."""
        override = os.environ.get("TELEMETRY_DATA_DIR")
        if override and override.strip():
            p = Path(override.strip())
            return (PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
        if self.telemetry_data_dir:
            p = Path(self.telemetry_data_dir)
            return (PROJECT_ROOT / p).resolve() if not p.is_absolute() else p.resolve()
        return (get_user_agent_home() / "data").resolve()

    @property
    def app_data_dir(self) -> Path:
        return get_user_agent_home()


def load_settings(
    env_file: Optional[str] = None,
    member_id: Optional[str] = None,
    device_id: Optional[str] = None,
    overrides: Optional[dict] = None
) -> Settings:
    """Load settings from environment variables, .env file, persistent storage, or direct parameters.

    Raises:
        ConfigurationError: If MEMBER_ID or DEVICE_ID cannot be resolved.
    """
    env_vars = {}
    user_home = get_user_agent_home()

    # 1. Load from custom or default .env files
    search_paths = []
    if env_file:
        search_paths.append(Path(env_file))
    else:
        search_paths.extend([
            user_home / "config.env",
            PROJECT_ROOT / ".env",
            Path(".env"),
        ])

    for path in search_paths:
        if path.is_file():
            env_vars.update(parse_dotenv(path))
            break

    # 2. Check persistent identity.json in user_home if no explicit env_file is provided
    member_name = None
    hostname = None
    if env_file is None:
        ident_file = user_home / "identity.json"
        if ident_file.is_file():
            try:
                ident_data = json.loads(ident_file.read_text(encoding="utf-8"))
                if isinstance(ident_data, dict):
                    if not env_vars.get("MEMBER_ID") and ident_data.get("member_id"):
                        env_vars["MEMBER_ID"] = str(ident_data["member_id"]).strip()
                    if not env_vars.get("DEVICE_ID") and ident_data.get("device_id"):
                        env_vars["DEVICE_ID"] = str(ident_data["device_id"]).strip()
                    member_name = ident_data.get("member_name")
                    hostname = ident_data.get("hostname")
            except Exception:
                pass

    # 3. Layer with actual OS environment variables
    for key in [
        "MEMBER_ID", "DEVICE_ID", "COLLECTOR_VERSION", "LOG_LEVEL",
        "HEARTBEAT_INTERVAL_SECONDS", "TELEMETRY_DATA_DIR",
        "GIT_MONITOR_INTERVAL_SECONDS", "GIT_MONITOR_PATHS",
        "ANTIGRAVITY_MONITOR_ENABLED", "TELEMETRY_SOURCE",
        "KIRO_SESSIONS_DIR", "KIRO_MONITOR_ENABLED",
        "JIRA_BASE_URL", "JIRA_API_TOKEN", "JIRA_PAT",
        "JIRA_USERNAME", "JIRA_AUTH_TYPE", "JIRA_KEY_PATTERN"
    ]:
        val = os.environ.get(key)
        if val is not None:
            env_vars[key] = val

    # 4. Layer with explicit overrides
    if overrides:
        env_vars.update(overrides)

    if member_id:
        env_vars["MEMBER_ID"] = member_id
    if device_id:
        env_vars["DEVICE_ID"] = device_id

    # Resolve member_id and device_id
    final_member_id = (env_vars.get("MEMBER_ID") or "").strip()
    final_device_id = (env_vars.get("DEVICE_ID") or "").strip()

    if env_file is None:
        if not final_member_id:
            final_member_id = get_default_member_id(user_home)
        if not final_device_id:
            final_device_id = get_or_create_device_id(user_home)

    errors = []
    if not final_member_id:
        errors.append("MEMBER_ID is required. Set it in .env or via environment variable.")
    if not final_device_id:
        errors.append("DEVICE_ID is required. Set it in .env or via environment variable.")

    # Validate TELEMETRY_SOURCE
    raw_source = (env_vars.get("TELEMETRY_SOURCE") or "antigravity").strip().lower()
    if raw_source not in VALID_TELEMETRY_SOURCES:
        errors.append(
            f"Invalid TELEMETRY_SOURCE '{raw_source}'. "
            f"Supported values: {', '.join(sorted(VALID_TELEMETRY_SOURCES))}."
        )

    if errors:
        raise ConfigurationError("Configuration Error:\n" + "\n".join(f"- {err}" for err in errors))

    version = env_vars.get("COLLECTOR_VERSION", "0.1.2").strip()
    log_level = env_vars.get("LOG_LEVEL", "INFO").strip().upper()

    try:
        heartbeat_sec = int(env_vars.get("HEARTBEAT_INTERVAL_SECONDS", 30))
    except ValueError:
        heartbeat_sec = 30

    raw_data_dir = env_vars.get("TELEMETRY_DATA_DIR", "").strip() or None

    try:
        git_interval = int(env_vars.get("GIT_MONITOR_INTERVAL_SECONDS", 10))
    except ValueError:
        git_interval = 10

    raw_git_paths = env_vars.get("GIT_MONITOR_PATHS", ".")
    if isinstance(raw_git_paths, str):
        git_paths = [p.strip() for p in raw_git_paths.split(",") if p.strip()]
    elif isinstance(raw_git_paths, list):
        git_paths = raw_git_paths
    else:
        git_paths = ["."]

    ag_enabled_raw = env_vars.get("ANTIGRAVITY_MONITOR_ENABLED", "true")
    if isinstance(ag_enabled_raw, bool):
        ag_enabled = ag_enabled_raw
    else:
        ag_enabled = str(ag_enabled_raw).strip().lower() in ("true", "1", "yes", "t")

    raw_kiro_dir = env_vars.get("KIRO_SESSIONS_DIR", "").strip() or None

    kiro_enabled_raw = env_vars.get("KIRO_MONITOR_ENABLED", "true")
    if isinstance(kiro_enabled_raw, bool):
        kiro_enabled = kiro_enabled_raw
    else:
        kiro_enabled = str(kiro_enabled_raw).strip().lower() in ("true", "1", "yes", "t")

    raw_jira_url = env_vars.get("JIRA_BASE_URL", "").strip() or None
    raw_jira_token = env_vars.get("JIRA_API_TOKEN") or env_vars.get("JIRA_PAT")
    raw_jira_username = env_vars.get("JIRA_USERNAME", "").strip() or None
    raw_jira_auth = (env_vars.get("JIRA_AUTH_TYPE") or "pat").strip().lower()
    raw_jira_pattern = env_vars.get("JIRA_KEY_PATTERN", "").strip() or None

    return Settings(
        member_id=final_member_id,
        device_id=final_device_id,
        member_name=member_name,
        hostname=hostname,
        collector_version=version,
        log_level=log_level,
        heartbeat_interval_seconds=heartbeat_sec,
        telemetry_data_dir=raw_data_dir,
        git_monitor_interval_seconds=git_interval,
        git_monitor_paths=git_paths,
        antigravity_monitor_enabled=ag_enabled,
        telemetry_source=raw_source,
        kiro_sessions_dir=raw_kiro_dir,
        kiro_monitor_enabled=kiro_enabled,
        jira_base_url=raw_jira_url,
        jira_api_token=raw_jira_token,
        jira_username=raw_jira_username,
        jira_auth_type=raw_jira_auth,
        jira_key_pattern=raw_jira_pattern,
    )
