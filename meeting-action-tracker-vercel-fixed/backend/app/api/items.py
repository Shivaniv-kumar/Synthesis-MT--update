"""Action-items API routes.

GET    /api/items/       — list items with filters (tenant-scoped, paginated)
PATCH  /api/items/{id}   — update item fields
DELETE /api/items/{id}   — delete item
POST   /api/items/bulk   — bulk-save draft items from review screen
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.logger import write_audit_log
from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.auth.rbac import Permission, require_permission
from app.database import get_db
from app.models import ActionItem, Meeting, User
from app.models.project import Project
from app.schemas.action_item import (
    ActionItemOut,
    BulkSaveRequest,
    ItemFilters,
    PaginatedItems,
    UpdateItemRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/items", tags=["items"])

PriorityValue = Literal["High", "Medium", "Low"]
StatusValue = Literal["Open", "In progress", "Done"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_priority(value: object) -> PriorityValue:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return "High"
    if normalized == "low":
        return "Low"
    return "Medium"


def _normalize_status(value: object) -> StatusValue:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"done", "completed", "complete"}:
        return "Done"
    if normalized in {"in_progress", "inprogress", "progress"}:
        return "In progress"
    return "Open"


def _item_to_out(
    item: ActionItem,
    meeting_title: str | None = None,
    project_name: str | None = None,
    reviewed_by_name: str | None = None,
) -> ActionItemOut:
    return ActionItemOut(
        id=item.id,
        meeting_id=item.meeting_id,
        tenant_id=item.tenant_id,
        workspace_id=item.workspace_id,
        task=item.task or "",
        owner_user_id=item.owner_user_id,
        owner_label=item.owner_label or "",
        priority=_normalize_priority(item.priority),
        due_date=item.due_date,
        due_text=item.due_text or "",
        status=_normalize_status(item.status),
        context=item.context or "",
        confidence=float(item.confidence or 0.0),
        needs_review=bool(item.needs_review),
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        meeting_title=meeting_title,
        project_name=project_name,
        reviewed_by_name=str(reviewed_by_name) if reviewed_by_name is not None else None,
    )


async def _get_item_or_404(
    item_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,  # H4: also scope by workspace_id
    db: AsyncSession,
) -> ActionItem:
    result = await db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.tenant_id == tenant_id,
            ActionItem.workspace_id == workspace_id,  # H4: workspace isolation
        )
    )
    item: ActionItem | None = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedItems,
    include_in_schema=False,
)
@router.get(
    "/",
    response_model=PaginatedItems,  # H6: paginated response
    summary="List action items with optional filters",
)
async def list_items(
    filters: ItemFilters = Depends(),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ITEM)),
) -> PaginatedItems:
    effective_workspace_id = ctx.workspace_id
    if filters.meeting_id is not None:
        try:
            meeting_result = await db.execute(
                select(Meeting.workspace_id).where(
                    Meeting.id == filters.meeting_id,
                    Meeting.tenant_id == ctx.tenant_id,
                )
            )
            meeting_workspace_id = meeting_result.scalar_one_or_none()
        except Exception as exc:
            logger.exception(
                "items.workspace_lookup_failed",
                meeting_id=str(filters.meeting_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve items",
            ) from exc
        if meeting_workspace_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
        if meeting_workspace_id != ctx.workspace_id:
            if current_user.role != "Admin":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
            effective_workspace_id = meeting_workspace_id

    base_q = select(ActionItem).where(
        ActionItem.tenant_id == ctx.tenant_id,
        ActionItem.workspace_id == effective_workspace_id,
    )

    if filters.status is not None:
        base_q = base_q.where(ActionItem.status == filters.status)
    if filters.priority is not None:
        base_q = base_q.where(ActionItem.priority == filters.priority)
    if filters.owner_user_id is not None:
        base_q = base_q.where(ActionItem.owner_user_id == filters.owner_user_id)
    if filters.meeting_id is not None:
        base_q = base_q.where(ActionItem.meeting_id == filters.meeting_id)
    if filters.needs_review is not None:
        base_q = base_q.where(ActionItem.needs_review == filters.needs_review)
    if filters.search:
        term = f"%{filters.search.strip()}%"
        base_q = base_q.where(
            or_(
                ActionItem.task.ilike(term),
                ActionItem.owner_label.ilike(term),
                ActionItem.context.ilike(term),
            )
        )
    if filters.project_id is not None:
        base_q = (
            base_q
            .join(Meeting, ActionItem.meeting_id == Meeting.id)
            .where(Meeting.project_id == filters.project_id)
        )

    # H6: total count for pagination metadata
    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total: int = count_result.scalar_one()

    effective_size = filters.effective_size
    offset = (filters.page - 1) * effective_size
    page_q = base_q.order_by(ActionItem.created_at.desc()).offset(offset).limit(effective_size)

    result = await db.execute(page_q)
    items = result.scalars().all()

    # Batch-load meeting titles, project names, and reviewer names (no N+1)
    meeting_title_map: dict[uuid.UUID, str] = {}
    project_name_map: dict[uuid.UUID, str] = {}
    reviewer_name_map: dict[uuid.UUID, str] = {}  # item_id → reviewer display_name

    if items:
        # Meeting titles + project ids
        meeting_ids = {i.meeting_id for i in items}
        m_rows = await db.execute(
            select(Meeting.id, Meeting.title, Meeting.project_id)
            .where(Meeting.id.in_(meeting_ids))
        )
        meetings_data = m_rows.all()
        for m_id, m_title, _ in meetings_data:
            meeting_title_map[m_id] = m_title or "Untitled Meeting"

        # Project names
        project_ids = {m_proj_id for _, _, m_proj_id in meetings_data if m_proj_id}
        if project_ids:
            p_rows = await db.execute(
                select(Project.id, Project.name).where(Project.id.in_(project_ids))
            )
            proj_id_to_name = {row[0]: row[1] for row in p_rows.all()}
            for m_id, _, m_proj_id in meetings_data:
                if m_proj_id and m_proj_id in proj_id_to_name:
                    project_name_map[m_id] = proj_id_to_name[m_proj_id]

        # Reviewer display names
        reviewer_ids = {i.reviewed_by for i in items if i.reviewed_by is not None}
        if reviewer_ids:
            u_rows = await db.execute(
                select(User.id, User.display_name).where(User.id.in_(reviewer_ids))
            )
            user_id_to_name = {row[0]: row[1] or str(row[0]) for row in u_rows.all()}
            for i in items:
                if i.reviewed_by and i.reviewed_by in user_id_to_name:
                    reviewer_name_map[i.id] = user_id_to_name[i.reviewed_by]

    return PaginatedItems(
        items=[
            _item_to_out(
                i,
                meeting_title=meeting_title_map.get(i.meeting_id),
                project_name=project_name_map.get(i.meeting_id),
                reviewed_by_name=reviewer_name_map.get(i.id),
            )
            for i in items
        ],
        total=total,
        page=filters.page,
        size=effective_size,
        has_next=(offset + effective_size) < total,
        has_prev=filters.page > 1,
    )


# ---------------------------------------------------------------------------
# PATCH /{id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{item_id}",
    response_model=ActionItemOut,
    summary="Update action item fields",
)
async def update_item(
    item_id: uuid.UUID,
    body: UpdateItemRequest,
    current_user: User = Depends(require_permission(Permission.EDIT_ITEM)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> ActionItemOut:
    item = await _get_item_or_404(item_id, ctx.tenant_id, ctx.workspace_id, db)

    before_snapshot: dict = {
        "status": item.status,
        "priority": item.priority,
        "owner_user_id": str(item.owner_user_id) if item.owner_user_id else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "task": item.task,
    }

    if body.status is not None:
        item.status = body.status
    if body.owner_label is not None:
        item.owner_label = body.owner_label
    if body.due_text is not None:
        item.due_text = body.due_text
    if body.owner_user_id is not None:
        # M3: verify the new owner belongs to the same tenant
        owner_check = await db.execute(
            select(User).where(
                User.id == body.owner_user_id,
                User.tenant_id == ctx.tenant_id,
            )
        )
        if owner_check.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="owner_user_id does not belong to this tenant.",
            )
        item.owner_user_id = body.owner_user_id
    if body.priority is not None:
        item.priority = body.priority
    if body.due_date is not None:
        item.due_date = body.due_date
    if body.task is not None:
        item.task = body.task
    if body.context is not None:
        item.context = body.context
    if body.needs_review is not None:
        item.needs_review = body.needs_review
        if not body.needs_review:
            # Stamp who cleared the flag and when
            item.reviewed_by = current_user.id
            item.reviewed_at = datetime.now(tz=timezone.utc)
        else:
            # Re-flagged for review — clear the previous reviewer stamp
            item.reviewed_by = None
            item.reviewed_at = None

    item.updated_at = datetime.now(tz=timezone.utc)
    await db.flush([item])

    after_snapshot: dict = {
        "status": item.status,
        "priority": item.priority,
        "owner_user_id": str(item.owner_user_id) if item.owner_user_id else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "task": item.task,
    }

    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="action_item",
        entity_id=item.id,
        action="update",
        before=before_snapshot,
        after=after_snapshot,
    )

    logger.info(
        "items.updated",
        item_id=str(item_id),
        user_id=str(ctx.user_id),
        changes=list(body.model_fields_set),
    )
    return _item_to_out(item)


# ---------------------------------------------------------------------------
# DELETE /{id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an action item",
)
async def delete_item(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.DELETE_ITEM)),  # C4: RBAC
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    item = await _get_item_or_404(item_id, ctx.tenant_id, ctx.workspace_id, db)

    before_snapshot: dict = {
        "task": item.task,
        "status": item.status,
        "meeting_id": str(item.meeting_id),
    }

    await db.delete(item)
    await db.flush()

    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="action_item",
        entity_id=item_id,
        action="delete",
        before=before_snapshot,
    )

    logger.info(
        "items.deleted",
        item_id=str(item_id),
        user_id=str(ctx.user_id),
        tenant_id=str(ctx.tenant_id),
    )


# ---------------------------------------------------------------------------
# POST /bulk
# ---------------------------------------------------------------------------


@router.post(
    "/bulk",
    response_model=list[ActionItemOut],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk-save draft action items from the review screen",
)
async def bulk_save_items(
    body: BulkSaveRequest,
    current_user: User = Depends(require_permission(Permission.EDIT_ITEM)),  # C5: RBAC
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> list[ActionItemOut]:
    """Confirm and persist a batch of draft items.

    Validates that every referenced meeting belongs to the current tenant AND
    workspace (C6).
    """
    # C6: Validate ownership by tenant_id AND workspace_id
    meeting_ids = {draft.meeting_id for draft in body.items}
    result = await db.execute(
        select(Meeting.id).where(
            Meeting.id.in_(meeting_ids),
            Meeting.tenant_id == ctx.tenant_id,
            Meeting.workspace_id == ctx.workspace_id,  # C6: workspace isolation
        )
    )
    valid_ids = {row[0] for row in result.all()}
    invalid = meeting_ids - valid_ids
    if invalid:
        # M5: generic message — don't enumerate valid/invalid IDs (prevents fishing)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more referenced meetings were not found or access was denied.",
        )

    saved: list[ActionItem] = []
    for draft in body.items:
        item = ActionItem(
            id=uuid.uuid4(),
            meeting_id=draft.meeting_id,
            tenant_id=ctx.tenant_id,
            workspace_id=ctx.workspace_id,
            task=draft.task,
            owner_user_id=draft.owner_user_id,
            owner_label=draft.owner_label,
            priority=draft.priority,
            due_date=draft.due_date,
            due_text=draft.due_text,
            status=draft.status,
            context=draft.context,
            confidence=draft.confidence,
            needs_review=draft.needs_review,
            created_by=ctx.user_id,  # H5: set created_by from auth context
        )
        db.add(item)
        saved.append(item)

    await db.flush(saved)

    # Single audit log entry covering the bulk operation
    await write_audit_log(
        db=db,
        actor_user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        entity_type="action_item",
        entity_id=saved[0].id,  # representative item
        action="bulk_create",
        after={"count": len(saved)},
    )

    logger.info(
        "items.bulk_saved",
        count=len(saved),
        user_id=str(ctx.user_id),
        tenant_id=str(ctx.tenant_id),
    )
    return [_item_to_out(i) for i in saved]
