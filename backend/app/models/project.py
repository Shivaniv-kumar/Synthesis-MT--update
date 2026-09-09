"""Project ORM model — organises meetings into named folders."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin, WorkspaceMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting


class Project(Base, UUIDMixin, TimestampMixin, TenantMixin, WorkspaceMixin):
    __tablename__ = "projects"
    __table_args__ = (
        sa.Index("ix_projects_tenant_workspace", "tenant_id", "workspace_id"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hex colour string, e.g. "#6366f1" — null means use the default UI colour
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    meetings: Mapped[list[Meeting]] = relationship("Meeting", back_populates="project")
