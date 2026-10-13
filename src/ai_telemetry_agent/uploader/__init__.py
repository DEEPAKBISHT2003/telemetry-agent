"""Telemetry Uploader package for AI Telemetry Agent.

Provides:
- Secure device credential storage with DPAPI and restrictive file permissions.
- Atomic, crash-safe JSONL checkpointing.
- HTTPS backend client with privacy scrubbing and strict certificate validation.
- TelemetryUploader and UploaderDaemon for automatic background telemetry sync.
"""

from ai_telemetry_agent.uploader.checkpoint import CheckpointManager
from ai_telemetry_agent.uploader.client import (
    AuthenticationError,
    BackendClient,
    ConflictError,
    InsecureURLError,
    NetworkError,
    RateLimitError,
    ServerError,
    UploaderClientError,
    scrub_client_event,
)
from ai_telemetry_agent.uploader.credentials import CredentialStore, EnrollmentMetadata
from ai_telemetry_agent.uploader.uploader import (
    TelemetryUploader,
    UploaderDaemon,
    UploaderFileLock,
    UploaderLockError,
    UploadCycleResult,
)

__all__ = [
    "CheckpointManager",
    "CredentialStore",
    "EnrollmentMetadata",
    "BackendClient",
    "scrub_client_event",
    "UploaderClientError",
    "InsecureURLError",
    "AuthenticationError",
    "ConflictError",
    "RateLimitError",
    "ServerError",
    "NetworkError",
    "TelemetryUploader",
    "UploaderDaemon",
    "UploaderFileLock",
    "UploaderLockError",
    "UploadCycleResult",
]
