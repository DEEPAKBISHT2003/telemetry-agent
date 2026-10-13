"""SQLAlchemy model for Ingestion Upload Batches."""

from datetime import datetime
from enum import Enum
import uuid
from typing import List, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, utc_now


class BatchStatus(str, Enum):
    """Status of an uploaded telemetry batch."""
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class UploadBatch(Base):
    """Batch-level ingestion envelope for network deduplication and idempotency."""

    __tablename__ = "upload_batches"
    __table_args__ = (
        Index("idx_batches_org_device", "organization_id", "device_id", "server_received_at"),
    )

    # Client-generated UUID for batch-level idempotency
    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("developers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    payload_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    status: Mapped[BatchStatus] = mapped_column(
        SAEnum(BatchStatus, name="batch_status", native_enum=False),
        default=BatchStatus.RECEIVED,
        nullable=False,
    )
    client_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # type: ignore # noqa: F821
        "Organization",
    )
    device: Mapped["Device"] = relationship(  # type: ignore # noqa: F821
        "Device",
        back_populates="batches",
    )
    developer: Mapped["Developer"] = relationship(  # type: ignore # noqa: F821
        "Developer",
    )
    events: Mapped[List["TelemetryEvent"]] = relationship(  # type: ignore # noqa: F821
        "TelemetryEvent",
        back_populates="batch",
    )
