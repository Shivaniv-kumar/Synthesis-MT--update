"""SQLAlchemy ORM models package.

Import order matters: Base must be imported before any model that references
another. Alembic's env.py does ``import app.models`` as a side-effect import
so that Base.metadata is fully populated before autogenerate / migration runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.models.project import Project
from app.models.meeting import Meeting
from app.models.action_item import ActionItem
from app.models.audit_log import AuditLog
from app.models.extraction_run import ExtractionRun

# ---------------------------------------------------------------------------
# Platform / integration models (added by platform + PM-integration agents)
# ---------------------------------------------------------------------------

from app.models.platform_config import PlatformConfig, PlatformType
from app.models.integration_config import IntegrationConfig, ItemIntegrationRef

# ---------------------------------------------------------------------------
# Notification models (added by notifications agent)
# ---------------------------------------------------------------------------

from app.models.notification import NotificationRule, NotificationLog

# ---------------------------------------------------------------------------
# GDPR / compliance models (added by compliance agent)
# ---------------------------------------------------------------------------

from app.models.gdpr import GDPRAuditEvent, DataRetentionConfig

# ---------------------------------------------------------------------------
# MFA model
# ---------------------------------------------------------------------------

from app.models.user_mfa import UserMFA

# ---------------------------------------------------------------------------
# Item comments model
# ---------------------------------------------------------------------------

from app.models.item_comment import ItemComment

# ---------------------------------------------------------------------------
# User project access model
# ---------------------------------------------------------------------------

from app.models.user_project_access import UserProjectAccess

# ---------------------------------------------------------------------------
# Knowledge base model
# ---------------------------------------------------------------------------

from app.models.knowledge_entry import KnowledgeEntry

# ---------------------------------------------------------------------------
# Security models
# ---------------------------------------------------------------------------

from app.models.revoked_token import RevokedToken
from app.models.password_reset_token import PasswordResetToken


# ---------------------------------------------------------------------------
# Tenant model — defined here to avoid creating a separate file.
# Fixes the conftest.py import gap (``from app.models import Tenant``).
# ---------------------------------------------------------------------------


class Tenant(Base, UUIDMixin, TimestampMixin):
    """Top-level tenant (organisation) that owns one or more workspaces.

    The ``plan`` column stores the billing tier (e.g. ``"free"``, ``"pro"``,
    ``"enterprise"``).  ``is_active`` allows soft-disabling an entire tenant
    without deleting their data.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    plan: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="free",
        server_default=sa.text("'free'"),
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Core
    "User",
    "Workspace",
    "WorkspaceMember",
    "Project",
    "Meeting",
    "ActionItem",
    "AuditLog",
    "ExtractionRun",
    # Platform / integration
    "PlatformConfig",
    "PlatformType",
    "IntegrationConfig",
    "ItemIntegrationRef",
    # Notifications
    "NotificationRule",
    "NotificationLog",
    # GDPR / compliance
    "GDPRAuditEvent",
    "DataRetentionConfig",
    # Tenant
    "Tenant",
    # MFA
    "UserMFA",
    # Item comments
    "ItemComment",
    # User project access
    "UserProjectAccess",
    # Knowledge base
    "KnowledgeEntry",
    # Security
    "RevokedToken",
    "PasswordResetToken",
]
