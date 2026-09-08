"""Item comments API.

GET    /items/{item_id}/comments                 — list comments (newest first)
POST   /items/{item_id}/comments                 — add a comment
DELETE /items/{item_id}/comments/{comment_id}    — delete own comment (Admin can delete any)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, get_tenant_context, TenantContext
from app.auth.rbac import Permission, require_permission
from app.database import get_db
from app.models import ActionItem, User
from app.models.item_comment import ItemComment

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/items", tags=["comments"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CommentOut(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    user_id: uuid.UUID | None
    user_display_name: str
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_item_or_404(
    item_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> ActionItem:
    result = await db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.tenant_id == tenant_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return item


# ---------------------------------------------------------------------------
# GET /items/{item_id}/comments
# ---------------------------------------------------------------------------


@router.get(
    "/{item_id}/comments",
    response_model=list[CommentOut],
    summary="List all comments for an action item (newest first)",
)
async def list_comments(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_ITEM)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[ItemComment]:
    await _get_item_or_404(item_id, ctx.tenant_id, db)

    result = await db.execute(
        select(ItemComment)
        .where(
            ItemComment.item_id == item_id,
            ItemComment.tenant_id == ctx.tenant_id,
        )
        .order_by(ItemComment.created_at.desc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# POST /items/{item_id}/comments
# ---------------------------------------------------------------------------


@router.post(
    "/{item_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment or progress update to an action item",
)
async def add_comment(
    item_id: uuid.UUID,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ITEM)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> ItemComment:
    await _get_item_or_404(item_id, ctx.tenant_id, db)

    now = datetime.now(tz=timezone.utc)
    comment = ItemComment(
        id=uuid.uuid4(),
        item_id=item_id,
        tenant_id=ctx.tenant_id,
        user_id=current_user.id,
        user_display_name=current_user.display_name,
        content=body.content.strip(),
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    await db.flush()

    logger.info(
        "comment.created",
        comment_id=str(comment.id),
        item_id=str(item_id),
        user_id=str(current_user.id),
    )
    return comment


# ---------------------------------------------------------------------------
# DELETE /items/{item_id}/comments/{comment_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{item_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment (own comments; Admins can delete any)",
)
async def delete_comment(
    item_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.VIEW_ITEM)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    result = await db.execute(
        select(ItemComment).where(
            ItemComment.id == comment_id,
            ItemComment.item_id == item_id,
            ItemComment.tenant_id == ctx.tenant_id,
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    # Only the comment author or an Admin may delete
    if comment.user_id != current_user.id and current_user.role != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments.",
        )

    await db.delete(comment)
    await db.flush()

    logger.info(
        "comment.deleted",
        comment_id=str(comment_id),
        item_id=str(item_id),
        deleted_by=str(current_user.id),
    )
