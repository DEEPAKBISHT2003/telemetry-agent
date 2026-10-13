"""Initial schema: organizations, developers, devices, telemetry events, and rollups.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-10-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Widen alembic_version.version_num on PostgreSQL to accommodate descriptive revision identifiers (>32 chars)
    bind = op.get_bind()
    if bind is not None and getattr(bind.dialect, "name", "") == "postgresql":
        op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")

    # 1. Organizations table
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("idx_organizations_slug", "organizations", ["slug"])

    # 2. Teams table
    op.create_table(
        "teams",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_teams_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint("organization_id", "name", name="uq_team_org_name"),
    )
    op.create_index("idx_teams_organization_id", "teams", ["organization_id"])

    # 3. Developers table
    op.create_table(
        "developers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="DEVELOPER"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_developers_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_developers"),
        sa.UniqueConstraint("organization_id", "email", name="uq_dev_org_email"),
    )
    op.create_index("idx_developers_email", "developers", ["email"])
    op.create_index("idx_developers_org", "developers", ["organization_id", "status"])

    # 4. Team Memberships table
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_team_memberships_team_id_teams",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_team_memberships_developer_id_developers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_team_memberships"),
        sa.UniqueConstraint("team_id", "developer_id", name="uq_team_membership"),
    )
    op.create_index("idx_team_memberships_dev", "team_memberships", ["developer_id"])

    # 5. Enrollment Tokens table
    op.create_table(
        "enrollment_tokens",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_device_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_enrollment_tokens_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_enrollment_tokens_developer_id_developers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_enrollment_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_enrollment_tokens_token_hash"),
    )
    op.create_index("idx_enrollment_tokens_lookup", "enrollment_tokens", ["token_hash"])
    op.create_index("idx_enrollment_tokens_dev", "enrollment_tokens", ["developer_id", "status"])

    # 6. Devices table
    op.create_table(
        "devices",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("hardware_uuid", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_devices_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_devices_developer_id_developers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.UniqueConstraint("developer_id", "hardware_uuid", name="uq_dev_hardware"),
    )
    op.create_index("idx_devices_org_dev", "devices", ["organization_id", "developer_id"])

    # 7. Device Credentials table
    op.create_table(
        "device_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_hash", sa.CHAR(64), nullable=False),
        sa.Column("token_prefix", sa.String(12), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name="fk_device_credentials_device_id_devices",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_device_credentials"),
        sa.UniqueConstraint("credential_hash", name="uq_device_credentials_credential_hash"),
    )
    op.create_index("idx_device_creds_active", "device_credentials", ["credential_hash"])

    # 8. Upload Batches table
    op.create_table(
        "upload_batches",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="RECEIVED"),
        sa.Column("client_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_upload_batches_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name="fk_upload_batches_device_id_devices",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_upload_batches_developer_id_developers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_upload_batches"),
    )
    op.create_index(
        "idx_batches_org_device",
        "upload_batches",
        ["organization_id", "device_id", "server_received_at"],
    )

    # 9. Telemetry Events table (Unpartitioned with globally unique single-column PK)
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=True),
        sa.Column("repository_name", sa.String(255), nullable=True),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("tokens_input_uncached", sa.BigInteger(), nullable=True),
        sa.Column("tokens_input_cached", sa.BigInteger(), nullable=True),
        sa.Column("tokens_output", sa.BigInteger(), nullable=True),
        sa.Column("tokens_total", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "raw_payload",
            sa.JSON().with_variant(postgresql.JSONB, "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_telemetry_events_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_telemetry_events_developer_id_developers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name="fk_telemetry_events_device_id_devices",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["upload_batches.id"],
            name="fk_telemetry_events_batch_id_upload_batches",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telemetry_events"),
    )
    op.create_index(
        "idx_te_org_time",
        "telemetry_events",
        ["organization_id", "event_timestamp"],
    )
    op.create_index(
        "idx_te_org_dev_time",
        "telemetry_events",
        ["organization_id", "developer_id", "event_timestamp"],
    )
    op.create_index(
        "idx_te_org_repo_time",
        "telemetry_events",
        ["organization_id", "repository_name", "event_timestamp"],
    )
    op.create_index("idx_te_session", "telemetry_events", ["session_id"])
    op.create_index("idx_te_event_type", "telemetry_events", ["event_type"])
    op.create_index("idx_te_model_name", "telemetry_events", ["model_name"])

    # 10. Daily Developer Metrics (Rollup table)
    op.create_table(
        "daily_developer_metrics",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_name", sa.String(255), nullable=False, server_default="unknown"),
        sa.Column("model_name", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("ai_runs_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_input_uncached", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_input_cached", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_daily_developer_metrics_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["developer_id"],
            ["developers.id"],
            name="fk_daily_developer_metrics_developer_id_developers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "date",
            "organization_id",
            "developer_id",
            "repository_name",
            "model_name",
            name="pk_daily_developer_metrics",
        ),
    )
    op.create_index(
        "idx_ddm_org_date",
        "daily_developer_metrics",
        ["organization_id", "date"],
    )


def downgrade() -> None:
    op.drop_table("daily_developer_metrics")
    op.drop_table("telemetry_events")
    op.drop_table("upload_batches")
    op.drop_table("device_credentials")
    op.drop_table("devices")
    op.drop_table("enrollment_tokens")
    op.drop_table("team_memberships")
    op.drop_table("developers")
    op.drop_table("teams")
    op.drop_table("organizations")
