"""GDPR and data retention SQLAlchemy ORM models.

Two tables:
- GDPRAuditEvent  : immutable record of every GDPR-related event (erasure, export,
                    access, consent revocation). Written by RetentionService and the
                    compliance API handler.
- DataRetentionConfig : per-workspace configuration for how long different data
                        categories are retained before automated deletion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import DateTime, Enum, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, UUIDMixin


# ---------------------------------------------------------------------------
# Enum for GDPR event types
# ---------------------------------------------------------------------------

GDPREventTypeEnum = Enum(
    "erasure",
    "export",
    "access",
    "consent_revoked",
    name="gdpr_event_type",
)


# ---------------------------------------------------------------------------
# GDPRAuditEvent
# ---------------------------------------------------------------------------


class GDPRAuditEvent(Base, UUIDMixin):
    """Immutable audit record of every GDPR-relevant event.

    This table is append-only — rows are never updated or deleted.
    It provides a tamper-evident trail for regulatory compliance.

    ``actor_user_id`` is NULL for system-triggered events (e.g. scheduled
    retention jobs). For API-triggered events it is the admin who invoked
    the action.

    ``subject_user_id`` is the data subject whose data was affected.
    """

    __tablename__ = "gdpr_audit_events"
    __table_args__ = (
        sa.Index("ix_gdpr_audit_events_tenant_id", "tenant_id"),
        sa.Index("ix_gdpr_audit_events_subject_user_id", "subject_user_id"),
        sa.Index("ix_gdpr_audit_events_event_type", "event_type"),
        sa.Index("ix_gdpr_audit_events_created_at", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="Owning tenant — used for tenant-scoped queries.",
    )
    event_type: Mapped[str] = mapped_column(
        GDPREventTypeEnum,
        nullable=False,
        comment="erasure | export | access | consent_revoked",
    )
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Admin who triggered the event; NULL for automated/system runs.",
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="The data subject whose data was acted upon.",
    )
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "Freeform JSON payload with event-specific metadata. "
            "e.g. {'retention_applied': 12} or {'reason': 'User request'}"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Wall-clock time when the event was recorded (UTC, set by DB server).",
    )


# ---------------------------------------------------------------------------
# DataRetentionConfig
# ---------------------------------------------------------------------------


class DataRetentionConfig(Base, UUIDMixin, TenantMixin):
    """Per-workspace data retention policy configuration.

    Retention days control how long each data category is kept before the
    automated retention job deletes or anonymises it:

    * ``transcript_retention_days`` — days before transcript text is cleared
      and any transcript S3 object deleted.
    * ``item_retention_days`` — days before action items on old meetings are
      deleted.
    * ``audio_retention_days`` — days before audio S3 objects are deleted.
      This is usually shorter than transcript retention because audio files
      are larger and contain raw voice data.

    A workspace that has no row in this table uses the application defaults
    defined in ``RetentionService``:  365 / 730 / 90 days.
    """

    __tablename__ = "data_retention_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_data_retention_configs_workspace_id"),
        sa.Index("ix_data_retention_configs_tenant_id", "tenant_id"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        comment="The workspace this policy applies to.",
    )
    transcript_retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=365,
        comment="Days to keep transcript text / transcript S3 objects. Default: 365.",
    )
    item_retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=730,
        comment="Days to keep action items on old meetings. Default: 730 (2 years).",
    )
    audio_retention_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=90,
        comment="Days to keep audio S3 objects. Default: 90.",
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Admin user who last modified this config.",
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
        comment="Timestamp of the last update (NULL on initial creation).",
    )
