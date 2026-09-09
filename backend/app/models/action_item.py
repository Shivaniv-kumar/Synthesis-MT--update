"""ActionItem ORM model."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin, WorkspaceMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting
    from app.models.user import User


class ActionItem(Base, UUIDMixin, TimestampMixin, TenantMixin, WorkspaceMixin):
    __tablename__ = "action_items"
    __table_args__ = (
        sa.Index("ix_action_items_meeting_id", "meeting_id"),
        sa.Index("ix_action_items_tenant_workspace", "tenant_id", "workspace_id"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    priority: Mapped[str] = mapped_column(
        Enum("High", "Medium", "Low", name="action_item_priority"),
        nullable=False,
        default="Medium",
    )
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_text: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        Enum("Open", "In progress", "Done", name="action_item_status"),
        nullable=False,
        default="Open",
    )
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="action_items")
    owner_user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[owner_user_id],
    )
    created_by_user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[created_by],
    )
    reviewed_by_user: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )
