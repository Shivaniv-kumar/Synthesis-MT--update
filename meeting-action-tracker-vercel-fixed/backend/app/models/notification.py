"""Notification ORM models.

Two tables:
- NotificationRule  : per-workspace configuration that determines when and how to notify users.
- NotificationLog   : immutable log entry written after every send attempt (success or failure).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, UUIDMixin


# ---------------------------------------------------------------------------
# Enum types — declared as Python enums so they are re-usable in Pydantic schemas
# ---------------------------------------------------------------------------

ChannelEnum = Enum(
    "email",
    "slack",
    "teams",
    name="notification_channel",
)

NotificationStatusEnum = Enum(
    "sent",
    "failed",
    name="notification_status",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NotificationRule(Base, UUIDMixin, TenantMixin):
    """Configuration row that enables a specific type of notification for a workspace.

    ``config`` is a freeform JSON bag whose keys depend on ``rule_type``:

    * ``assignment`` — no extra keys required; optionally ``{"webhook_url": "..."}``
    * ``due_soon``   — ``{"days_before_due": 2}``
    * ``overdue``    — no extra keys required
    * ``digest``     — ``{"digest_day": "Monday"}``  (used by the weekly digest beat task)

    If ``channel`` is ``slack`` or ``teams``, ``config`` **must** contain ``webhook_url``.
    """

    __tablename__ = "notification_rules"
    __table_args__ = (
        sa.Index("ix_notification_rules_workspace_id", "workspace_id"),
        sa.Index("ix_notification_rules_tenant_id", "tenant_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    rule_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(
        ChannelEnum,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Channel/rule-specific config. "
            "e.g. {'days_before_due': 2, 'digest_day': 'Monday', 'webhook_url': '...'}"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class NotificationLog(Base, UUIDMixin):
    """Immutable audit record of every notification send attempt.

    Written by NotificationService regardless of success or failure.
    Rows are never updated — a retry produces a new row.
    """

    __tablename__ = "notification_logs"
    __table_args__ = (
        sa.Index("ix_notification_logs_user_id", "user_id"),
        sa.Index("ix_notification_logs_item_id", "item_id"),
        sa.Index("ix_notification_logs_sent_at", "sent_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Recipient user.",
    )
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("action_items.id", ondelete="SET NULL"),
        nullable=True,
        comment="The action item this notification relates to (NULL for digest).",
    )
    rule_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Matches NotificationRule.rule_type value.",
    )
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Delivery channel: email | slack | teams.",
    )
    status: Mapped[str] = mapped_column(
        NotificationStatusEnum,
        nullable=False,
        comment="sent | failed",
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.utcnow(),
        comment="Wall-clock time of the send attempt (UTC).",
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Exception / HTTP error detail when status=failed.",
    )
