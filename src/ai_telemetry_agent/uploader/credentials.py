"""Secure Device Credential Storage for AI Telemetry Agent.

Supports:
- Windows DPAPI (Data Protection API) hardware/user-bound encryption on Windows via ctypes.
- Restrictive file permissions (0o600 / user-only) on Linux, macOS, and fallback environments.
- Strict hygiene: Plaintext credentials are never written to standard logs.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any, Dict, Optional

from ai_telemetry_agent.config.settings import get_user_agent_home

# Windows DPAPI setup
_USE_DPAPI = False
if platform.system() == "Windows":
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte)),
            ]

        _USE_DPAPI = True
    except Exception:
        _USE_DPAPI = False


def _encrypt_dpapi(plaintext_bytes: bytes) -> bytes:
    """Encrypt byte payload using Windows Data Protection API (DPAPI)."""
    if not _USE_DPAPI:
        raise RuntimeError("DPAPI is not available on this platform")

    import ctypes
    from ctypes import wintypes

    data_in = DATA_BLOB(
        len(plaintext_bytes),
        ctypes.cast(ctypes.create_string_buffer(plaintext_bytes, len(plaintext_bytes)), ctypes.POINTER(ctypes.c_byte)),
    )
    data_out = DATA_BLOB()

    # CryptProtectData with current user scope
    success = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data_in),
        None,  # description
        None,  # optional entropy
        None,  # reserved
        None,  # prompt struct
        0,     # flags
        ctypes.byref(data_out),
    )
    if not success:
        raise RuntimeError("Windows CryptProtectData call failed")

    encrypted_bytes = ctypes.string_at(data_out.pbData, data_out.cbData)
    ctypes.windll.kernel32.LocalFree(data_out.pbData)
    return encrypted_bytes


def _decrypt_dpapi(cipher_bytes: bytes) -> bytes:
    """Decrypt byte payload using Windows Data Protection API (DPAPI)."""
    if not _USE_DPAPI:
        raise RuntimeError("DPAPI is not available on this platform")

    import ctypes
    from ctypes import wintypes

    data_in = DATA_BLOB(
        len(cipher_bytes),
        ctypes.cast(ctypes.create_string_buffer(cipher_bytes, len(cipher_bytes)), ctypes.POINTER(ctypes.c_byte)),
    )
    data_out = DATA_BLOB()

    success = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(data_out),
    )
    if not success:
        raise RuntimeError("Windows CryptUnprotectData call failed")

    decrypted_bytes = ctypes.string_at(data_out.pbData, data_out.cbData)
    ctypes.windll.kernel32.LocalFree(data_out.pbData)
    return decrypted_bytes


@dataclass
class EnrollmentMetadata:
    """Non-secret device enrollment attribution metadata."""

    server_url: str
    organization_id: str
    developer_id: str
    device_id: str
    token_prefix: str
    enrolled_at: str
    expires_at: Optional[str] = None
    hardware_uuid: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnrollmentMetadata":
        return cls(
            server_url=data.get("server_url", ""),
            organization_id=data.get("organization_id", ""),
            developer_id=data.get("developer_id", ""),
            device_id=data.get("device_id", ""),
            token_prefix=data.get("token_prefix", ""),
            enrolled_at=data.get("enrolled_at", ""),
            expires_at=data.get("expires_at"),
            hardware_uuid=data.get("hardware_uuid"),
        )


class CredentialStore:
    """Manages persistent device credentials and enrollment metadata."""

    def __init__(self, custom_home: Optional[Path] = None):
        self.home = (custom_home or get_user_agent_home()).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.cred_dpapi_file = self.home / "device_credential.dat"
        self.cred_fallback_file = self.home / "device_credential.key"
        self.enrollment_file = self.home / "enrollment.json"

    def save_credential(self, token: str, metadata: Optional[EnrollmentMetadata] = None) -> None:
        """Securely persist device Bearer credential token ('dvt_...')."""
        clean_token = token.strip()
        if not clean_token.startswith("dvt_"):
            raise ValueError("Device credential must begin with 'dvt_' prefix")

        raw_bytes = clean_token.encode("utf-8")

        # 1. Try Windows DPAPI first if available
        if _USE_DPAPI:
            try:
                encrypted = _encrypt_dpapi(raw_bytes)
                temp_file = self.home / f"device_credential.dat.{os.getpid()}.tmp"
                temp_file.write_bytes(encrypted)
                temp_file.replace(self.cred_dpapi_file)
                # Remove unencrypted fallback if exists
                if self.cred_fallback_file.exists():
                    self.cred_fallback_file.unlink()
            except Exception:
                self._save_fallback(raw_bytes)
        else:
            self._save_fallback(raw_bytes)

        # 2. Save non-secret enrollment metadata if provided
        if metadata:
            self.save_metadata(metadata)

    def _save_fallback(self, raw_bytes: bytes) -> None:
        """Fallback for non-Windows or if DPAPI fails: secure file permissions."""
        temp_file = self.home / f"device_credential.key.{os.getpid()}.tmp"
        temp_file.write_bytes(raw_bytes)
        try:
            temp_file.chmod(0o600)
        except Exception:
            pass
        temp_file.replace(self.cred_fallback_file)
        try:
            self.cred_fallback_file.chmod(0o600)
        except Exception:
            pass

    def load_credential(self) -> Optional[str]:
        """Load and decrypt the persisted device credential token."""
        # Check DPAPI file first
        if _USE_DPAPI and self.cred_dpapi_file.is_file():
            try:
                cipher_bytes = self.cred_dpapi_file.read_bytes()
                decrypted = _decrypt_dpapi(cipher_bytes).decode("utf-8").strip()
                if decrypted.startswith("dvt_"):
                    return decrypted
            except Exception:
                pass

        # Check fallback file
        if self.cred_fallback_file.is_file():
            try:
                token = self.cred_fallback_file.read_text(encoding="utf-8").strip()
                if token.startswith("dvt_"):
                    return token
            except Exception:
                pass

        return None

    def has_credential(self) -> bool:
        """Check if an active device credential exists on disk."""
        return self.load_credential() is not None

    def save_metadata(self, metadata: EnrollmentMetadata) -> None:
        """Persist enrollment metadata to enrollment.json."""
        meta_dict = metadata.to_dict()
        temp_file = self.home / f"enrollment.json.{os.getpid()}.tmp"
        temp_file.write_text(json.dumps(meta_dict, indent=2), encoding="utf-8")
        temp_file.replace(self.enrollment_file)

    def load_metadata(self) -> Optional[EnrollmentMetadata]:
        """Retrieve stored enrollment metadata."""
        if not self.enrollment_file.is_file():
            return None
        try:
            data = json.loads(self.enrollment_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return EnrollmentMetadata.from_dict(data)
        except Exception:
            pass
        return None

    def clear(self) -> None:
        """Purge stored credentials and enrollment metadata."""
        for f in [self.cred_dpapi_file, self.cred_fallback_file, self.enrollment_file]:
            if f.is_file():
                try:
                    f.unlink()
                except Exception:
                    pass
