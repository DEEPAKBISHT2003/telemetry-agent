"""Configuration settings and environment variable parser for AI Telemetry Agent."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import List, Optional
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
    telemetry_server_url: Optional[str] = None
    upload_enabled: bool = False
    upload_interval_seconds: int = 60
    upload_timeout_seconds: float = 15.0
    upload_max_batch_size: int = 500
    upload_max_batch_bytes: int = 2 * 1024 * 1024
    upload_max_retries: int = 3
    upload_backoff_factor: float = 1.5
    allow_insecure_http: bool = False

    @property
    def data_dir(self) -> Path:
        """Return the persistent directory storing date-based JSONL files (~/.telemetry_agent/data)."""
        override = os.environ.get("TELEMETRY_DATA_DIR")
        if override:
            return Path(override).resolve()
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
        "ANTIGRAVITY_MONITOR_ENABLED", "TELEMETRY_SERVER_URL",
        "TELEMETRY_UPLOAD_ENABLED", "UPLOAD_ENABLED",
        "TELEMETRY_UPLOAD_INTERVAL_SECONDS", "UPLOAD_INTERVAL_SECONDS",
        "TELEMETRY_UPLOAD_TIMEOUT_SECONDS", "UPLOAD_TIMEOUT_SECONDS",
        "TELEMETRY_UPLOAD_MAX_BATCH_SIZE", "UPLOAD_MAX_BATCH_SIZE",
        "TELEMETRY_UPLOAD_MAX_BATCH_BYTES", "UPLOAD_MAX_BATCH_BYTES",
        "TELEMETRY_UPLOAD_MAX_RETRIES", "UPLOAD_MAX_RETRIES",
        "TELEMETRY_UPLOAD_BACKOFF_FACTOR", "UPLOAD_BACKOFF_FACTOR",
        "TELEMETRY_ALLOW_INSECURE_HTTP", "ALLOW_INSECURE_HTTP",
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

    # Uploader configuration & validation
    server_url = env_vars.get("TELEMETRY_SERVER_URL", "").strip() or None

    upload_enabled_raw = env_vars.get("TELEMETRY_UPLOAD_ENABLED", env_vars.get("UPLOAD_ENABLED", "false"))
    if isinstance(upload_enabled_raw, bool):
        upload_enabled = upload_enabled_raw
    else:
        upload_enabled = str(upload_enabled_raw).strip().lower() in ("true", "1", "yes", "t")

    try:
        upload_interval = max(1, int(env_vars.get("TELEMETRY_UPLOAD_INTERVAL_SECONDS", env_vars.get("UPLOAD_INTERVAL_SECONDS", 60))))
    except ValueError:
        upload_interval = 60

    try:
        upload_timeout = max(1.0, float(env_vars.get("TELEMETRY_UPLOAD_TIMEOUT_SECONDS", env_vars.get("UPLOAD_TIMEOUT_SECONDS", 15.0))))
    except ValueError:
        upload_timeout = 15.0

    try:
        raw_batch_size = int(env_vars.get("TELEMETRY_UPLOAD_MAX_BATCH_SIZE", env_vars.get("UPLOAD_MAX_BATCH_SIZE", 500)))
        upload_max_batch_size = max(1, min(500, raw_batch_size))
    except ValueError:
        upload_max_batch_size = 500

    try:
        raw_max_bytes = int(env_vars.get("TELEMETRY_UPLOAD_MAX_BATCH_BYTES", env_vars.get("UPLOAD_MAX_BATCH_BYTES", 2 * 1024 * 1024)))
        upload_max_batch_bytes = max(1024, min(2 * 1024 * 1024, raw_max_bytes))
    except ValueError:
        upload_max_batch_bytes = 2 * 1024 * 1024

    try:
        upload_max_retries = max(0, int(env_vars.get("TELEMETRY_UPLOAD_MAX_RETRIES", env_vars.get("UPLOAD_MAX_RETRIES", 3))))
    except ValueError:
        upload_max_retries = 3

    try:
        upload_backoff = max(1.0, float(env_vars.get("TELEMETRY_UPLOAD_BACKOFF_FACTOR", env_vars.get("UPLOAD_BACKOFF_FACTOR", 1.5))))
    except ValueError:
        upload_backoff = 1.5

    allow_insecure_raw = env_vars.get("TELEMETRY_ALLOW_INSECURE_HTTP", env_vars.get("ALLOW_INSECURE_HTTP", "false"))
    if isinstance(allow_insecure_raw, bool):
        allow_insecure = allow_insecure_raw
    else:
        allow_insecure = str(allow_insecure_raw).strip().lower() in ("true", "1", "yes", "t")

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
        telemetry_server_url=server_url,
        upload_enabled=upload_enabled,
        upload_interval_seconds=upload_interval,
        upload_timeout_seconds=upload_timeout,
        upload_max_batch_size=upload_max_batch_size,
        upload_max_batch_bytes=upload_max_batch_bytes,
        upload_max_retries=upload_max_retries,
        upload_backoff_factor=upload_backoff,
        allow_insecure_http=allow_insecure,
    )
