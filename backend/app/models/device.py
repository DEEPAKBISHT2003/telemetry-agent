"""SQLAlchemy models for Enrolled Devices and Device Credentials."""

from datetime import datetime
from enum import Enum
import uuid
from typing import List, Optional

from sqlalchemy import Boolean, CHAR, DateTime, Enum as SAEnum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, utc_now


class DeviceStatus(str, Enum):
    """Lifecycle state of an enrolled developer workstation or laptop."""
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUSPENDED = "SUSPENDED"


class Device(Base):
    """Enrolled physical or virtual execution endpoint."""

    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("developer_id", "hardware_uuid", name="uq_dev_hardware"),
        Index("idx_devices_org_dev", "organization_id", "developer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("developers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    hardware_uuid: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    hostname: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    agent_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    status: Mapped[DeviceStatus] = mapped_column(
        SAEnum(DeviceStatus, name="device_status", native_enum=False),
        default=DeviceStatus.ACTIVE,
        nullable=False,
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # type: ignore # noqa: F821
        "Organization",
        back_populates="devices",
    )
    developer: Mapped["Developer"] = relationship(  # type: ignore # noqa: F821
        "Developer",
        back_populates="devices",
    )
    credentials: Mapped[List["DeviceCredential"]] = relationship(
        "DeviceCredential",
        back_populates="device",
        cascade="all, delete-orphan",
    )
    batches: Mapped[List["UploadBatch"]] = relationship(  # type: ignore # noqa: F821
        "UploadBatch",
        back_populates="device",
    )
    telemetry_events: Mapped[List["TelemetryEvent"]] = relationship(  # type: ignore # noqa: F821
        "TelemetryEvent",
        back_populates="device",
    )


class DeviceCredential(Base):
    """Cryptographic device authentication secret for batch telemetry upload.

    IMPORTANT: Only the SHA-256 cryptographic hash of the Bearer token (dvt_...)
    is stored in credential_hash. Plaintext tokens are returned only once upon enrollment.
    """

    __tablename__ = "device_credentials"
    __table_args__ = (
        Index("idx_device_creds_active", "credential_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )
    credential_hash: Mapped[str] = mapped_column(
        CHAR(64),
        unique=True,
        nullable=False,
    )
    token_prefix: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    device: Mapped["Device"] = relationship(
        "Device",
        back_populates="credentials",
    )
