"""Audit log writer.

Every mutating operation (create/update/delete) should call
``write_audit_log`` so that we have a tamper-evident trail stored in the
``audit_logs`` table.

The function is intentionally fire-and-forget within a single DB session:
it catches *all* exceptions, logs them via structlog, and returns without
re-raising so that a logging failure never aborts the main operation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def write_audit_log(
    db: AsyncSession,
    actor_user_id: Optional[uuid.UUID],
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Insert an ``AuditLog`` row.

    Never raises — any exception is caught, logged, and swallowed so that
    audit-logging failures do not abort the calling business operation.
    """
    try:
        # Import here to avoid circular import at module level
        from app.models.audit_log import AuditLog  # noqa: PLC0415

        entry = AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_data=before,
            after_data=after,
        )
        db.add(entry)
        # Flush within the same transaction; commit is owned by the caller /
        # the get_db dependency.
        await db.flush([entry])
        logger.debug(
            "audit.written",
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "audit.write_failed",
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            error=str(exc),
        )
