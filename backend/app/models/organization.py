"""SQLAlchemy models for Organizations, Teams, and Team Memberships."""

from datetime import datetime
import uuid
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, TimestampMixin, utc_now


class Organization(Base, TimestampMixin):
    """Tenant boundary entity. All developers, devices, and telemetry belong to an organization."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Relationships
    teams: Mapped[List["Team"]] = relationship(
        "Team",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    developers: Mapped[List["Developer"]] = relationship(  # type: ignore # noqa: F821
        "Developer",
        back_populates="organization",
    )
    devices: Mapped[List["Device"]] = relationship(  # type: ignore # noqa: F821
        "Device",
        back_populates="organization",
    )


class Team(Base):
    """Logical team grouping within an organization (e.g. Frontend, Core Platform)."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_team_org_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="teams",
    )
    memberships: Mapped[List["TeamMembership"]] = relationship(
        "TeamMembership",
        back_populates="team",
        cascade="all, delete-orphan",
    )


class TeamMembership(Base):
    """Associative entity linking developers to teams."""

    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "developer_id", name="uq_team_membership"),
        Index("idx_team_memberships_dev", "developer_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("developers.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    # Relationships
    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="memberships",
    )
    developer: Mapped["Developer"] = relationship(  # type: ignore # noqa: F821
        "Developer",
        back_populates="team_memberships",
    )
