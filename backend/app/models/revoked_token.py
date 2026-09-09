"""RevokedToken — DB-backed JWT revocation list.

Keyed on the JWT ``jti`` claim.  A row here means the token must be rejected
even if its signature and expiry are still valid.

Cleanup: expired rows are deleted lazily in the logout handler so the table
does not grow unbounded.  The unique index on ``jti`` keeps revocation checks
fast (single index lookup per authenticated request).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UUIDMixin


class RevokedToken(Base, UUIDMixin):
    """A JWT that has been explicitly revoked via logout or key rotation."""

    __tablename__ = "revoked_tokens"
    __table_args__ = (
        sa.Index("ix_revoked_tokens_jti", "jti", unique=True),
        sa.Index("ix_revoked_tokens_expires_at", "expires_at"),
    )

    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
