"""001 - Initial schema

Creates all tables for the Meeting Action Tracker:
    users, workspaces, workspace_members, meetings,
    action_items, audit_logs, extraction_runs

Revision ID: 001
Revises: (none)
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Alembic identifiers
# ---------------------------------------------------------------------------

revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enum types (created before the tables that reference them)
    # ------------------------------------------------------------------

    # DO blocks are idempotent and safe inside asyncpg run_sync transaction wrappers.
    for name, values in (
        ("user_role", "('Admin', 'Member', 'Viewer')"),
        ("workspace_member_role", "('Admin', 'Member', 'Viewer')"),
        ("meeting_source_type", "('paste', 'file', 'audio', 'integration')"),
        ("meeting_status", "('draft', 'extracting', 'extracted', 'reviewed')"),
        ("action_item_priority", "('High', 'Medium', 'Low')"),
        ("action_item_status", "('Open', 'In progress', 'Done')"),
    ):
        op.execute(f"""
            DO $$ BEGIN
                CREATE TYPE {name} AS ENUM {values};
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """)

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("sso_subject", sa.String(512), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column(
            "role",
            postgresql.ENUM(
                "Admin", "Member", "Viewer", name="user_role", create_type=False
            ),
            nullable=False,
            server_default="Member",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_sso_subject", "users", ["sso_subject"], unique=True)
    op.create_unique_constraint(
        "uq_users_tenant_email", "users", ["tenant_id", "email"]
    )

    # ------------------------------------------------------------------
    # workspaces
    # ------------------------------------------------------------------

    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_index("ix_workspaces_tenant_id", "workspaces", ["tenant_id"])

    # ------------------------------------------------------------------
    # workspace_members
    # ------------------------------------------------------------------

    op.create_table(
        "workspace_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            postgresql.ENUM(
                "Admin",
                "Member",
                "Viewer",
                name="workspace_member_role",
                create_type=False,
            ),
            nullable=False,
            server_default="Member",
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
        "ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"]
    )
    op.create_index(
        "ix_workspace_members_user_id", "workspace_members", ["user_id"]
    )

    # ------------------------------------------------------------------
    # meetings
    # ------------------------------------------------------------------

    op.create_table(
        "meetings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "title", sa.String(512), nullable=False, server_default="Untitled Meeting"
        ),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                "paste",
                "file",
                "audio",
                "integration",
                name="meeting_source_type",
                create_type=False,
            ),
            nullable=False,
            server_default="paste",
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attendees",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("transcript_ref", sa.String(1024), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft",
                "extracting",
                "extracted",
                "reviewed",
                name="meeting_status",
                create_type=False,
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    op.create_index("ix_meetings_tenant_id", "meetings", ["tenant_id"])
    op.create_index("ix_meetings_workspace_id", "meetings", ["workspace_id"])
    op.create_index(
        "ix_meetings_tenant_workspace", "meetings", ["tenant_id", "workspace_id"]
    )

    # ------------------------------------------------------------------
    # action_items
    # ------------------------------------------------------------------

    op.create_table(
        "action_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("owner_label", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "priority",
            postgresql.ENUM(
                "High", "Medium", "Low", name="action_item_priority", create_type=False
            ),
            nullable=False,
            server_default="Medium",
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_text", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "status",
            postgresql.ENUM(
                "Open",
                "In progress",
                "Done",
                name="action_item_status",
                create_type=False,
            ),
            nullable=False,
            server_default="Open",
        ),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    op.create_index("ix_action_items_meeting_id", "action_items", ["meeting_id"])
    op.create_index("ix_action_items_tenant_id", "action_items", ["tenant_id"])
    op.create_index("ix_action_items_workspace_id", "action_items", ["workspace_id"])
    op.create_index(
        "ix_action_items_tenant_workspace",
        "action_items",
        ["tenant_id", "workspace_id"],
    )

    # ------------------------------------------------------------------
    # audit_logs
    # ------------------------------------------------------------------

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])
    op.create_index(
        "ix_audit_logs_tenant_entity",
        "audit_logs",
        ["tenant_id", "entity_type", "entity_id"],
    )
    op.create_index(
        "ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"]
    )

    # ------------------------------------------------------------------
    # extraction_runs
    # ------------------------------------------------------------------

    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_extraction_runs_meeting_id", "extraction_runs", ["meeting_id"]
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("extraction_runs")
    op.drop_table("audit_logs")
    op.drop_table("action_items")
    op.drop_table("meetings")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")

    # Drop enum types
    for enum_name in (
        "action_item_status",
        "action_item_priority",
        "meeting_status",
        "meeting_source_type",
        "workspace_member_role",
        "user_role",
    ):
        postgresql.ENUM(name=enum_name, create_type=False).drop(
            op.get_bind(), checkfirst=True
        )
