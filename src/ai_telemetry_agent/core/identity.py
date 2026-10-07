"""Developer identity management and enrollment module for AI Telemetry Agent.

Provides local developer enrollment, identity persistence, and identity resolution.
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
from typing import Any, Dict, Optional

from ai_telemetry_agent.config.settings import (
    ConfigurationError,
    Settings,
    get_or_create_device_id,
    get_user_agent_home,
)


@dataclass(frozen=True)
class DeveloperIdentity:
    """Represents the persistent identity of a human developer on a local machine."""
    member_id: str
    member_name: str
    device_id: str
    hostname: str
    windows_username: Optional[str] = None
    os_username: Optional[str] = None
    enrolled_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "member_id": self.member_id,
            "member_name": self.member_name,
            "device_id": self.device_id,
            "hostname": self.hostname,
        }
        win_user = self.windows_username or self.os_username
        if win_user:
            d["windows_username"] = win_user
        if self.enrolled_at:
            d["enrolled_at"] = self.enrolled_at
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeveloperIdentity":
        win_user = data.get("windows_username") or data.get("os_username")
        return cls(
            member_id=str(data.get("member_id", "")).strip(),
            member_name=str(data.get("member_name", "")).strip(),
            device_id=str(data.get("device_id", "")).strip(),
            hostname=str(data.get("hostname", "")).strip(),
            windows_username=win_user,
            os_username=win_user,
            enrolled_at=data.get("enrolled_at"),
        )


def get_identity_file_path(custom_home: Optional[Path] = None) -> Path:
    """Return path to identity.json within user agent home (~/.telemetry_agent/identity.json)."""
    home = custom_home if custom_home is not None else get_user_agent_home()
    return (home / "identity.json").resolve()


def detect_hostname() -> str:
    """Detect local machine hostname without sensitive device data."""
    try:
        node = platform.node()
        if node and node.strip():
            return node.strip()
    except Exception:
        pass
    try:
        host = socket.gethostname()
        if host and host.strip():
            return host.strip()
    except Exception:
        pass
    return "localhost"


def detect_os_username() -> Optional[str]:
    """Detect current OS username for local metadata attribution."""
    os_user = os.environ.get("USERNAME") or os.environ.get("USER")
    return os_user.strip() if os_user and os_user.strip() else None


def generate_local_member_id(member_name: str, device_id: str) -> str:
    """Generate a deterministic local member ID based on normalized name and device ID.

    Note: This is a temporary local identity mechanism. In future phases,
    centralized enrollment/SSO backend will assign employee IDs.
    """
    clean_name = member_name.strip().lower()
    raw = f"{clean_name}:{device_id.strip()}".encode("utf-8")
    hash_hex = hashlib.sha256(raw).hexdigest()[:10]
    return f"mem-{hash_hex}"


def is_enrolled(custom_home: Optional[Path] = None) -> bool:
    """Return True if developer identity is already configured and valid."""
    ident = get_identity(custom_home=custom_home)
    return ident is not None and bool(ident.member_name) and bool(ident.member_id)


def get_identity(custom_home: Optional[Path] = None) -> Optional[DeveloperIdentity]:
    """Load and return existing DeveloperIdentity from identity.json, or None."""
    path = get_identity_file_path(custom_home)
    if not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict) and data.get("member_name") and data.get("member_id"):
            return DeveloperIdentity.from_dict(data)
    except Exception:
        pass
    return None


def enroll(
    member_name: str,
    member_id: Optional[str] = None,
    device_id: Optional[str] = None,
    hostname: Optional[str] = None,
    custom_home: Optional[Path] = None,
) -> DeveloperIdentity:
    """Enroll a developer with validated human name, persisting identity.json locally.

    Raises:
        ValueError: If member_name is empty or whitespace only.
    """
    if not member_name or not isinstance(member_name, str) or not member_name.strip():
        raise ValueError("Developer name cannot be empty.")

    clean_name = member_name.strip()
    home = custom_home if custom_home is not None else get_user_agent_home()
    home.mkdir(parents=True, exist_ok=True)

    resolved_device_id = device_id or get_or_create_device_id(custom_home=home)
    resolved_hostname = hostname or detect_hostname()
    resolved_os_user = detect_os_username()
    resolved_member_id = member_id or generate_local_member_id(clean_name, resolved_device_id)
    enrolled_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    identity = DeveloperIdentity(
        member_id=resolved_member_id,
        member_name=clean_name,
        device_id=resolved_device_id,
        hostname=resolved_hostname,
        os_username=resolved_os_user,
        enrolled_at=enrolled_at,
    )

    # Persist identity safely
    target_path = get_identity_file_path(custom_home=home)
    payload_str = json.dumps(identity.to_dict(), indent=2, ensure_ascii=False) + "\n"
    target_path.write_text(payload_str, encoding="utf-8")

    return identity


def clear_identity(custom_home: Optional[Path] = None) -> None:
    """Remove local identity.json file if present (for test environments / resets)."""
    target = get_identity_file_path(custom_home)
    if target.is_file():
        try:
            target.unlink()
        except Exception:
            pass


class IdentityProvider(ABC):
    """Abstract interface for developer identity resolution."""

    @abstractmethod
    def get_current_member(self) -> str:
        """Return the current human member ID (e.g. mem-1a2b3c4d5e)."""
        pass

    @abstractmethod
    def get_device_id(self) -> str:
        """Return the current device ID (e.g. dev-1234567890ab)."""
        pass

    def get_member_name(self) -> Optional[str]:
        """Return human-readable developer name if enrolled."""
        return None

    def get_hostname(self) -> str:
        """Return local hostname."""
        return detect_hostname()


class LocalIdentityProvider(IdentityProvider):
    """Local configuration and enrollment-based identity provider."""

    def __init__(self, settings: Settings, identity: Optional[DeveloperIdentity] = None):
        self._settings = settings
        self._identity = identity
        self._validate()

    def _validate(self) -> None:
        if not self._settings.member_id or not self._settings.member_id.strip():
            raise ConfigurationError("LocalIdentityProvider: MEMBER_ID is not configured.")
        if not self._settings.device_id or not self._settings.device_id.strip():
            raise ConfigurationError("LocalIdentityProvider: DEVICE_ID is not configured.")

    def get_current_member(self) -> str:
        return self._settings.member_id.strip()

    def get_device_id(self) -> str:
        return self._settings.device_id.strip()

    def get_member_name(self) -> Optional[str]:
        if self._settings.member_name:
            return self._settings.member_name.strip()
        if self._identity:
            return self._identity.member_name
        return None

    def get_hostname(self) -> str:
        if self._settings.hostname:
            return self._settings.hostname.strip()
        if self._identity:
            return self._identity.hostname
        return detect_hostname()
