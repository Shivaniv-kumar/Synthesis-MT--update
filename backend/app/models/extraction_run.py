"""ExtractionRun ORM model.

Tracks each call to the Claude extraction API so we can audit cost,
latency, and prompt-version history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

import sqlalchemy as sa
from app.database import Base
from app.models.base import UUIDMixin

if TYPE_CHECKING:
    from app.models.meeting import Meeting


class ExtractionRun(Base, UUIDMixin):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        sa.Index("ix_extraction_runs_meeting_id", "meeting_id"),
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    meeting: Mapped[Meeting] = relationship("Meeting", back_populates="extraction_runs")
