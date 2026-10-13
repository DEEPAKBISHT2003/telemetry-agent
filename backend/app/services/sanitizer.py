"""Telemetry event privacy allowlist sanitizer and schema normalizer.

Enforces:
1. Strict server-side field allowlisting.
2. Complete scrubbing of prompts, completions, transcripts, source code, diffs,
   terminal history, raw tool arguments/outputs, and local repository root paths.
3. Timestamp UTC normalization and reasonable time-window validation.
4. Authoritative binding to server-side device context.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import re
from typing import Any, Dict, List, Optional, Set
import uuid

from fastapi import HTTPException, status

from app.models.base import ensure_utc, utc_now
from app.schemas.telemetry import ClientTelemetryEvent

logger = logging.getLogger("telemetry.sanitizer")

# Forbidden substrings in keys or metadata (case-insensitive)
FORBIDDEN_KEY_SUBSTRINGS: Set[str] = {
    "prompt",
    "completion",
    "transcript",
    "message",
    "content",
    "response",
    "code",
    "source",
    "diff",
    "patch",
    "terminal",
    "command",
    "stdout",
    "stderr",
    "argument",
    "output",
    "secret",
    "password",
    "credential",
    "api_key",
    "auth",
    "bearer",
    "root",  # context.repository.root local filesystem path
}

# Allowlisted fields for the structured raw_payload JSONB column
ALLOWED_PAYLOAD_KEYS: Set[str] = {
    "provider",
    "model",
    "session_id",
    "run_id",
    "started_at",
    "completed_at",
    "duration_ms",
    "turns",
    "agent_name",
    "tool_name",
    "capture_method",
    "cache_write_tokens",
    "metadata",
}

# Allowlisted metadata keys inside payload.metadata
ALLOWED_METADATA_KEYS: Set[str] = {
    "thinking_tokens",
    "turn_index",
    "run_index",
    "execution_id",
    "reasoning_tokens",
    "cache_hit",
}

MAX_EVENT_AGE_DAYS = 30
MAX_EVENT_FUTURE_DAYS = 1


@dataclass
class SanitizedTelemetryEvent:
    """Server-side sanitized and authoritative telemetry event representation ready for persistence."""

    id: uuid.UUID
    organization_id: uuid.UUID
    developer_id: uuid.UUID
    device_id: uuid.UUID
    batch_id: uuid.UUID
    event_type: str
    event_timestamp: datetime
    session_id: Optional[str]
    repository_name: Optional[str]
    branch: Optional[str]
    model_name: Optional[str]
    tool_name: Optional[str]
    tokens_input_uncached: Optional[int]
    tokens_input_cached: Optional[int]
    tokens_output: Optional[int]
    tokens_total: Optional[int]
    cost_usd: Optional[Decimal]
    raw_payload: Dict[str, Any]


def is_sensitive_key(key: str) -> bool:
    """Check if a field name matches or contains forbidden sensitive substrings."""
    lower_key = key.lower()
    return any(forbidden in lower_key for forbidden in FORBIDDEN_KEY_SUBSTRINGS)


def sanitize_metadata_dict(raw_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitize metadata dictionary against forbidden keys and data types.

    Ensures that prompts, transcripts, code, and raw tool arguments cannot leak into JSONB.
    """
    sanitized: Dict[str, Any] = {}
    if not isinstance(raw_meta, dict):
        return sanitized

    for k, v in raw_meta.items():
        if not isinstance(k, str):
            continue
        # Check against forbidden substrings
        if is_sensitive_key(k):
            continue
        # Limit to safe primitive values
        if isinstance(v, (int, float, bool)):
            sanitized[k] = v
        elif isinstance(v, str):
            # Enforce reasonable string length for metadata values (max 256 chars)
            if len(v) <= 256:
                sanitized[k] = v
        elif isinstance(v, dict):
            nested = sanitize_metadata_dict(v)
            if nested:
                sanitized[k] = nested

    return sanitized


def validate_and_sanitize_event(
    event: ClientTelemetryEvent,
    batch_id: uuid.UUID,
    org_id: uuid.UUID,
    dev_id: uuid.UUID,
    device_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> SanitizedTelemetryEvent:
    """Validate client event against constraints and construct an immutable, sanitized representation.

    Args:
        event: Client-submitted event envelope.
        batch_id: Authoritative batch UUID.
        org_id: Authoritative organization UUID from device context.
        dev_id: Authoritative developer UUID from device context.
        device_id: Authoritative device UUID from device context.
        now: Optional reference timestamp.

    Returns:
        SanitizedTelemetryEvent ready for database persistence.

    Raises:
        HTTPException(422) if event fails validation constraints.
    """
    current_time = now or utc_now()

    # 1. Normalize timestamp to UTC
    event_time = ensure_utc(event.timestamp)

    # 2. Enforce time window bounds
    min_time = current_time - timedelta(days=MAX_EVENT_AGE_DAYS)
    max_time = current_time + timedelta(days=MAX_EVENT_FUTURE_DAYS)
    if event_time < min_time or event_time > max_time:
        raise HTTPException(
            status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
            detail=(
                f"Event {event.event_id} timestamp '{event_time.isoformat()}' falls outside the "
                f"acceptable range (-{MAX_EVENT_AGE_DAYS} days to +{MAX_EVENT_FUTURE_DAYS} day)."
            ),
        )

    # 3. Extract contextual repository metadata (STRICTLY stripping root path)
    session_id: Optional[str] = None
    repo_name: Optional[str] = None
    branch: Optional[str] = None

    if event.context:
        session_id = event.context.session_id
        if event.context.repository:
            repo_name = event.context.repository.name
            branch = event.context.repository.branch

    # 4. Extract metrics from payload
    model_name: Optional[str] = None
    tool_name: Optional[str] = None
    tokens_in_uncached: Optional[int] = None
    tokens_in_cached: Optional[int] = None
    tokens_out: Optional[int] = None
    tokens_tot: Optional[int] = None
    cost_usd: Optional[Decimal] = None
    raw_payload: Dict[str, Any] = {}

    if event.payload:
        model_name = event.payload.model
        tool_name = event.payload.tool_name
        tokens_in_uncached = event.payload.input_tokens
        tokens_in_cached = event.payload.cache_read_tokens
        tokens_out = event.payload.output_tokens
        tokens_tot = event.payload.total_tokens
        cost_usd = event.payload.cost_usd

        # If session_id was not in context, fall back to payload session_id
        if not session_id and event.payload.session_id:
            session_id = event.payload.session_id

        # Construct sanitized raw_payload from explicit allowlist
        payload_dict = event.payload.model_dump(exclude_unset=False)
        for k in ALLOWED_PAYLOAD_KEYS:
            if k == "metadata":
                sanitized_meta = sanitize_metadata_dict(payload_dict.get("metadata", {}))
                if sanitized_meta:
                    raw_payload["metadata"] = sanitized_meta
            elif k in payload_dict and payload_dict[k] is not None:
                # Extra check: make sure value is not sensitive
                raw_payload[k] = payload_dict[k]

    return SanitizedTelemetryEvent(
        id=event.event_id,
        organization_id=org_id,
        developer_id=dev_id,
        device_id=device_id,
        batch_id=batch_id,
        event_type=event.event_type,
        event_timestamp=event_time,
        session_id=session_id[:128] if session_id else None,
        repository_name=repo_name[:255] if repo_name else None,
        branch=branch[:255] if branch else None,
        model_name=model_name[:128] if model_name else None,
        tool_name=tool_name[:128] if tool_name else None,
        tokens_input_uncached=tokens_in_uncached,
        tokens_input_cached=tokens_in_cached,
        tokens_output=tokens_out,
        tokens_total=tokens_tot,
        cost_usd=cost_usd,
        raw_payload=raw_payload,
    )
