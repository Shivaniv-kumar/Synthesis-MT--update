"""UserMFA ORM model — stores TOTP secrets for per-user MFA enrollment."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class UserMFA(Base, UUIDMixin, TimestampMixin):
    """Stores the TOTP enrollment for a single user.

    One row per user; absent means MFA is not enrolled.
    ``totp_secret`` is the base32-encoded secret that the authenticator app uses.
    In production this column should be encrypted at rest via a KMS envelope key.
    """

    __tablename__ = "user_mfa"
    __table_args__ = (sa.Index("ix_user_mfa_user_id", "user_id", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    totp_secret: Mapped[str] = mapped_column(
        String(64), nullable=False,
        comment="Base32 TOTP secret — encrypt at rest in production",
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Comma-separated list of one-time backup codes (hashed with bcrypt)
    backup_codes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Brute-force rate limiting
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
