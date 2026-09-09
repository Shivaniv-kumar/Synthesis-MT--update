"""Meeting ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin, WorkspaceMixin

if TYPE_CHECKING:
    from app.models.action_item import ActionItem
    from app.models.extraction_run import ExtractionRun
    from app.models.project import Project


class Meeting(Base, UUIDMixin, TimestampMixin, TenantMixin, WorkspaceMixin):
    __tablename__ = "meetings"
    __table_args__ = (
        sa.Index("ix_meetings_tenant_workspace", "tenant_id", "workspace_id"),
    )

    title: Mapped[str] = mapped_column(
        String(512), nullable=False, default="Untitled Meeting"
    )
    source_type: Mapped[str] = mapped_column(
        Enum("paste", "file", "audio", "integration", name="meeting_source_type"),
        nullable=False,
        default="paste",
    )
    occurred_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Stored as PostgreSQL TEXT[] — names / emails of attendees
    attendees: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    # Raw transcript stored in-DB for small transcripts
    transcript_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # S3 object key for large transcripts / audio
    transcript_ref: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("draft", "extracting", "extracted", "reviewed", name="meeting_status"),
        nullable=False,
        default="draft",
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    project: Mapped[Optional[Project]] = relationship("Project", back_populates="meetings")
    action_items: Mapped[list[ActionItem]] = relationship(
        "ActionItem",
        back_populates="meeting",
        cascade="all, delete-orphan",
    )
    extraction_runs: Mapped[list[ExtractionRun]] = relationship(
        "ExtractionRun",
        back_populates="meeting",
        cascade="all, delete-orphan",
    )
