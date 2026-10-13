"""SQLAlchemy models for Telemetry Events and Daily Rollup Metrics."""

from datetime import date, datetime
from decimal import Decimal
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, GUID, utc_now

# Use PostgreSQL native JSONB for performance, JSON for dialect compatibility
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class TelemetryEvent(Base):
    """Normalized, immutable raw telemetry event stored in central database.

    IMPORTANT ARCHITECTURAL & PRIVACY CONSTRAINTS:
    - Unpartitioned table with global single-column PRIMARY KEY on id.
    - Zero columns for prompts, completions, transcripts, source code, or raw tool arguments.
    - Local workstation repository paths (context.repository.root) are explicitly excluded.
    - All token counters are exact integer counts; never fabricated.
    - cost_usd is authoritatively NULL unless an authoritative billing provider is established.
    """

    __tablename__ = "telemetry_events"
    __table_args__ = (
        Index("idx_te_org_time", "organization_id", "event_timestamp"),
        Index("idx_te_org_dev_time", "organization_id", "developer_id", "event_timestamp"),
        Index("idx_te_org_repo_time", "organization_id", "repository_name", "event_timestamp"),
        Index("idx_te_session", "session_id"),
    )

    # Client-generated deterministic RFC 4122 UUIDv5 (or UUIDv4)
    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
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
    device_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("devices.id", ondelete="RESTRICT"),
        nullable=False,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("upload_batches.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Canonical event type (e.g. ai_run, ai_run_started, ai_tool_call)
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    # Contextual identifiers
    session_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )
    repository_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    branch: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Model and tool metadata
    model_name: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    tool_name: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )

    # Exact token metrics (integers only; null if not reported by source)
    tokens_input_uncached: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    tokens_input_cached: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    tokens_output: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )
    tokens_total: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
    )

    # Cost tracking (null unless authoritative financial provider is connected)
    cost_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 6),
        nullable=True,
        default=None,
    )

    # Structured metadata sanitized of any sensitive prompts or source code
    raw_payload: Mapped[Dict[str, Any]] = mapped_column(
        JSON_TYPE,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # type: ignore # noqa: F821
        "Organization",
    )
    developer: Mapped["Developer"] = relationship(  # type: ignore # noqa: F821
        "Developer",
        back_populates="telemetry_events",
    )
    device: Mapped["Device"] = relationship(  # type: ignore # noqa: F821
        "Device",
        back_populates="telemetry_events",
    )
    batch: Mapped["UploadBatch"] = relationship(  # type: ignore # noqa: F821
        "UploadBatch",
        back_populates="events",
    )


class DailyDeveloperMetrics(Base):
    """Pre-aggregated daily rollups grouped by organization, developer, repo, and model.

    Enforces uniqueness across the complete multi-dimensional aggregation tuple
    to prevent duplicate rollup drift.
    """

    __tablename__ = "daily_developer_metrics"
    __table_args__ = (
        Index("idx_ddm_org_date", "organization_id", "date"),
    )

    # Multi-dimensional composite primary key
    date: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("developers.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    repository_name: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        default="unknown",
    )
    model_name: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        default="unknown",
    )

    # Aggregated volume and run counts
    ai_runs_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    tool_calls_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Aggregated token counters
    tokens_input_uncached: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    tokens_input_cached: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    tokens_output: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )
    tokens_total: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


# Alias for compatibility with Phase 5.3 prompt reference
DailyMetrics = DailyDeveloperMetrics
