"""SQLAlchemy model for Developer Enrollment Tokens."""

from datetime import datetime
from enum import Enum
import uuid
from typing import Optional

from sqlalchemy import CHAR, DateTime, Enum as SAEnum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, GUID, utc_now


class TokenStatus(str, Enum):
    """Lifecycle status of enrollment invitations."""
    PENDING = "PENDING"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class EnrollmentToken(Base):
    """Cryptographic single-use bootstrap token for developer device onboarding.

    IMPORTANT: Only the SHA-256 cryptographic hash of the token is stored in token_hash.
    Plaintext tokens are never stored, logged, or recoverable.
    """

    __tablename__ = "enrollment_tokens"
    __table_args__ = (
        Index("idx_enrollment_tokens_lookup", "token_hash"),
        Index("idx_enrollment_tokens_dev", "developer_id", "status"),
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
    )
    developer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("developers.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        CHAR(64),
        unique=True,
        nullable=False,
    )
    status: Mapped[TokenStatus] = mapped_column(
        SAEnum(TokenStatus, name="token_status", native_enum=False),
        default=TokenStatus.PENDING,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    consumed_by_device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID,
        nullable=True,
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

    # Relationships
    organization: Mapped["Organization"] = relationship(  # type: ignore # noqa: F821
        "Organization",
    )
    developer: Mapped["Developer"] = relationship(  # type: ignore # noqa: F821
        "Developer",
        back_populates="enrollment_tokens",
    )
