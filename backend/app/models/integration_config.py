"""ORM models for PM-platform integration configuration and sync tracking.

IntegrationConfig  — one row per (workspace, platform) pair; stores encrypted
                     credentials and JSON target config (project keys, list IDs, …).

ItemIntegrationRef — one row per (action_item, integration); tracks the
                     external ID/URL and sync state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UUIDMixin


class IntegrationConfig(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Stores per-workspace PM integration settings.

    ``credentials_encrypted`` holds the serialised credentials ciphertext
    produced by ``app.utils.encryption.encrypt_credentials``.  It is never
    returned to API clients in plaintext.

    ``target_config`` is a plain JSON dict and may contain fields such as:
        - project_key  (Jira)
        - workspace_gid / project_gid  (Asana)
        - team_id  (Linear)
        - list_id  (ClickUp)
    """

    __tablename__ = "integration_configs"
    __table_args__ = (
        Index("ix_integration_configs_workspace_platform", "workspace_id", "platform"),
        Index("ix_integration_configs_tenant_id", "tenant_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    platform: Mapped[str] = mapped_column(
        Enum("jira", "asana", "linear", "clickup", name="integration_platform"),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )

    # Encrypted credentials blob (ciphertext string, base64-encoded)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Free-form JSON for platform-specific target settings
    target_config: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Back-reference to item refs
    item_refs: Mapped[list[ItemIntegrationRef]] = relationship(
        "ItemIntegrationRef",
        back_populates="integration",
        cascade="all, delete-orphan",
    )


class ItemIntegrationRef(Base, UUIDMixin):
    """Tracks the external representation of an action item on a PM platform.

    One row is created per (item, integration) after a successful push.
    ``sync_status`` reflects the last known sync outcome:

        pending   — queued but not yet pushed
        synced    — successfully pushed; ``external_id`` is valid
        failed    — last push or status-pull attempt failed
        conflict  — external status diverges from local in a way that needs
                     manual resolution
    """

    __tablename__ = "item_integration_refs"
    __table_args__ = (
        Index("ix_item_integration_refs_item_id", "item_id"),
        Index("ix_item_integration_refs_integration_id", "integration_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("action_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_configs.id", ondelete="CASCADE"),
        nullable=False,
    )

    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)

    sync_status: Mapped[str] = mapped_column(
        Enum("pending", "synced", "failed", "conflict", name="item_sync_status"),
        nullable=False,
        default="pending",
    )

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Optional error message captured on failure
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    integration: Mapped[IntegrationConfig] = relationship(
        "IntegrationConfig",
        back_populates="item_refs",
    )
