"""Tests for privacy enforcement and secret redaction."""

from src.config.settings import Settings
from src.core.identity import LocalIdentityProvider
from src.core.processor import EventProcessor
from src.logging.structured import REDACTED_STR, sanitize_data


def test_sanitize_dictionary_keys_and_values():
    raw_payload = {
        "user_name": "developer",
        "api_key": "sk-12345678901234567890abcdef",
        "password": "SuperSecretPassword123!",
        "auth_token": "bearer xyz9876543210",
        "nested": {
            "db_secret": "my-secret-pw",
            "safe_metric": 42,
            "message": "Token is sk-abcdefabcdefabcdefabcdef1234",
        },
    }

    clean = sanitize_data(raw_payload)

    assert clean["user_name"] == "developer"
    assert clean["api_key"] == REDACTED_STR
    assert clean["password"] == REDACTED_STR
    assert clean["auth_token"] == REDACTED_STR
    assert clean["nested"]["db_secret"] == REDACTED_STR
    assert clean["nested"]["safe_metric"] == 42
    assert "sk-abcdef" not in clean["nested"]["message"]
    assert REDACTED_STR in clean["nested"]["message"]


def test_processor_sanitizes_incoming_payloads():
    settings = Settings(member_id="M001", device_id="DEV-001")
    processor = EventProcessor(LocalIdentityProvider(settings))

    raw_event = {
        "event_type": "custom_event",
        "payload": {
            "repository": "ai-project",
            "api_key": "secret-key-value",
            "openai_key": "sk-12345678901234567890abcdef",
        }
    }

    event = processor.process_raw(raw_event)
    assert event is not None
    assert event.payload["repository"] == "ai-project"
    assert event.payload["api_key"] == REDACTED_STR
    assert event.payload["openai_key"] == REDACTED_STR
