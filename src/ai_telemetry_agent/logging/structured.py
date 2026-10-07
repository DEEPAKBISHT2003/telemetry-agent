"""Structured logger with built-in privacy protection and secret redaction."""

import datetime
import json
import logging
import re
import sys
from typing import Any, Dict, Optional

# Sensitive keys that should never be logged
SENSITIVE_KEY_PATTERNS = [
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"auth", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
]

# Sensitive value patterns (e.g. OpenAI/Anthropic/AWS/GitHub keys)
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9\._\-]+", re.IGNORECASE),
]

REDACTED_STR = "[REDACTED]"


def sanitize_data(data: Any) -> Any:
    """Recursively sanitize data structures to strip out secrets and PII."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            str_key = str(k)
            if any(pat.search(str_key) for pat in SENSITIVE_KEY_PATTERNS):
                sanitized[k] = REDACTED_STR
            else:
                sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        val = data
        for pat in SENSITIVE_VALUE_PATTERNS:
            val = pat.sub(REDACTED_STR, val)
        return val
    return data


class StructuredFormatter(logging.Formatter):
    """Formats log records into clean, structured key-value lines with ISO timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        level = record.levelname

        # Extract extra structured fields
        event_name = getattr(record, "event_name", record.getMessage())
        fields: Dict[str, Any] = getattr(record, "fields", {})

        # Sanitize any fields passed in
        clean_fields = sanitize_data(fields)
        field_strs = [f"{k}={v}" for k, v in clean_fields.items()]

        if field_strs:
            return f"{timestamp} {level} {event_name} " + " ".join(field_strs)
        else:
            return f"{timestamp} {level} {event_name}"


class LogEvent:
    """Helper to emit structured log records."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def info(self, event_name: str, **fields: Any) -> None:
        self.logger.info(event_name, extra={"event_name": event_name, "fields": fields})

    def warning(self, event_name: str, **fields: Any) -> None:
        self.logger.warning(event_name, extra={"event_name": event_name, "fields": fields})

    def error(self, event_name: str, **fields: Any) -> None:
        self.logger.error(event_name, extra={"event_name": event_name, "fields": fields})

    def debug(self, event_name: str, **fields: Any) -> None:
        self.logger.debug(event_name, extra={"event_name": event_name, "fields": fields})


def setup_structured_logging(level_name: str = "INFO") -> None:
    """Configure root logger with structured formatting."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger("telemetry")
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str = "telemetry") -> LogEvent:
    """Get a structured logger instance."""
    return LogEvent(logging.getLogger(name))
