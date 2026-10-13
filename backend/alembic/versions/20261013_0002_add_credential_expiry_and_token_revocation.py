"""Add credential expiry and enrollment token revocation columns.

Revision ID: 0002_add_credential_expiry_and_token_revocation
Revises: 0001_initial_schema
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_add_credential_expiry_and_token_revocation"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add revoked_at to enrollment_tokens for audit-friendly revocation timestamping
    op.add_column(
        "enrollment_tokens",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add expires_at to device_credentials for configurable credential lifetimes
    op.add_column(
        "device_credentials",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_credentials", "expires_at")
    op.drop_column("enrollment_tokens", "revoked_at")
