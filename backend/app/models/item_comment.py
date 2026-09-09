"""ItemComment ORM model — per-item threaded comments/updates."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy import Text, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class ItemComment(Base, UUIDMixin, TimestampMixin):
    """A single comment or progress update on an action item.

    ``user_id`` is nullable so comments survive user deletion.
    ``user_display_name`` is denormalized for fast display without a join.
    ``tenant_id`` is stored directly for tenant-scoped queries without joining
    through action_items.
    """

    __tablename__ = "item_comments"
    __table_args__ = (
        sa.Index("ix_item_comments_item_id", "item_id"),
        sa.Index("ix_item_comments_tenant_id", "tenant_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("action_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_display_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
