"""Meetings API routes.

POST   /api/meetings/           — create a new meeting
GET    /api/meetings/           — list meetings (paginated, filterable)
GET    /api/meetings/{id}       — get a single meeting (tenant-scoped)
POST   /api/meetings/{id}/extract — trigger async extraction
DELETE /api/meetings/{id}       — delete meeting + items + transcript + audit log
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import write_audit_log
from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.auth.rbac import Permission, require_permission
from app.config import get_settings
from app.database import get_db
from app.models import ActionItem, Meeting, User
from app.schemas.meeting import (
    CreateMeetingRequest,
    ExtractRequest,
    MeetingOut,
    PaginatedMeetings,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/meetings", tags=["meetings"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meeting_to_out(meeting: Meeting, item_count: int = 0) -> MeetingOut:
    return MeetingOut(
        id=meeting.id,
        tenant_id=meeting.tenant_id,
        workspace_id=meeting.workspace_id,
        title=meeting.title,
        source_type=meeting.source_type,  # type: ignore[arg-type]
        occurred_at=meeting.occurred_at,
        attendees=meeting.attendees or [],
        transcript_ref=meeting.transcript_ref,
        status=meeting.status,  # type: ignore[arg-type]
        created_by=meeting.created_by,
        created_at=meeting.created_at,
        item_count=item_count,
        project_id=meeting.project_id,
    )


async def _get_meeting_or_404(
    meeting_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,  # H3: also scope by workspace_id
    db: AsyncSession,
    *,
    allow_tenant_admin_fallback: bool = False,
) -> Meeting:
    """Load a meeting by ID; raise 404 (not 403) if absent or wrong tenant/workspace."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.tenant_id == tenant_id,
            Meeting.workspace_id == workspace_id,  # H3: workspace isolation
        )
    )
    meeting: Meeting | None = result.scalar_one_or_none()
    if meeting is None and allow_tenant_admin_fallback:
        fallback_result = await db.execute(
            select(Meeting).where(
                Meeting.id == meeting_id,
                Meeting.tenant_id == tenant_id,
            )
        )
        meeting = fallback_result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    return meeting


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=MeetingOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post(
    "/",
    response_model=MeetingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new meeting",
)
async def create_meeting(
    body: CreateMeetingRequest,
    current_user: User = Depends(require_permission(Permission.CREATE_MEETING)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MeetingOut:
    """Persist a new meeting record.

    For ``paste`` source type, an optional ``transcript_text`` is stored
    directly in the ``transcript_text`` column.  Audio and integration sources
    should use pre-signed upload URLs and pass the resulting S3 key as
    ``transcript_ref``.
    """
    if body.project_id is not None:
        from app.models.project import Project as _Project
        proj_result = await db.execute(
            select(_Project).where(
                _Project.id == body.project_id,
                _Project.tenant_id == ctx.tenant_id,
                _Project.workspace_id == ctx.workspace_id,
            )
        )
        if proj_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project not found")

    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        created_by=ctx.user_id,
        title=body.title,
        source_type=body.source_type,
        occurred_at=body.occurred_at,
        attendees=body.attendees or [],
        transcript_text=body.transcript_text,  # M4: inline text → transcript_text, not transcript_ref
        status="draft",
        project_id=body.project_id,
    )
    db.add(meeting)
    await db.flush([meeting])

    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="meeting",
        entity_id=meeting.id,
        action="create",
        after={
            "title": meeting.title,
            "source_type": meeting.source_type,
            "status": meeting.status,
        },
    )

    logger.info(
        "meetings.created",
        meeting_id=str(meeting.id),
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
    )
    return _meeting_to_out(meeting, item_count=0)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedMeetings,
    include_in_schema=False,
)
@router.get(
    "/",
    response_model=PaginatedMeetings,
    summary="List meetings for the current workspace",
)
async def list_meetings(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_ITEM)),
) -> PaginatedMeetings:
    base_q = select(Meeting).where(
        Meeting.tenant_id == ctx.tenant_id,
        Meeting.workspace_id == ctx.workspace_id,
    )
    if status_filter:
        base_q = base_q.where(Meeting.status == status_filter)

    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total: int = count_result.scalar_one()

    offset = (page - 1) * size
    rows_result = await db.execute(
        base_q.order_by(Meeting.created_at.desc()).offset(offset).limit(size)
    )
    meetings = rows_result.scalars().all()

    # Fetch item counts for all returned meetings in one query
    if meetings:
        meeting_ids = [m.id for m in meetings]
        count_q = (
            select(ActionItem.meeting_id, func.count().label("cnt"))
            .where(ActionItem.meeting_id.in_(meeting_ids))
            .group_by(ActionItem.meeting_id)
        )
        count_rows = (await db.execute(count_q)).all()
        counts = {row[0]: row[1] for row in count_rows}
    else:
        counts = {}

    items = [_meeting_to_out(m, counts.get(m.id, 0)) for m in meetings]
    return PaginatedMeetings(items=items, total=total, page=page, size=size)


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------


@router.get(
    "/{meeting_id}",
    response_model=MeetingOut,
    summary="Get a single meeting",
)
async def get_meeting(
    meeting_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MeetingOut:
    meeting = await _get_meeting_or_404(
        meeting_id,
        ctx.tenant_id,
        ctx.workspace_id,
        db,
        allow_tenant_admin_fallback=current_user.role == "Admin",
    )

    count_result = await db.execute(
        select(func.count()).where(ActionItem.meeting_id == meeting.id)
    )
    item_count: int = count_result.scalar_one()
    return _meeting_to_out(meeting, item_count)


# ---------------------------------------------------------------------------
# POST /{id}/extract
# ---------------------------------------------------------------------------


@router.post(
    "/{meeting_id}/extract",
    summary="Trigger action-item extraction for a meeting",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_extraction(
    meeting_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    _body: ExtractRequest = None,  # empty body is optional
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    meeting = await _get_meeting_or_404(meeting_id, ctx.tenant_id, ctx.workspace_id, db)

    if meeting.status == "extracting":
        return {"status": "extracting", "meeting_id": str(meeting.id)}

    # Persist transcript text before extraction (M4 fix: use transcript_text, not transcript_ref)
    meeting.status = "extracting"
    await db.flush([meeting])

    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="meeting",
        entity_id=meeting.id,
        action="extract_triggered",
        before={"status": "draft"},
        after={"status": "extracting"},
    )

    # Commit now so the background task's fresh session sees "extracting" status
    await db.commit()

    settings = get_settings()
    celery_enqueued = False

    if settings.environment != "development":
        # Staging/production: attempt Celery first
        try:
            from app.workers.extraction_worker import extract_meeting_task  # noqa: PLC0415
            extract_meeting_task.delay(str(meeting.id))
            celery_enqueued = True
            logger.info(
                "meetings.extraction_enqueued",
                meeting_id=str(meeting.id),
                tenant_id=str(ctx.tenant_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "meetings.extraction_celery_unavailable",
                error=str(exc),
                meeting_id=str(meeting.id),
            )

    if not celery_enqueued:
        # Dev mode or Celery unavailable — run inline after the response is sent
        from app.workers.extraction_worker import run_extraction_inline  # noqa: PLC0415
        background_tasks.add_task(run_extraction_inline, str(meeting.id))
        logger.info(
            "meetings.inline_extraction_scheduled",
            meeting_id=str(meeting.id),
        )

    return {"status": "extracting", "meeting_id": str(meeting.id)}


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a meeting with all its action items",
)
async def delete_meeting(
    meeting_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.DELETE_MEETING)),  # C4: RBAC
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    meeting = await _get_meeting_or_404(meeting_id, ctx.tenant_id, ctx.workspace_id, db)

    before_snapshot = {
        "title": meeting.title,
        "status": meeting.status,
        "source_type": meeting.source_type,
    }

    # Delete child action items first (cascade handles FK, but explicit is cleaner)
    await db.execute(delete(ActionItem).where(ActionItem.meeting_id == meeting.id))

    # If transcript was stored in S3, schedule deletion (best-effort)
    if meeting.transcript_ref and meeting.source_type in ("audio", "file"):
        try:
            from app.services.storage import delete_object  # noqa: PLC0415

            await delete_object(meeting.transcript_ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "meetings.transcript_delete_failed",
                meeting_id=str(meeting.id),
                error=str(exc),
            )

    await db.delete(meeting)
    await db.flush()

    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="meeting",
        entity_id=meeting_id,
        action="delete",
        before=before_snapshot,
    )

    logger.info(
        "meetings.deleted",
        meeting_id=str(meeting_id),
        user_id=str(ctx.user_id),
        tenant_id=str(ctx.tenant_id),
    )
