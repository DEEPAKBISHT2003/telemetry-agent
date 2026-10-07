"""Configuration settings and environment variable parser."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ConfigurationError(ValueError):
    """Raised when configuration values are missing or invalid."""
    pass


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
    except Exception as e:
        # Ignore dotenv read errors gracefully
        pass
    return env_vars


@dataclass(frozen=True)
class Settings:
    member_id: str
    device_id: str
    collector_version: str = "0.1.0"
    log_level: str = "INFO"
    heartbeat_interval_seconds: int = 30
    local_database_path: str = "./data/telemetry.db"
    git_monitor_interval_seconds: int = 10
    git_monitor_paths: List[str] = field(default_factory=lambda: ["."])
    antigravity_monitor_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return Path(self.local_database_path).resolve()


def load_settings(
    env_file: Optional[str] = None,
    member_id: Optional[str] = None,
    device_id: Optional[str] = None,
    overrides: Optional[dict] = None
) -> Settings:
    """Load settings from environment variables, .env file, or direct parameters.

    Raises:
        ConfigurationError: If MEMBER_ID or DEVICE_ID are missing or empty.
    """
    env_vars = {}

    # 1. Load from custom or default .env files
    search_paths = []
    if env_file:
        search_paths.append(Path(env_file))
    else:
        search_paths.extend([
            Path(".env"),
            Path(__file__).resolve().parent.parent.parent / ".env"
        ])

    for path in search_paths:
        if path.is_file():
            env_vars.update(parse_dotenv(path))
            break

    # 2. Layer with actual OS environment variables
    for key in [
        "MEMBER_ID", "DEVICE_ID", "COLLECTOR_VERSION", "LOG_LEVEL",
        "HEARTBEAT_INTERVAL_SECONDS", "LOCAL_DATABASE_PATH",
        "GIT_MONITOR_INTERVAL_SECONDS", "GIT_MONITOR_PATHS",
        "ANTIGRAVITY_MONITOR_ENABLED"
    ]:
        val = os.environ.get(key)
        if val is not None:
            env_vars[key] = val

    # 3. Layer with explicit overrides
    if overrides:
        env_vars.update(overrides)

    if member_id:
        env_vars["MEMBER_ID"] = member_id
    if device_id:
        env_vars["DEVICE_ID"] = device_id

    final_member_id = (env_vars.get("MEMBER_ID") or "").strip()
    final_device_id = (env_vars.get("DEVICE_ID") or "").strip()

    errors = []
    if not final_member_id:
        errors.append("MEMBER_ID is required (e.g. MEMBER_ID=M001). Configure it in .env or environment variables.")
    if not final_device_id:
        errors.append("DEVICE_ID is required (e.g. DEVICE_ID=DEV-001). Configure it in .env or environment variables.")

    if errors:
        raise ConfigurationError("Configuration Error:\n" + "\n".join(f"- {err}" for err in errors))

    version = env_vars.get("COLLECTOR_VERSION", "0.1.0").strip()
    log_level = env_vars.get("LOG_LEVEL", "INFO").strip().upper()

    try:
        heartbeat_sec = int(env_vars.get("HEARTBEAT_INTERVAL_SECONDS", 30))
    except ValueError:
        heartbeat_sec = 30

    db_path = env_vars.get("LOCAL_DATABASE_PATH", "./data/telemetry.db").strip()

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

    return Settings(
        member_id=final_member_id,
        device_id=final_device_id,
        collector_version=version,
        log_level=log_level,
        heartbeat_interval_seconds=heartbeat_sec,
        local_database_path=db_path,
        git_monitor_interval_seconds=git_interval,
        git_monitor_paths=git_paths,
        antigravity_monitor_enabled=ag_enabled,
    )
