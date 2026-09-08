"""AuditLog ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, UUIDMixin


class AuditLog(Base, UUIDMixin, TenantMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(
            "ix_audit_logs_tenant_entity",
            "tenant_id",
            "entity_type",
            "entity_id",
        ),
    )

    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # create | update | delete | view
    before_data: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    after_data: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
