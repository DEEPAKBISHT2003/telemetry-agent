"""Add payload_fingerprint to upload_batches for batch idempotency.

Revision ID: 0003_add_batch_payload_fingerprint
Revises: 0002_add_credential_expiry_and_token_revocation
Create Date: 2026-10-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_add_batch_payload_fingerprint"
down_revision: Union[str, None] = "0002_add_credential_expiry_and_token_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add payload_fingerprint to upload_batches for detecting replayed/altered batches
    op.add_column(
        "upload_batches",
        sa.Column("payload_fingerprint", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("upload_batches", "payload_fingerprint")
