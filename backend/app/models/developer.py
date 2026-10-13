"""SQLAlchemy model for Developers."""

from enum import Enum
import uuid
from typing import List

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, TimestampMixin


class DeveloperRole(str, Enum):
    """Developer authorization roles."""
    ORG_ADMIN = "ORG_ADMIN"
    TEAM_LEAD = "TEAM_LEAD"
    DEVELOPER = "DEVELOPER"
    AUDITOR = "AUDITOR"


class AccountStatus(str, Enum):
    """Developer account lifecycle state."""
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class Developer(Base, TimestampMixin):
    """Developer human identity entity within an organization."""

    __tablename__ = "developers"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_dev_org_email"),
        Index("idx_developers_org", "organization_id", "status"),
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
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[DeveloperRole] = mapped_column(
        SAEnum(DeveloperRole, name="developer_role", native_enum=False),
        default=DeveloperRole.DEVELOPER,
        nullable=False,
    )
    status: Mapped[AccountStatus] = mapped_column(
        SAEnum(AccountStatus, name="account_status", native_enum=False),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(  # type: ignore # noqa: F821
        "Organization",
        back_populates="developers",
    )
    team_memberships: Mapped[List["TeamMembership"]] = relationship(  # type: ignore # noqa: F821
        "TeamMembership",
        back_populates="developer",
        cascade="all, delete-orphan",
    )
    devices: Mapped[List["Device"]] = relationship(  # type: ignore # noqa: F821
        "Device",
        back_populates="developer",
    )
    enrollment_tokens: Mapped[List["EnrollmentToken"]] = relationship(  # type: ignore # noqa: F821
        "EnrollmentToken",
        back_populates="developer",
        cascade="all, delete-orphan",
    )
    telemetry_events: Mapped[List["TelemetryEvent"]] = relationship(  # type: ignore # noqa: F821
        "TelemetryEvent",
        back_populates="developer",
    )
