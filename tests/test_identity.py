"""Tests for identity provider configuration and validation."""

import os
import pytest
from src.config.settings import ConfigurationError, Settings, load_settings
from src.core.identity import LocalIdentityProvider


def test_valid_identity():
    settings = Settings(member_id="M001", device_id="DEV-001")
    provider = LocalIdentityProvider(settings)
    assert provider.get_current_member() == "M001"
    assert provider.get_device_id() == "DEV-001"


def test_missing_member_id_in_settings():
    settings = Settings(member_id="", device_id="DEV-001")
    with pytest.raises(ConfigurationError) as exc_info:
        LocalIdentityProvider(settings)
    assert "MEMBER_ID is not configured" in str(exc_info.value)


def test_missing_device_id_in_settings():
    settings = Settings(member_id="M001", device_id="  ")
    with pytest.raises(ConfigurationError) as exc_info:
        LocalIdentityProvider(settings)
    assert "DEVICE_ID is not configured" in str(exc_info.value)


def test_load_settings_missing_member(monkeypatch):
    monkeypatch.delenv("MEMBER_ID", raising=False)
    monkeypatch.delenv("DEVICE_ID", raising=False)
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file="nonexistent.env")
    assert "MEMBER_ID is required" in str(exc_info.value)
    assert "DEVICE_ID is required" in str(exc_info.value)


def test_load_settings_override():
    settings = load_settings(member_id="USER-99", device_id="LAPTOP-7")
    assert settings.member_id == "USER-99"
    assert settings.device_id == "LAPTOP-7"
