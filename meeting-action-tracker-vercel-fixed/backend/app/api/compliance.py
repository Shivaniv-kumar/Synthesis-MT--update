"""Compliance API router.

Prefix: /compliance

All endpoints are gated by RBAC — admin-only unless the endpoint explicitly
allows a user to access their own data (e.g. GDPR export).

Endpoints
---------
GET  /compliance/audit-log
     Paginated query over AuditLog rows with optional filters.

POST /compliance/gdpr/erasure
     Trigger a GDPR right-to-erasure for a data subject.

GET  /compliance/gdpr/export/{user_id}
     Download a JSON bundle of all personal data we hold for a user.
     Admin users can export any user; non-admin users can export only themselves.

GET  /compliance/retention-config
     Retrieve the retention policy for the current workspace.

PATCH /compliance/retention-config
     Update the retention policy for the current workspace.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.auth.rbac import Permission, require_permission
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.gdpr import DataRetentionConfig, GDPRAuditEvent
from app.models.user import User
from app.services.retention import RetentionService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/compliance", tags=["compliance"])

_retention_service = RetentionService()

# ---------------------------------------------------------------------------
# Pydantic schemas (inline — small enough to keep in one file)
# ---------------------------------------------------------------------------

_MINIMUM_RETENTION_DAYS = 30


class AuditLogOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_user_id: Optional[uuid.UUID]
    entity_type: str
    entity_id: uuid.UUID
    action: str
    before_data: Optional[Any] = None
    after_data: Optional[Any] = None
    created_at: Any  # datetime — serialised as ISO string

    model_config = {"from_attributes": True}


class PaginatedAuditLog(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    size: int
    pages: int


class ErasureRequest(BaseModel):
    subject_user_id: uuid.UUID = Field(..., description="UUID of the user to erase.")
    reason: str = Field(..., min_length=1, description="Reason for the erasure request.")


class ErasureResponse(BaseModel):
    message: str
    estimated_completion: str


class RetentionConfigOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    transcript_retention_days: int
    item_retention_days: int
    audio_retention_days: int
    updated_by: Optional[uuid.UUID] = None
    updated_at: Optional[Any] = None

    model_config = {"from_attributes": True}


class RetentionConfigPatch(BaseModel):
    transcript_retention_days: Optional[int] = Field(
        None, ge=_MINIMUM_RETENTION_DAYS, description="Days to retain transcripts."
    )
    item_retention_days: Optional[int] = Field(
        None, ge=_MINIMUM_RETENTION_DAYS, description="Days to retain action items."
    )
    audio_retention_days: Optional[int] = Field(
        None, ge=_MINIMUM_RETENTION_DAYS, description="Days to retain audio files."
    )

    @validator("transcript_retention_days", "item_retention_days", "audio_retention_days", pre=True)
    @classmethod
    def _validate_min_days(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < _MINIMUM_RETENTION_DAYS:
            raise ValueError(
                f"Retention period must be at least {_MINIMUM_RETENTION_DAYS} days."
            )
        return v


# ---------------------------------------------------------------------------
# Helper — load or create default DataRetentionConfig
# ---------------------------------------------------------------------------


async def _get_or_create_retention_config(
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> DataRetentionConfig:
    """Return the DataRetentionConfig for a workspace, creating defaults if absent."""
    result = await db.execute(
        select(DataRetentionConfig).where(
            DataRetentionConfig.workspace_id == workspace_id,
            DataRetentionConfig.tenant_id == tenant_id,
        )
    )
    config: Optional[DataRetentionConfig] = result.scalar_one_or_none()

    if config is None:
        config = DataRetentionConfig(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            transcript_retention_days=365,
            item_retention_days=730,
            audio_retention_days=90,
        )
        db.add(config)
        await db.flush()
        logger.info(
            "compliance.retention_config.defaults_created",
            workspace_id=str(workspace_id),
        )

    return config


# ---------------------------------------------------------------------------
# GET /audit-log
# ---------------------------------------------------------------------------


@router.get(
    "/audit-log",
    response_model=PaginatedAuditLog,
    summary="List audit log entries (admin only)",
)
async def list_audit_log(
    entity_type: Optional[str] = Query(None, description="Filter by entity type."),
    action: Optional[str] = Query(None, description="Filter by action (create/update/delete/view)."),
    since: Optional[date] = Query(None, description="Return entries on or after this date (UTC)."),
    until: Optional[date] = Query(None, description="Return entries on or before this date (UTC)."),
    page: int = Query(1, ge=1, description="Page number (1-indexed)."),
    size: int = Query(50, ge=1, le=500, description="Results per page."),
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> PaginatedAuditLog:
    """Return paginated ``AuditLog`` rows for the current tenant, newest first.

    All filter parameters are optional.  Results are tenant-scoped so admins
    can only see events within their own tenant.
    """
    stmt = select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id)

    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if since:
        from datetime import datetime, timezone
        stmt = stmt.where(
            AuditLog.created_at >= datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        )
    if until:
        from datetime import datetime, timezone
        stmt = stmt.where(
            AuditLog.created_at
            < datetime(until.year, until.month, until.day + 1, tzinfo=timezone.utc)
        )

    # Count total before pagination
    count_stmt = select(AuditLog.id).where(AuditLog.tenant_id == ctx.tenant_id)
    if entity_type:
        count_stmt = count_stmt.where(AuditLog.entity_type == entity_type)
    if action:
        count_stmt = count_stmt.where(AuditLog.action == action)

    count_result = await db.execute(count_stmt)
    total = len(count_result.all())

    # Paginate
    offset = (page - 1) * size
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(size)
    rows_result = await db.execute(stmt)
    rows: list[AuditLog] = list(rows_result.scalars().all())

    import math

    return PaginatedAuditLog(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        size=size,
        pages=max(1, math.ceil(total / size)),
    )


# ---------------------------------------------------------------------------
# POST /gdpr/erasure
# ---------------------------------------------------------------------------


@router.post(
    "/gdpr/erasure",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ErasureResponse,
    summary="Trigger GDPR right-to-erasure (admin only)",
)
async def gdpr_erasure(
    body: ErasureRequest,
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> ErasureResponse:
    """Anonymise and purge all personal data for a data subject.

    The erasure is processed synchronously within this request because the
    GDPR regulation requires that the erasure be completed within 30 days,
    and processing it immediately keeps the implementation simple and auditable.

    A ``GDPRAuditEvent`` row is written recording the actor and subject IDs
    along with the stated reason.  The estimated completion time of
    "Within 72 hours" refers to any downstream propagation (caches, backups,
    derived datasets) that a production deployment would also need to handle.
    """
    logger.info(
        "compliance.gdpr.erasure_requested",
        actor_user_id=str(current_user.id),
        subject_user_id=str(body.subject_user_id),
        reason=body.reason,
    )

    try:
        await _retention_service.process_erasure_request(
            subject_user_id=body.subject_user_id,
            actor_user_id=current_user.id,
            tenant_id=ctx.tenant_id,
            db=db,
        )
    except Exception as exc:
        logger.exception(
            "compliance.gdpr.erasure_failed",
            subject_user_id=str(body.subject_user_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erasure processing failed. Please contact support.",
        ) from exc

    logger.info(
        "compliance.gdpr.erasure_complete",
        actor_user_id=str(current_user.id),
        subject_user_id=str(body.subject_user_id),
    )

    return ErasureResponse(
        message="Erasure request queued",
        estimated_completion="Within 72 hours",
    )


# ---------------------------------------------------------------------------
# GET /gdpr/export/{user_id}
# ---------------------------------------------------------------------------


@router.get(
    "/gdpr/export/{user_id}",
    summary="Download all personal data for a user (admin or self)",
)
async def gdpr_export(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return a JSON file containing all personal data held for *user_id*.

    Access rules
    ------------
    * Admin users (role == "Admin") may export data for any user in their tenant.
    * Non-admin users may only export their own data (``user_id == current_user.id``).

    The response has ``Content-Disposition: attachment`` so browsers will
    prompt a download dialog.
    """
    # Authorisation: admin can export anyone; non-admin only self
    is_admin = getattr(current_user, "role", None) == "Admin"
    is_self = current_user.id == user_id

    if not is_admin and not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You may only export your own personal data.",
        )

    logger.info(
        "compliance.gdpr.export_requested",
        requester_id=str(current_user.id),
        subject_user_id=str(user_id),
    )

    try:
        data = await _retention_service.export_user_data(
            subject_user_id=user_id,
            tenant_id=ctx.tenant_id,
            db=db,
        )
    except Exception as exc:
        logger.exception(
            "compliance.gdpr.export_failed",
            subject_user_id=str(user_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Export failed. Please try again or contact support.",
        ) from exc

    # Serialise to JSON bytes with a readable indent
    json_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")

    return Response(
        content=json_bytes,
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=user_data_export.json",
            "Content-Length": str(len(json_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# GET /retention-config
# ---------------------------------------------------------------------------


@router.get(
    "/retention-config",
    response_model=RetentionConfigOut,
    summary="Get workspace retention policy (admin only)",
)
async def get_retention_config(
    current_user: User = Depends(require_permission(Permission.CONFIGURE_RETENTION)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> RetentionConfigOut:
    """Return the data retention policy for the current workspace.

    If no policy has been explicitly configured, default values are stored and
    returned (365 / 730 / 90 days).
    """
    config = await _get_or_create_retention_config(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
    )
    return RetentionConfigOut.model_validate(config)


# ---------------------------------------------------------------------------
# PATCH /retention-config
# ---------------------------------------------------------------------------


@router.patch(
    "/retention-config",
    response_model=RetentionConfigOut,
    summary="Update workspace retention policy (admin only)",
)
async def patch_retention_config(
    body: RetentionConfigPatch,
    current_user: User = Depends(require_permission(Permission.CONFIGURE_RETENTION)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> RetentionConfigOut:
    """Update the data retention policy for the current workspace.

    Validation rules
    ----------------
    All provided values must be >= 30 days (configurable via ``_MINIMUM_RETENTION_DAYS``).
    Omitted fields are unchanged.

    The ``updated_by`` field is set to the calling admin's user ID.
    """
    config = await _get_or_create_retention_config(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
    )

    changed = False

    if body.transcript_retention_days is not None:
        if body.transcript_retention_days < _MINIMUM_RETENTION_DAYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"transcript_retention_days must be >= {_MINIMUM_RETENTION_DAYS}.",
            )
        config.transcript_retention_days = body.transcript_retention_days
        changed = True

    if body.item_retention_days is not None:
        if body.item_retention_days < _MINIMUM_RETENTION_DAYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"item_retention_days must be >= {_MINIMUM_RETENTION_DAYS}.",
            )
        config.item_retention_days = body.item_retention_days
        changed = True

    if body.audio_retention_days is not None:
        if body.audio_retention_days < _MINIMUM_RETENTION_DAYS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"audio_retention_days must be >= {_MINIMUM_RETENTION_DAYS}.",
            )
        config.audio_retention_days = body.audio_retention_days
        changed = True

    if changed:
        config.updated_by = current_user.id
        await db.flush()
        # Refresh the object so server-side expressions (updated_at onupdate)
        # are loaded back; without this async SQLAlchemy triggers a lazy load
        # that raises MissingGreenlet in an async context.
        await db.refresh(config)

        logger.info(
            "compliance.retention_config.updated",
            workspace_id=str(ctx.workspace_id),
            updated_by=str(current_user.id),
            transcript_days=config.transcript_retention_days,
            item_days=config.item_retention_days,
            audio_days=config.audio_retention_days,
        )

    return RetentionConfigOut.model_validate(config)
