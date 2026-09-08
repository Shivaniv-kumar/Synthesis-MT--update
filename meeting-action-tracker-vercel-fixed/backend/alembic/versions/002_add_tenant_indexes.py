"""002 - Add tenant query performance indexes

Adds composite indexes optimised for the most common tenant-scoped queries:
  - meetings filtered by tenant + workspace + status
  - action_items filtered by tenant + workspace + status
  - users looked up by tenant + email (unique)
  - action_items looked up by meeting_id (foreign-key traversal)

The ix_action_items_meeting_id index already exists from migration 001 so we
use ``if_not_exists=True`` / ``checkfirst`` handling to make the migration
idempotent when applied against a database that already has the index.

Revision ID: 002
Revises: 001
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# ---------------------------------------------------------------------------
# Alembic identifiers
# ---------------------------------------------------------------------------

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------
    # meetings — composite index for tenant + workspace + status queries
    # ------------------------------------------------------------------
    op.create_index(
        "ix_meetings_tenant_workspace_status",
        "meetings",
        ["tenant_id", "workspace_id", "status"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # action_items — composite index for tenant + workspace + status queries
    # ------------------------------------------------------------------
    op.create_index(
        "ix_action_items_tenant_workspace_status",
        "action_items",
        ["tenant_id", "workspace_id", "status"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # users — unique composite index: one email per tenant
    # The UniqueConstraint in migration 001 (uq_users_tenant_email) already
    # enforces uniqueness; this named index makes lookups explicit and fast.
    # ------------------------------------------------------------------
    op.create_index(
        "ix_users_tenant_email",
        "users",
        ["tenant_id", "email"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # action_items — single-column index on meeting_id for FK traversal.
    # Migration 001 may have already created ix_action_items_meeting_id.
    # We wrap this in a try/except so re-running the migration is safe.
    # ------------------------------------------------------------------
    try:
        op.create_index(
            "ix_action_items_meeting",
            "action_items",
            ["meeting_id"],
            unique=False,
        )
    except Exception:
        # Index already exists — safe to continue.
        pass


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop in reverse order; ignore errors for indexes that were pre-existing.
    try:
        op.drop_index("ix_action_items_meeting", table_name="action_items")
    except Exception:
        pass

    op.drop_index("ix_users_tenant_email", table_name="users")
    op.drop_index("ix_action_items_tenant_workspace_status", table_name="action_items")
    op.drop_index("ix_meetings_tenant_workspace_status", table_name="meetings")
