"""005 - Add projects, item_comments, user_project_access tables; add missing columns

Creates:
  - projects                  (Project model)
  - item_comments             (ItemComment model)
  - user_project_access       (UserProjectAccess model)

Adds missing columns:
  - meetings.project_id       (FK → projects.id)
  - action_items.reviewed_by  (FK → users.id)
  - action_items.reviewed_at  (TIMESTAMPTZ)

Revision ID: 005
Revises: 004
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])
    op.create_index(
        "ix_projects_tenant_workspace", "projects", ["tenant_id", "workspace_id"]
    )

    # ------------------------------------------------------------------
    # meetings.project_id  (FK → projects.id)
    # ------------------------------------------------------------------
    op.add_column(
        "meetings",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_meetings_project_id", "meetings", ["project_id"])

    # ------------------------------------------------------------------
    # action_items: reviewed_by, reviewed_at
    # ------------------------------------------------------------------
    op.add_column(
        "action_items",
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "action_items",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # item_comments
    # ------------------------------------------------------------------
    op.create_table(
        "item_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
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
    op.create_index("ix_item_comments_item_id", "item_comments", ["item_id"])
    op.create_index("ix_item_comments_tenant_id", "item_comments", ["tenant_id"])

    # ------------------------------------------------------------------
    # user_project_access
    # ------------------------------------------------------------------
    op.create_table(
        "user_project_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project_access"),
    )
    op.create_index("ix_user_project_access_tenant_id", "user_project_access", ["tenant_id"])
    op.create_index(
        "ix_user_project_access_workspace_id", "user_project_access", ["workspace_id"]
    )
    op.create_index("ix_user_project_access_user_id", "user_project_access", ["user_id"])
    op.create_index(
        "ix_user_project_access_project_id", "user_project_access", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("user_project_access")
    op.drop_table("item_comments")
    op.drop_column("action_items", "reviewed_at")
    op.drop_column("action_items", "reviewed_by")
    op.drop_index("ix_meetings_project_id", table_name="meetings")
    op.drop_column("meetings", "project_id")
    op.drop_table("projects")
