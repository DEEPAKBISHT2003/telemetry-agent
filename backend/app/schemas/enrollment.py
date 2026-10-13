"""Pydantic schemas for developer and device enrollment operations."""

from datetime import datetime
import re
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.developer import DeveloperRole

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EnrollmentInviteRequest(BaseModel):
    """Administrative request to invite/enroll a developer and issue an enrollment token."""

    organization_id: uuid.UUID = Field(
        ...,
        description="Target organization UUID where developer belongs",
    )
    email: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="Developer corporate or personal email address",
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Developer full display name",
    )
    role: DeveloperRole = Field(
        default=DeveloperRole.DEVELOPER,
        description="Developer permission role within the organization",
    )
    expires_in_hours: Optional[int] = Field(
        default=None,
        ge=1,
        le=720,
        description="Custom expiration window in hours (defaults to server config)",
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        clean = v.strip().lower()
        if not EMAIL_REGEX.match(clean):
            raise ValueError(f"Invalid email address format: {v}")
        return clean


class EnrollmentInviteResponse(BaseModel):
    """Response containing one-time plaintext enrollment token for developer invitation."""

    model_config = ConfigDict(from_attributes=True)

    token_id: uuid.UUID = Field(..., description="Unique ID of the issued enrollment token")
    enrollment_token: str = Field(
        ...,
        description="Single-use plaintext enrollment token (returned ONLY once upon issuance)",
    )
    organization_id: uuid.UUID = Field(..., description="Organization UUID")
    developer_id: uuid.UUID = Field(..., description="Developer UUID")
    email: str = Field(..., description="Developer email")
    expires_at: datetime = Field(..., description="UTC timestamp after which token cannot be redeemed")
    created_at: datetime = Field(..., description="UTC timestamp of token creation")


class EnrollmentClaimRequest(BaseModel):
    """Client device request to redeem a single-use enrollment token."""

    enrollment_token: str = Field(
        ...,
        min_length=16,
        max_length=128,
        description="Plaintext enrollment token beginning with 'dve_'",
    )
    hardware_uuid: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Stable hardware identifier of the client workstation",
    )
    hostname: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Operating system hostname",
    )
    platform: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Operating system platform description (e.g. windows, darwin, linux)",
    )
    agent_version: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Telemetry agent software version (e.g. 0.1.2)",
    )


class EnrollmentClaimResponse(BaseModel):
    """Response returned upon successful enrollment with single-use device credential."""

    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID = Field(..., description="Enrolled device UUID")
    device_credential: str = Field(
        ...,
        description="Plaintext device Bearer token beginning with 'dvt_' (returned ONLY once)",
    )
    token_prefix: str = Field(..., description="Device credential prefix for identifier lookups")
    organization_id: uuid.UUID = Field(..., description="Organization UUID")
    developer_id: uuid.UUID = Field(..., description="Developer UUID")
    hardware_uuid: str = Field(..., description="Enrolled hardware UUID")
    enrolled_at: datetime = Field(..., description="UTC timestamp when device enrolled")
    expires_at: Optional[datetime] = Field(
        default=None,
        description="UTC expiration timestamp of device credential if applicable",
    )


class TokenRevokeResponse(BaseModel):
    """Response when an administrator revokes an unconsumed enrollment invitation."""

    token_id: uuid.UUID
    status: str
    revoked_at: datetime
