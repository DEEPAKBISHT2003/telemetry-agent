"""Typed application configuration module for AI Telemetry Backend."""

from functools import lru_cache
import logging
import re
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


AppEnvironment = Literal["development", "testing", "staging", "production"]


class Settings(BaseSettings):
    """Centralized, typed application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core Application Settings
    app_name: str = Field(default="AI Telemetry Backend", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    app_env: AppEnvironment = Field(
        default="development",
        description="Deployment environment (development, testing, staging, production)",
    )
    debug: bool = Field(default=False, description="Debug mode flag")
    log_level: str = Field(default="INFO", description="Logging verbosity level")
    api_v1_prefix: str = Field(default="/api/v1", description="API route prefix")

    # Database Configuration
    database_url: Optional[str] = Field(
        default=None,
        description="Async database connection URL (e.g., postgresql+asyncpg://user:pass@host:5432/db)",
    )
    db_pool_size: int = Field(default=10, ge=1, le=100, description="Database connection pool size")
    db_max_overflow: int = Field(default=20, ge=0, le=100, description="Database pool max overflow")
    db_pool_timeout: float = Field(default=30.0, gt=0.0, description="Database pool checkout timeout (seconds)")
    db_pool_recycle: int = Field(default=1800, ge=60, description="Database pool recycle interval (seconds)")
    db_pool_pre_ping: bool = Field(default=True, description="Enable database pre-ping connectivity check")

    # CORS Configuration
    cors_origins: List[str] = Field(
        default_factory=list,
        description="List of allowed CORS origins. Restrictive (empty) by default.",
    )
    cors_allow_credentials: bool = Field(default=False, description="Allow credentials in CORS requests")
    cors_allow_methods: List[str] = Field(
        default_factory=lambda: ["GET", "POST", "OPTIONS"],
        description="Allowed HTTP methods for CORS",
    )
    cors_allow_headers: List[str] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "Accept", "X-Client-Version", "X-Admin-Key"],
        description="Allowed HTTP headers for CORS",
    )

    # Ingestion & Security Thresholds
    max_request_body_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1024,
        description="Maximum request payload size in bytes (default 2 MB)",
    )

    # Enrollment & Authentication Configuration
    admin_api_key: Optional[str] = Field(
        default=None,
        description="Explicit bootstrap administrator API key for initial setup and administration",
    )
    enrollment_token_expiry_hours: int = Field(
        default=24,
        ge=1,
        le=720,
        description="Default lifetime in hours for developer enrollment tokens (default 24h)",
    )
    device_credential_expiry_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=3650,
        description="Optional lifetime in days for device credentials (None for non-expiring until revoked)",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        if isinstance(v, str):
            v_upper = v.strip().upper()
            valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
            if v_upper in valid_levels:
                return v_upper
        raise ValueError(f"Invalid log level: {v}. Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> List[str]:
        if isinstance(v, str):
            cleaned = [origin.strip() for origin in v.split(",") if origin.strip()]
            return cleaned
        if isinstance(v, list):
            return [str(origin).strip() for origin in v if str(origin).strip()]
        return []

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = str(v).strip()
        if not cleaned:
            return None
        # Verify valid URI scheme
        if not (cleaned.startswith("postgresql+asyncpg://") or 
                cleaned.startswith("postgresql://") or 
                cleaned.startswith("sqlite+aiosqlite://")):
            raise ValueError(
                "DATABASE_URL must start with a valid async scheme (e.g. postgresql+asyncpg:// or sqlite+aiosqlite://)"
            )
        return cleaned

    def get_masked_database_url(self) -> str:
        """Return database URL with password masked to prevent accidental credential leakage in logs."""
        if not self.database_url:
            return "<none>"
        try:
            # Mask password portion (://user:password@host...)
            pattern = r"://([^:]+):([^@]+)@"
            masked = re.sub(pattern, r"://\1:***@", self.database_url)
            return masked
        except Exception:
            return "<masked>"

    def __repr__(self) -> str:
        return (
            f"Settings(app_name={self.app_name!r}, app_env={self.app_env!r}, "
            f"database_url={self.get_masked_database_url()!r}, log_level={self.log_level!r})"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Acquire cached application settings singleton."""
    return Settings()
