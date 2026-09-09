"""Dashboard API routes — expanded with throughput, owner stats, export, and quality.

Endpoints
---------
GET  /dashboard/summary            Aggregated workspace summary (DashboardSummary schema)
GET  /dashboard/throughput         Daily created/completed counts (?days=30)
GET  /dashboard/overdue            Overdue action items
GET  /dashboard/owner-stats        Per-owner open/done/overdue breakdown
GET  /dashboard/extraction-quality Extraction quality metrics
GET  /dashboard/export/csv         Filtered CSV download
GET  /dashboard/export/pdf         Filtered PDF download
"""

from __future__ import annotations

import io
import uuid
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.auth.rbac import Permission, require_permission
from app.database import get_db
from app.models import ActionItem, Meeting, User
from app.schemas.action_item import ActionItemOut
from app.schemas.dashboard import (
    DashboardSummary,
    ItemsByOwner,
    ItemsByPriority,
    OwnerWorkload,
    StatusCounts,
)
from app.services.export_service import ExportService
from app.services.reporting import (
    DailyThroughput,
    ExtractionQualityMetrics,
    OwnerStat,
    ReportingService,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_reporting = ReportingService()
_export = ExportService()


# ---------------------------------------------------------------------------
# Pydantic response models for the new endpoints
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from datetime import date as _date, datetime as _datetime


class DailyThroughputOut(BaseModel):
    date: _date
    created_count: int
    completed_count: int


class OwnerStatOut(BaseModel):
    owner_label: str
    open_count: int
    done_count: int
    overdue_count: int


class ExtractionQualityOut(BaseModel):
    avg_confidence: float
    review_rate: float
    total_runs: int
    avg_items_per_run: float
    model_usage: dict[str, int]


# ---------------------------------------------------------------------------
# Helper: build item filter WHERE clause
# ---------------------------------------------------------------------------


def _build_item_filters(
    ctx: TenantContext,
    status: Optional[str],
    priority: Optional[str],
    meeting_id: Optional[uuid.UUID],
):
    """Return a list of SQLAlchemy WHERE conditions for the export endpoints."""
    conditions = [
        ActionItem.tenant_id == ctx.tenant_id,
        ActionItem.workspace_id == ctx.workspace_id,
    ]
    if status:
        conditions.append(ActionItem.status == status)
    if priority:
        conditions.append(ActionItem.priority == priority)
    if meeting_id:
        conditions.append(ActionItem.meeting_id == meeting_id)
    return conditions


# ---------------------------------------------------------------------------
# GET /summary
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="Aggregated workspace dashboard summary",
)
async def get_summary(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> DashboardSummary:
    """Return the flat DashboardSummary for the current workspace.

    All queries are scoped to (tenant_id, workspace_id) for full tenant isolation.
    """
    from datetime import date, datetime, timedelta, timezone
    from sqlalchemy import case as sa_case, func

    today = date.today()
    # "This week" = Monday 00:00 UTC through now
    week_start = datetime.now(tz=timezone.utc) - timedelta(days=today.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    base_where = [
        ActionItem.tenant_id == ctx.tenant_id,
        ActionItem.workspace_id == ctx.workspace_id,
    ]

    # ── Status counts ─────────────────────────────────────────────────────────
    status_q = await db.execute(
        select(
            func.count(sa_case((ActionItem.status == "Open", 1))).label("total_open"),
            func.count(sa_case((ActionItem.status == "In progress", 1))).label("total_in_progress"),
            func.count(sa_case((ActionItem.status == "Done", 1))).label("total_done"),
        ).where(*base_where)
    )
    status_row = status_q.one()

    # ── Overdue count ─────────────────────────────────────────────────────────
    overdue_q = await db.execute(
        select(func.count()).where(
            *base_where,
            ActionItem.status != "Done",
            ActionItem.due_date < today,
        )
    )
    overdue_count: int = overdue_q.scalar_one()

    # ── Needs-review count ────────────────────────────────────────────────────
    review_q = await db.execute(
        select(func.count()).where(*base_where, ActionItem.needs_review.is_(True))
    )
    needs_review_count: int = review_q.scalar_one()

    # ── Meetings this week ────────────────────────────────────────────────────
    mtw_q = await db.execute(
        select(func.count()).where(
            Meeting.tenant_id == ctx.tenant_id,
            Meeting.workspace_id == ctx.workspace_id,
            Meeting.created_at >= week_start,
        )
    )
    meetings_this_week: int = mtw_q.scalar_one()

    # ── Items by priority ─────────────────────────────────────────────────────
    priority_q = await db.execute(
        select(
            func.count(sa_case((ActionItem.priority == "High", 1))).label("High"),
            func.count(sa_case((ActionItem.priority == "Medium", 1))).label("Medium"),
            func.count(sa_case((ActionItem.priority == "Low", 1))).label("Low"),
        ).where(*base_where)
    )
    priority_row = priority_q.one()

    # ── Items by owner (top 10 by open count) ─────────────────────────────────
    owner_q = await db.execute(
        select(
            ActionItem.owner_label,
            func.count().label("count"),
        )
        .where(*base_where, ActionItem.status != "Done")
        .group_by(ActionItem.owner_label)
        .order_by(func.count().desc())
        .limit(10)
    )
    items_by_owner = [
        ItemsByOwner(owner_label=row.owner_label or "(unassigned)", count=row.count)
        for row in owner_q.all()
    ]

    return DashboardSummary(
        total_open=status_row.total_open or 0,
        total_in_progress=status_row.total_in_progress or 0,
        total_done=status_row.total_done or 0,
        overdue_count=overdue_count,
        needs_review_count=needs_review_count,
        meetings_this_week=meetings_this_week,
        items_by_priority=ItemsByPriority(
            High=priority_row.High or 0,
            Medium=priority_row.Medium or 0,
            Low=priority_row.Low or 0,
        ),
        items_by_owner=items_by_owner,
    )


# ---------------------------------------------------------------------------
# GET /throughput
# ---------------------------------------------------------------------------


@router.get(
    "/throughput",
    response_model=list[DailyThroughputOut],
    summary="Daily item creation and completion counts",
)
async def get_throughput(
    days: Annotated[int, Query(ge=1, le=365, description="Number of days to look back")] = 30,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> list[DailyThroughputOut]:
    """Return daily created/completed counts for the last *days* calendar days."""
    results = await _reporting.get_throughput(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
        days=days,
    )
    return [
        DailyThroughputOut(
            date=r.date,
            created_count=r.created_count,
            completed_count=r.completed_count,
        )
        for r in results
    ]


# ---------------------------------------------------------------------------
# GET /overdue
# ---------------------------------------------------------------------------


@router.get(
    "/overdue",
    response_model=list[ActionItemOut],
    summary="All overdue action items (due_date < today, status != Done)",
)
async def get_overdue(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> list[ActionItemOut]:
    """Return all non-Done action items past their due date, oldest first."""
    items = await _reporting.get_overdue_items(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
    )
    return [ActionItemOut.model_validate(item) for item in items]


# ---------------------------------------------------------------------------
# GET /owner-stats
# ---------------------------------------------------------------------------


@router.get(
    "/owner-stats",
    response_model=list[OwnerStatOut],
    summary="Per-owner open, done, and overdue item counts",
)
async def get_owner_stats(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> list[OwnerStatOut]:
    """Return open/done/overdue counts grouped by owner label."""
    stats = await _reporting.get_per_owner_stats(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
    )
    return [
        OwnerStatOut(
            owner_label=s.owner_label,
            open_count=s.open_count,
            done_count=s.done_count,
            overdue_count=s.overdue_count,
        )
        for s in stats
    ]


# ---------------------------------------------------------------------------
# GET /extraction-quality
# ---------------------------------------------------------------------------


@router.get(
    "/extraction-quality",
    response_model=ExtractionQualityOut,
    summary="Extraction quality metrics (confidence, review rate, model usage)",
)
async def get_extraction_quality(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.VIEW_DASHBOARD)),
) -> ExtractionQualityOut:
    """Return aggregate extraction quality metrics for the workspace."""
    metrics = await _reporting.get_extraction_quality(
        workspace_id=ctx.workspace_id,
        tenant_id=ctx.tenant_id,
        db=db,
    )
    return ExtractionQualityOut(
        avg_confidence=metrics.avg_confidence,
        review_rate=metrics.review_rate,
        total_runs=metrics.total_runs,
        avg_items_per_run=metrics.avg_items_per_run,
        model_usage=metrics.model_usage,
    )


# ---------------------------------------------------------------------------
# GET /export/csv
# ---------------------------------------------------------------------------


@router.get(
    "/export/csv",
    summary="Download filtered action items as CSV (Excel-compatible UTF-8 BOM)",
)
async def export_csv(
    status: Annotated[
        Optional[str],
        Query(description="Filter by status: Open | In progress | Done"),
    ] = None,
    priority: Annotated[
        Optional[str],
        Query(description="Filter by priority: High | Medium | Low"),
    ] = None,
    meeting_id: Annotated[
        Optional[uuid.UUID],
        Query(description="Filter to a single meeting"),
    ] = None,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.EXPORT_DATA)),
) -> StreamingResponse:
    """Return a CSV file of action items, optionally filtered.

    The file includes a UTF-8 BOM so it opens correctly in Microsoft Excel.
    """
    conditions = _build_item_filters(ctx, status, priority, meeting_id)
    items_result = await db.execute(
        select(ActionItem).where(*conditions).order_by(ActionItem.created_at.asc())
    )
    items = list(items_result.scalars().all())

    # Resolve meeting titles for the items we fetched
    meeting_ids = list({str(item.meeting_id) for item in items})
    meetings_map: dict[str, str] = {}
    if meeting_ids:
        m_result = await db.execute(
            select(Meeting.id, Meeting.title).where(
                Meeting.tenant_id == ctx.tenant_id,
                Meeting.workspace_id == ctx.workspace_id,
                Meeting.id.in_([uuid.UUID(mid) for mid in meeting_ids]),
            )
        )
        meetings_map = {str(row.id): row.title for row in m_result.all()}

    csv_bytes = _export.export_csv(items=items, meetings=meetings_map)

    logger.info(
        "dashboard.export_csv",
        workspace_id=str(ctx.workspace_id),
        item_count=len(items),
        filters={"status": status, "priority": priority, "meeting_id": str(meeting_id)},
    )

    return StreamingResponse(
        content=io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=action_items.csv"},
    )


# ---------------------------------------------------------------------------
# GET /export/pdf
# ---------------------------------------------------------------------------


@router.get(
    "/export/pdf",
    summary="Download filtered action items as PDF",
)
async def export_pdf(
    status: Annotated[
        Optional[str],
        Query(description="Filter by status: Open | In progress | Done"),
    ] = None,
    priority: Annotated[
        Optional[str],
        Query(description="Filter by priority: High | Medium | Low"),
    ] = None,
    meeting_id: Annotated[
        Optional[uuid.UUID],
        Query(description="Filter to a single meeting"),
    ] = None,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.EXPORT_DATA)),
) -> StreamingResponse:
    """Return a formatted PDF of action items.

    High-priority rows are highlighted with a light red background and the
    Priority cell text is rendered in dark red.
    """
    conditions = _build_item_filters(ctx, status, priority, meeting_id)
    items_result = await db.execute(
        select(ActionItem).where(*conditions).order_by(
            ActionItem.priority.asc(),  # High sorts first alphabetically, then Medium, Low
            ActionItem.due_date.asc().nulls_last(),
        )
    )
    items = list(items_result.scalars().all())

    # Build a human-readable title
    parts = ["Action Items"]
    if priority:
        parts.append(f"— {priority} Priority")
    if status:
        parts.append(f"({status})")
    report_title = " ".join(parts)

    pdf_bytes = _export.export_pdf(items=items, title=report_title)

    logger.info(
        "dashboard.export_pdf",
        workspace_id=str(ctx.workspace_id),
        item_count=len(items),
        filters={"status": status, "priority": priority, "meeting_id": str(meeting_id)},
    )

    return StreamingResponse(
        content=io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=action_items.pdf"},
    )
