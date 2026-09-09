"""PlatformConfig ORM model.

Stores per-workspace configuration for each connected meeting platform
(Zoom, Teams, Google Meet).  OAuth credentials are stored encrypted via
``app.utils.encryption``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin


# ---------------------------------------------------------------------------
# Platform enum
# ---------------------------------------------------------------------------


class PlatformType(str, enum.Enum):
    """Supported meeting platform integrations."""

    zoom = "zoom"
    teams = "teams"
    google_meet = "google_meet"


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class PlatformConfig(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Configuration and credentials for a workspace's meeting platform integration.

    One row per (workspace, platform) pair.  A workspace may connect to multiple
    platforms, each with its own PlatformConfig row.

    Attributes
    ----------
    workspace_id:
        UUID of the owning workspace.
    platform:
        The meeting platform: zoom | teams | google_meet.
    is_active:
        Whether the integration is currently active.
    credentials_encrypted:
        Fernet-encrypted JSON blob containing OAuth tokens and app credentials.
        Use ``app.utils.encryption.encrypt_credentials`` /
        ``decrypt_credentials`` to read/write this field.
    last_sync_at:
        UTC timestamp of the last successful recording sync, or None if
        no sync has been performed yet.
    sync_enabled:
        Whether automatic background sync is enabled for this config.
    """

    __tablename__ = "platform_configs"
    __table_args__ = (
        sa.Index("ix_platform_configs_workspace_id", "workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "platform", name="uq_platform_configs_workspace_platform"
        ),
        sa.Index("ix_platform_configs_tenant_id", "tenant_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    platform: Mapped[PlatformType] = mapped_column(
        Enum(PlatformType, name="platform_type"),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )

    credentials_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    last_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    sync_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )
