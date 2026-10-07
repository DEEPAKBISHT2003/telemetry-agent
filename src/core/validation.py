"""Event validation rules and schema verification."""

import datetime
import re
from typing import Any, Dict, List, Tuple
from src.core.event import EventType, TelemetryEvent
from src.logging.structured import sanitize_data


class ValidationError(ValueError):
    """Raised when a telemetry event fails schema or integrity validation."""
    pass


# Strict ISO-8601 timestamp parser check
def is_valid_iso8601(ts: str) -> bool:
    if not isinstance(ts, str) or not ts.strip():
        return False
    # Standard formats: 2026-10-05T16:30:01Z or 2026-10-05T16:30:01+00:00 or with microseconds
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        datetime.datetime.fromisoformat(ts)
        return True
    except Exception:
        return False


def validate_event(event: TelemetryEvent) -> Tuple[bool, List[str]]:
    """Validate a TelemetryEvent against the standardized envelope specification.

    Returns:
        (is_valid, list_of_errors)
    """
    errors: List[str] = []

    if not event.event_id or not str(event.event_id).strip():
        errors.append("event_id must be a non-empty string.")

    if not event.event_type or not str(event.event_type).strip():
        errors.append("event_type must be a non-empty string.")

    if not is_valid_iso8601(event.timestamp):
        errors.append(f"timestamp '{event.timestamp}' is not a valid ISO-8601 timestamp.")

    if not event.collector or not event.collector.device_id or not str(event.collector.device_id).strip():
        errors.append("collector.device_id is missing or empty.")

    if not event.collector or not event.collector.version or not str(event.collector.version).strip():
        errors.append("collector.version is missing or empty.")

    if not event.identity or not event.identity.member_id or not str(event.identity.member_id).strip():
        errors.append("identity.member_id is missing or empty.")

    if not isinstance(event.payload, dict):
        errors.append("payload must be a dictionary.")

    return (len(errors) == 0, errors)


def validate_raw_event_dict(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a raw dictionary before converting to TelemetryEvent."""
    errors: List[str] = []

    if not isinstance(data, dict):
        return (False, ["Event data must be a dictionary."])

    event_id = data.get("event_id")
    if event_id is not None and (not isinstance(event_id, str) or not event_id.strip()):
        errors.append("event_id, if provided, must be a non-empty string.")

    event_type = data.get("event_type")
    if not event_type or not isinstance(event_type, str) or not event_type.strip():
        errors.append("event_type is required and must be a non-empty string.")

    timestamp = data.get("timestamp")
    if timestamp is not None and not is_valid_iso8601(timestamp):
        errors.append(f"timestamp '{timestamp}' is not a valid ISO-8601 timestamp.")

    return (len(errors) == 0, errors)
