from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDMixin


class PasswordResetToken(Base, UUIDMixin):
    """Single-use, time-limited tokens for the forgot-password flow.

    The raw token is never stored — only its SHA-256 hex digest.  This means
    a DB leak cannot be used to reset passwords without access to the email.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        sa.Index("ix_password_reset_tokens_token_hash", "token_hash", unique=True),
        sa.Index("ix_password_reset_tokens_user_id", "user_id"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # "reset" = forgot-password flow; "invite" = new-user invite flow
    token_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reset", server_default="reset"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
