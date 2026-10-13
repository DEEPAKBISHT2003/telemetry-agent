"""Secure HTTP/HTTPS client for communicating with centralized telemetry backend.

Integrates with:
- POST /api/v1/enrollment/claim
- GET /api/v1/devices/me
- POST /api/v1/telemetry/events/batch

Enforces:
- Strict HTTPS by default with standard CA certificate validation.
- Privacy pre-flight verification: drops local filesystem roots and sensitive keys.
- Robust status code classification (Auth, RateLimit, Conflict, ServerError).
- Configurable timeout and exponential backoff retry logic.
"""

from datetime import datetime, timezone
import json
import logging
import random
import re
import ssl
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ai_telemetry_agent.logging.structured import get_logger

logger = get_logger("telemetry.uploader.client")


class UploaderClientError(Exception):
    """Base exception for telemetry uploader client failures."""
    pass


class InsecureURLError(UploaderClientError):
    """Raised when an unencrypted HTTP URL is used without explicit exemption."""
    pass


class AuthenticationError(UploaderClientError):
    """Raised on HTTP 401 or 403 response indicating invalid or revoked credentials."""
    pass


class ConflictError(UploaderClientError):
    """Raised on HTTP 409 response indicating batch payload or event mutation conflict."""
    pass


class RateLimitError(UploaderClientError):
    """Raised on HTTP 429 response indicating backend throttling."""
    pass


class ServerError(UploaderClientError):
    """Raised on HTTP 5xx responses indicating backend server errors."""
    pass


class NetworkError(UploaderClientError):
    """Raised on socket timeout, connection refused, or DNS failures."""
    pass


# Privacy sensitive keys to scrub from event payloads before dispatch
FORBIDDEN_PAYLOAD_KEYS = {
    "prompt", "completion", "transcript", "arguments", "raw_args",
    "filepath", "local_path", "source_code", "diff", "code", "secret",
    "password", "api_key", "bearer_token", "token",
}


def scrub_client_event(event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize and strip non-whitelisted/sensitive client data before server upload."""
    clean = dict(event_dict)

    # 1. Sanitize context: omit local repository root filesystem path
    if "context" in clean and isinstance(clean["context"], dict):
        ctx = dict(clean["context"])
        if "repository" in ctx and isinstance(ctx["repository"], dict):
            repo = dict(ctx["repository"])
            repo.pop("root", None)  # Strictly strip local machine path
            ctx["repository"] = repo
        clean["context"] = ctx

    # 2. Scrub payload forbidden keys
    if "payload" in clean and isinstance(clean["payload"], dict):
        payload = dict(clean["payload"])
        for k in list(payload.keys()):
            if k.lower() in FORBIDDEN_PAYLOAD_KEYS:
                payload.pop(k, None)
        clean["payload"] = payload

    return clean


class BackendClient:
    """Client for authenticated interactions with the centralized telemetry platform."""

    def __init__(
        self,
        server_url: str,
        timeout_seconds: float = 15.0,
        allow_insecure_http: bool = False,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
    ):
        clean_url = server_url.strip().rstrip("/")
        parsed = urllib.parse.urlparse(clean_url)

        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid telemetry server URL: '{server_url}'")

        is_localhost = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        if parsed.scheme.lower() == "http":
            if not allow_insecure_http and not is_localhost:
                raise InsecureURLError(
                    f"Insecure HTTP URL '{clean_url}' rejected. Centralized telemetry requires HTTPS."
                )

        self.server_url = clean_url
        self.timeout = max(1.0, float(timeout_seconds))
        self.allow_insecure_http = allow_insecure_http
        self.max_retries = max(1, int(max_retries))
        self.backoff_factor = max(1.0, float(backoff_factor))
        self.ssl_context = ssl.create_default_context()

    def _execute_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """Send an HTTP request with exponential backoff on retryable server errors."""
        url = f"{self.server_url}{path}"
        req_headers = {
            "Accept": "application/json",
            "User-Agent": "ai-telemetry-agent/0.1.2",
        }
        if headers:
            req_headers.update(headers)

        data_bytes = None
        if payload is not None:
            req_headers["Content-Type"] = "application/json"
            data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)

            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                    status_code = resp.getcode()
                    resp_bytes = resp.read()
                    data = {}
                    if resp_bytes:
                        try:
                            data = json.loads(resp_bytes.decode("utf-8"))
                        except Exception:
                            pass
                    return status_code, data

            except urllib.error.HTTPError as http_err:
                status_code = http_err.code
                error_body = ""
                try:
                    error_body = http_err.read().decode("utf-8")
                    err_json = json.loads(error_body)
                    error_detail = err_json.get("detail", error_body)
                except Exception:
                    error_detail = error_body

                if status_code in (401, 403):
                    raise AuthenticationError(f"HTTP {status_code}: {error_detail}") from http_err
                elif status_code == 409:
                    raise ConflictError(f"HTTP 409 Conflict: {error_detail}") from http_err
                elif status_code == 429:
                    if attempt < self.max_retries:
                        sleep_time = (self.backoff_factor ** attempt) + random.uniform(0.1, 0.5)
                        logger.warning(f"Rate limited (429). Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                    raise RateLimitError(f"HTTP 429 Rate limited: {error_detail}") from http_err
                elif status_code >= 500:
                    if attempt < self.max_retries:
                        sleep_time = (self.backoff_factor ** attempt) + random.uniform(0.1, 0.5)
                        logger.warning(f"Server error ({status_code}). Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                    raise ServerError(f"HTTP {status_code} Server Error: {error_detail}") from http_err
                else:
                    raise UploaderClientError(f"HTTP {status_code} Error: {error_detail}") from http_err

            except (urllib.error.URLError, TimeoutError, OSError) as net_err:
                if attempt < self.max_retries:
                    sleep_time = (self.backoff_factor ** attempt) + random.uniform(0.1, 0.5)
                    logger.warning(f"Network error ({net_err}). Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue
                raise NetworkError(f"Connection to '{url}' failed: {net_err}") from net_err

    def claim_enrollment(
        self,
        enrollment_token: str,
        hardware_uuid: str,
        hostname: str,
        platform_name: str,
        agent_version: str = "0.1.2",
    ) -> Dict[str, Any]:
        """Claim a single-use enrollment token and register this device workstation."""
        payload = {
            "enrollment_token": enrollment_token.strip(),
            "hardware_uuid": hardware_uuid.strip(),
            "hostname": hostname.strip(),
            "platform": platform_name.strip(),
            "agent_version": agent_version.strip(),
        }
        status_code, data = self._execute_request(
            method="POST",
            path="/api/v1/enrollment/claim",
            payload=payload,
        )
        if status_code != 201:
            raise UploaderClientError(f"Enrollment claim failed with unexpected status: {status_code}")
        return data

    def verify_device_identity(self, credential_token: str) -> Dict[str, Any]:
        """Verify device registration and credentials against GET /api/v1/devices/me."""
        headers = {"Authorization": f"Bearer {credential_token.strip()}"}
        status_code, data = self._execute_request(
            method="GET",
            path="/api/v1/devices/me",
            headers=headers,
        )
        if status_code != 200:
            raise UploaderClientError(f"Device verification failed with status: {status_code}")
        return data

    def upload_batch(
        self,
        credential_token: str,
        events: List[Dict[str, Any]],
        batch_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        """Upload a batch of sanitized telemetry events to POST /api/v1/telemetry/events/batch."""
        if not events:
            return {"status": "EMPTY", "submitted_count": 0, "newly_persisted_count": 0, "duplicate_count": 0}

        if len(events) > 500:
            raise ValueError(f"Batch size {len(events)} exceeds maximum allowed limit of 500 events.")

        # Scrub privacy allowlist
        scrubbed_events = [scrub_client_event(e) for e in events]
        b_id = str(batch_id or uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        batch_payload = {
            "batch_id": b_id,
            "client_sent_at": now_iso,
            "events": scrubbed_events,
        }

        headers = {"Authorization": f"Bearer {credential_token.strip()}"}
        status_code, data = self._execute_request(
            method="POST",
            path="/api/v1/telemetry/events/batch",
            payload=batch_payload,
            headers=headers,
        )

        if status_code not in (200, 201):
            raise UploaderClientError(f"Unexpected upload response status: {status_code}")

        return data
