"""KnowledgeEntry ORM model — structured knowledge extracted from meeting transcripts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin, WorkspaceMixin

KNOWLEDGE_CATEGORIES = (
    "dependency",
    "constraint",
    "decision",
    "blocker",
    "tradeoff",
    "principle",
    "assumption",
    "open_question",
)


class KnowledgeEntry(Base, UUIDMixin, TimestampMixin, TenantMixin, WorkspaceMixin):
    """A single classified knowledge item extracted from a meeting transcript."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (
        sa.Index("ix_knowledge_entries_tenant_ws", "tenant_id", "workspace_id"),
        sa.Index("ix_knowledge_entries_meeting", "meeting_id"),
        sa.Index("ix_knowledge_entries_project", "project_id"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latest_update: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    edited_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    edited_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
