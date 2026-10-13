"""Pydantic schemas for device identity and credential administration."""

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class DeviceIdentityResponse(BaseModel):
    """Enrolled device identity and organizational attribution response."""

    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID = Field(..., description="Device unique identifier")
    organization_id: uuid.UUID = Field(..., description="Organization UUID")
    organization_slug: str = Field(..., description="Organization slug identifier")
    developer_id: uuid.UUID = Field(..., description="Developer UUID")
    developer_email: str = Field(..., description="Developer email address")
    developer_name: str = Field(..., description="Developer full name")
    developer_role: str = Field(..., description="Developer role (e.g. ORG_ADMIN, DEVELOPER)")
    hardware_uuid: str = Field(..., description="Workstation hardware UUID")
    hostname: str = Field(..., description="Hostname of workstation")
    platform: str = Field(..., description="Operating system platform")
    agent_version: str = Field(..., description="Telemetry agent version")
    status: str = Field(..., description="Device status (e.g. ACTIVE, REVOKED)")
    enrolled_at: datetime = Field(..., description="Device enrollment timestamp")
    last_seen_at: Optional[datetime] = Field(default=None, description="Last telemetry contact timestamp")


class CredentialRevokeResponse(BaseModel):
    """Response returned when an administrator revokes a specific device credential."""

    credential_id: uuid.UUID
    device_id: uuid.UUID
    is_active: bool
    revoked_at: datetime


class DeviceRevokeResponse(BaseModel):
    """Response returned when an administrator revokes an entire device and all its credentials."""

    device_id: uuid.UUID
    status: str
    revoked_credentials_count: int
    revoked_at: datetime
