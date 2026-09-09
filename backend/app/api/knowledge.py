"""Knowledge Base API — CRUD for structured knowledge entries.

GET    /api/knowledge/                  — paginated list with filters
POST   /api/knowledge/                  — manually create an entry
GET    /api/knowledge/{entry_id}        — fetch one entry
PATCH  /api/knowledge/{entry_id}        — edit content / category / source_quote
DELETE /api/knowledge/{entry_id}        — delete an entry
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.database import get_db
from app.models import User
from app.models.knowledge_entry import KnowledgeEntry
from app.models.meeting import Meeting
from app.models.project import Project

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

KnowledgeCategory = Literal[
    "dependency",
    "constraint",
    "decision",
    "blocker",
    "tradeoff",
    "principle",
    "assumption",
    "open_question",
]

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class KnowledgeEntryCreate(BaseModel):
    meeting_id: uuid.UUID
    category: KnowledgeCategory
    content: str = Field(..., min_length=1, max_length=2000)
    source_quote: Optional[str] = Field(default=None, max_length=1000)
    latest_update: Optional[str] = Field(default=None, max_length=2000)
    project_id: Optional[uuid.UUID] = None


class KnowledgeEntryPatch(BaseModel):
    category: Optional[KnowledgeCategory] = None
    content: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    source_quote: Optional[str] = Field(default=None, max_length=1000)
    latest_update: Optional[str] = Field(default=None, max_length=2000)
    is_closed: Optional[bool] = None


class KnowledgeEntryOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    meeting_id: uuid.UUID
    project_id: Optional[uuid.UUID]
    category: str
    content: str
    source_quote: Optional[str]
    latest_update: Optional[str]
    is_closed: bool = False
    created_by: Optional[uuid.UUID]
    edited_by: Optional[uuid.UUID]
    edited_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    meeting_title: Optional[str] = None
    project_name: Optional[str] = None

    model_config = {"from_attributes": True}


class PaginatedKnowledge(BaseModel):
    items: list[KnowledgeEntryOut]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_entry_or_404(
    entry_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    db: AsyncSession,
) -> KnowledgeEntry:
    result = await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.id == entry_id,
            KnowledgeEntry.tenant_id == tenant_id,
            KnowledgeEntry.workspace_id == workspace_id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge entry not found",
        )
    return entry


async def _enrich_with_joined_fields(
    entries: list[KnowledgeEntry],
    db: AsyncSession,
) -> list[KnowledgeEntryOut]:
    """Fetch meeting titles and project names for a batch of entries."""
    if not entries:
        return []

    meeting_ids = {e.meeting_id for e in entries}
    project_ids = {e.project_id for e in entries if e.project_id}

    meeting_titles: dict[uuid.UUID, str] = {}
    project_names: dict[uuid.UUID, str] = {}

    if meeting_ids:
        rows = await db.execute(
            select(Meeting.id, Meeting.title).where(Meeting.id.in_(meeting_ids))
        )
        meeting_titles = {r.id: r.title for r in rows.all()}

    if project_ids:
        rows = await db.execute(
            select(Project.id, Project.name).where(Project.id.in_(project_ids))
        )
        project_names = {r.id: r.name for r in rows.all()}

    result: list[KnowledgeEntryOut] = []
    for e in entries:
        out = KnowledgeEntryOut.model_validate(e)
        out.meeting_title = meeting_titles.get(e.meeting_id)
        out.project_name = project_names.get(e.project_id) if e.project_id else None
        result.append(out)
    return result


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedKnowledge, include_in_schema=False)
@router.get("/", response_model=PaginatedKnowledge, summary="List knowledge entries")
async def list_knowledge(
    project_id: Optional[uuid.UUID] = Query(default=None),
    category: Optional[List[KnowledgeCategory]] = Query(default=None),
    meeting_id: Optional[uuid.UUID] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PaginatedKnowledge:
    base = (
        select(KnowledgeEntry)
        .where(
            KnowledgeEntry.tenant_id == ctx.tenant_id,
            KnowledgeEntry.workspace_id == ctx.workspace_id,
        )
    )
    if project_id is not None:
        base = base.where(KnowledgeEntry.project_id == project_id)
    if category:
        base = base.where(KnowledgeEntry.category.in_(category))
    if meeting_id is not None:
        base = base.where(KnowledgeEntry.meeting_id == meeting_id)
    if search:
        term = f"%{search}%"
        base = base.where(KnowledgeEntry.content.ilike(term))

    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total: int = count_result.scalar_one()

    offset = (page - 1) * page_size
    rows_result = await db.execute(
        base.order_by(KnowledgeEntry.created_at.desc()).offset(offset).limit(page_size)
    )
    entries = list(rows_result.scalars().all())
    items = await _enrich_with_joined_fields(entries, db)

    return PaginatedKnowledge(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=offset + page_size < total,
        has_prev=page > 1,
    )


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=KnowledgeEntryOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
@router.post(
    "/",
    response_model=KnowledgeEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Manually create a knowledge entry",
)
async def create_knowledge_entry(
    body: KnowledgeEntryCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEntryOut:
    # Verify the meeting belongs to this tenant/workspace
    meeting_result = await db.execute(
        select(Meeting).where(
            Meeting.id == body.meeting_id,
            Meeting.tenant_id == ctx.tenant_id,
            Meeting.workspace_id == ctx.workspace_id,
        )
    )
    if meeting_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found"
        )

    entry = KnowledgeEntry(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        meeting_id=body.meeting_id,
        project_id=body.project_id,
        category=body.category,
        content=body.content,
        source_quote=body.source_quote or None,
        latest_update=body.latest_update or None,
        created_by=current_user.id,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    logger.info(
        "knowledge.created",
        entry_id=str(entry.id),
        category=entry.category,
        tenant_id=str(ctx.tenant_id),
    )
    items = await _enrich_with_joined_fields([entry], db)
    return items[0]


# ---------------------------------------------------------------------------
# GET /{entry_id}
# ---------------------------------------------------------------------------


@router.get("/{entry_id}", response_model=KnowledgeEntryOut, summary="Get a knowledge entry")
async def get_knowledge_entry(
    entry_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> KnowledgeEntryOut:
    entry = await _get_entry_or_404(entry_id, ctx.tenant_id, ctx.workspace_id, db)
    items = await _enrich_with_joined_fields([entry], db)
    return items[0]


# ---------------------------------------------------------------------------
# PATCH /{entry_id}
# ---------------------------------------------------------------------------


@router.patch("/{entry_id}", response_model=KnowledgeEntryOut, summary="Update a knowledge entry")
async def update_knowledge_entry(
    entry_id: uuid.UUID,
    body: KnowledgeEntryPatch,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEntryOut:
    entry = await _get_entry_or_404(entry_id, ctx.tenant_id, ctx.workspace_id, db)

    if body.category is not None:
        entry.category = body.category
    if body.content is not None:
        entry.content = body.content
    # Always update nullable fields so the client can clear them by sending null/empty.
    if "source_quote" in body.model_fields_set:
        entry.source_quote = body.source_quote.strip() if body.source_quote else None
    if "latest_update" in body.model_fields_set:
        entry.latest_update = body.latest_update.strip() if body.latest_update else None
    if "is_closed" in body.model_fields_set and body.is_closed is not None:
        entry.is_closed = body.is_closed

    entry.edited_by = current_user.id
    entry.edited_at = datetime.now(tz=timezone.utc)

    await db.flush()
    # Eagerly reload all columns (including server-side updated_at) while still
    # inside the async session. Without this, Pydantic's model_validate triggers
    # a synchronous attribute access on the expired ORM object, which raises
    # MissingGreenlet in SQLAlchemy async.
    await db.refresh(entry)
    logger.info(
        "knowledge.updated",
        entry_id=str(entry_id),
        tenant_id=str(ctx.tenant_id),
    )
    items = await _enrich_with_joined_fields([entry], db)
    return items[0]


# ---------------------------------------------------------------------------
# DELETE /{entry_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a knowledge entry",
)
async def delete_knowledge_entry(
    entry_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    entry = await _get_entry_or_404(entry_id, ctx.tenant_id, ctx.workspace_id, db)
    await db.delete(entry)
    logger.info(
        "knowledge.deleted",
        entry_id=str(entry_id),
        tenant_id=str(ctx.tenant_id),
    )
