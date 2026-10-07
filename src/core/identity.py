"""Identity provider for local developer telemetry."""

from abc import ABC, abstractmethod
from typing import Optional
from src.config.settings import Settings, ConfigurationError


class IdentityProvider(ABC):
    """Abstract interface for developer identity resolution."""

    @abstractmethod
    def get_current_member(self) -> str:
        """Return the current human member ID (e.g. M001)."""
        pass

    @abstractmethod
    def get_device_id(self) -> str:
        """Return the current device ID (e.g. DEV-001)."""
        pass


class LocalIdentityProvider(IdentityProvider):
    """Local configuration-based identity provider.

    In Phase 1, identity is supplied via local settings (.env / environment variables).
    In future phases, this provider will be replaced with an SSO/Auth identity provider.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
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
