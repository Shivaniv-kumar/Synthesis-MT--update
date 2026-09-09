"""003 - Add new models: notifications, platform configs, integrations, GDPR, tenants

Creates the following tables:
  - notification_rules        (NotificationRule model)
  - notification_logs         (NotificationLog model)
  - platform_configs          (PlatformConfig model)
  - integration_configs       (IntegrationConfig model)
  - item_integration_refs     (ItemIntegrationRef model)
  - gdpr_audit_events         (GDPRAuditEvent model)
  - data_retention_configs    (DataRetentionConfig model)
  - tenants                   (Tenant model)

Revision ID: 003
Revises: 002
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Alembic identifiers
# ---------------------------------------------------------------------------

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Custom enum types — must be created before the tables that use them
    # ------------------------------------------------------------------

    # Use PostgreSQL DO blocks so enum creation is idempotent inside any transaction.
    # postgresql.ENUM checkfirst=True is unreliable with asyncpg run_sync wrappers.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_rule_type AS ENUM ('assignment', 'due_soon', 'overdue', 'digest');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_channel AS ENUM ('email', 'slack', 'teams');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_status AS ENUM ('sent', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE gdpr_event_type AS ENUM ('erasure', 'export', 'access', 'consent_revoked');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE platform_type AS ENUM ('zoom', 'teams', 'google_meet');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE integration_platform AS ENUM ('jira', 'asana', 'linear', 'clickup');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE item_sync_status AS ENUM ('pending', 'synced', 'failed', 'conflict');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ------------------------------------------------------------------
    # tenants
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False, server_default="free"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # notification_rules
    # ------------------------------------------------------------------
    op.create_table(
        "notification_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "rule_type",
            postgresql.ENUM(name="notification_rule_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "channel",
            postgresql.ENUM(name="notification_channel", create_type=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_notification_rules_workspace_id",
        "notification_rules",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_rules_tenant_id",
        "notification_rules",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_rules_workspace_tenant_active",
        "notification_rules",
        ["workspace_id", "tenant_id", "is_active"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # notification_logs
    # ------------------------------------------------------------------
    op.create_table(
        "notification_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_type", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="notification_status", create_type=False),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_notification_logs_user_id",
        "notification_logs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_logs_item_id",
        "notification_logs",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_logs_sent_at",
        "notification_logs",
        ["sent_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # platform_configs
    # ------------------------------------------------------------------
    op.create_table(
        "platform_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(name="platform_type", create_type=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "platform", name="uq_platform_configs_workspace_platform"
        ),
    )
    op.create_index(
        "ix_platform_configs_workspace_id",
        "platform_configs",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_platform_configs_tenant_id",
        "platform_configs",
        ["tenant_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # integration_configs
    # ------------------------------------------------------------------
    op.create_table(
        "integration_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(name="integration_platform", create_type=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("target_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_integration_configs_workspace_platform",
        "integration_configs",
        ["workspace_id", "platform"],
        unique=False,
    )
    op.create_index(
        "ix_integration_configs_tenant_id",
        "integration_configs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_configs_workspace_platform_active",
        "integration_configs",
        ["workspace_id", "platform", "is_active"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # item_integration_refs
    # ------------------------------------------------------------------
    op.create_table(
        "item_integration_refs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integration_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("external_url", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column(
            "sync_status",
            postgresql.ENUM(name="item_sync_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_item_integration_refs_item_id",
        "item_integration_refs",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_item_integration_refs_integration_id",
        "item_integration_refs",
        ["integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_item_integration_refs_integration_external",
        "item_integration_refs",
        ["integration_id", "external_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # gdpr_audit_events
    # ------------------------------------------------------------------
    op.create_table(
        "gdpr_audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "event_type",
            postgresql.ENUM(name="gdpr_event_type", create_type=False),
            nullable=False,
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_gdpr_audit_events_tenant_id",
        "gdpr_audit_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_gdpr_audit_events_subject_user_id",
        "gdpr_audit_events",
        ["subject_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_gdpr_audit_events_event_type",
        "gdpr_audit_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_gdpr_audit_events_created_at",
        "gdpr_audit_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_gdpr_audit_events_tenant_type_created",
        "gdpr_audit_events",
        ["tenant_id", "event_type", "created_at"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # data_retention_configs
    # ------------------------------------------------------------------
    op.create_table(
        "data_retention_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("transcript_retention_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("item_retention_days", sa.Integer(), nullable=False, server_default="730"),
        sa.Column("audio_retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", name="uq_data_retention_configs_workspace_id"
        ),
    )
    op.create_index(
        "ix_data_retention_configs_tenant_id",
        "data_retention_configs",
        ["tenant_id"],
        unique=False,
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("data_retention_configs")
    op.drop_table("gdpr_audit_events")
    op.drop_table("item_integration_refs")
    op.drop_table("integration_configs")
    op.drop_table("platform_configs")
    op.drop_table("notification_logs")
    op.drop_table("notification_rules")
    op.drop_table("tenants")

    # Drop custom enum types
    sa.Enum(name="item_sync_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="integration_platform").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="platform_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="gdpr_event_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="notification_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="notification_channel").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="notification_rule_type").drop(op.get_bind(), checkfirst=True)
