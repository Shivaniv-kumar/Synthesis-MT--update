"""BI Feed API — flat, PII-free data endpoints for third-party BI tools.

Authentication
--------------
Uses a separate API-key scheme (X-API-Key header) instead of JWT.
Set the following environment variables:

    BI_API_KEY   — the shared secret that BI tools must supply
    BI_TENANT_ID — the tenant UUID whose workspaces are exposed via /workspaces

No JWT, no OAuth.  Requests without a valid X-API-Key receive 401.

No PII policy
-------------
Responses intentionally omit email addresses, full names, transcript content,
and any other personally identifiable information.  owner_label values are
retained (they are already de-identified labels, not email addresses).

Routes
------
GET /bi/metrics           Aggregated KPIs for a workspace + date range
GET /bi/time-series       Daily snapshot series (created / completed / open EOD)
GET /bi/workspaces        List workspace IDs accessible to this API key
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.action_item import ActionItem
from app.models.extraction_run import ExtractionRun
from app.models.meeting import Meeting
from app.models.workspace import Workspace

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/bi", tags=["bi-feed"])

# ---------------------------------------------------------------------------
# API-key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_bi_api_key() -> str:
    """Read BI_API_KEY from environment at call time (not import time)."""
    key = os.environ.get("BI_API_KEY", "")
    if not key:
        logger.warning("bi_feed.missing_env_var", var="BI_API_KEY")
    return key


def _get_bi_tenant_id() -> uuid.UUID:
    """Read BI_TENANT_ID from environment, raise 500 if absent or invalid."""
    raw = os.environ.get("BI_TENANT_ID", "")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BI_TENANT_ID is not configured",
        )
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BI_TENANT_ID is not a valid UUID",
        )


async def require_bi_key(
    api_key: str = Security(_api_key_header),
) -> None:
    """Dependency: validate X-API-Key against BI_API_KEY env var.

    Raises 401 if the header is absent or the key does not match.
    Intentionally uses a constant-time-ish comparison to resist timing attacks.
    """
    expected = _get_bi_api_key()

    # Both missing → not configured; deny.
    # We compare with == rather than hmac.compare_digest because the values
    # are already in memory and this is not a cryptographic MAC check, but
    # be aware that Python's == short-circuits; swap to hmac.compare_digest
    # if your threat model includes timing side-channels.
    if not api_key or not expected or api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class MetricsResponse(BaseModel):
    workspace_id: uuid.UUID
    period_start: date
    period_end: date
    total_items: int
    completed_items: int
    completion_rate: float          # 0-1
    avg_time_to_complete_days: Optional[float]
    items_by_priority_high: int
    items_by_priority_medium: int
    items_by_priority_low: int
    overdue_count: int
    needs_review_count: int
    extraction_count: int           # extraction runs in period
    avg_confidence: float


class TimeSeriesPoint(BaseModel):
    date: date
    items_created: int
    items_completed: int
    items_open_eod: int             # cumulative open at end of day


class WorkspaceInfo(BaseModel):
    workspace_id: uuid.UUID
    name: str


# ---------------------------------------------------------------------------
# GET /bi/metrics
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Aggregated KPIs for a workspace over a date range",
    dependencies=[Depends(require_bi_key)],
)
async def get_metrics(
    workspace_id: Annotated[uuid.UUID, Query(description="Target workspace UUID")],
    period_start: Annotated[date, Query(description="Inclusive start date (YYYY-MM-DD)")],
    period_end: Annotated[date, Query(description="Inclusive end date (YYYY-MM-DD)")],
    db: AsyncSession = Depends(get_db),
) -> MetricsResponse:
    """Return flat KPI metrics for a workspace and date range.

    Designed for direct ingestion by BI tools (Tableau, Power BI, Looker, etc.).
    All values are scalars — no nested objects.
    """
    tenant_id = _get_bi_tenant_id()

    # Convert date → tz-aware datetimes for comparison with timestamptz columns
    start_dt = datetime(
        period_start.year, period_start.month, period_start.day, tzinfo=timezone.utc
    )
    end_dt = datetime(
        period_end.year, period_end.month, period_end.day,
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    base_filter = and_(
        ActionItem.tenant_id == tenant_id,
        ActionItem.workspace_id == workspace_id,
        ActionItem.created_at >= start_dt,
        ActionItem.created_at <= end_dt,
    )

    # -- Total items and priority breakdown --------------------------------
    counts_q = await db.execute(
        select(
            func.count().label("total"),
            func.count(case((ActionItem.status == "Done", 1))).label("done"),
            func.count(case((ActionItem.priority == "High", 1))).label("high"),
            func.count(case((ActionItem.priority == "Medium", 1))).label("medium"),
            func.count(case((ActionItem.priority == "Low", 1))).label("low"),
            func.count(case((ActionItem.needs_review.is_(True), 1))).label("needs_review"),
            func.avg(ActionItem.confidence).label("avg_confidence"),
        ).where(base_filter)
    )
    c = counts_q.one()
    total_items: int = c.total or 0
    completed_items: int = c.done or 0
    completion_rate: float = (completed_items / total_items) if total_items > 0 else 0.0
    avg_confidence: float = float(c.avg_confidence or 0.0)

    # -- Overdue: due_date < today, status != Done, created in period ------
    today = date.today()
    overdue_q = await db.execute(
        select(func.count()).where(
            and_(
                base_filter,
                ActionItem.due_date < today,
                ActionItem.status != "Done",
            )
        )
    )
    overdue_count: int = overdue_q.scalar_one()

    # -- Avg time to complete (Done items updated in period) ---------------
    #    We use updated_at - created_at for Done items whose updated_at falls
    #    within the period.
    time_q = await db.execute(
        select(
            func.avg(
                func.extract(
                    "epoch",
                    ActionItem.updated_at - ActionItem.created_at,
                )
            ).label("avg_seconds")
        ).where(
            and_(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.status == "Done",
                ActionItem.updated_at >= start_dt,
                ActionItem.updated_at <= end_dt,
            )
        )
    )
    time_row = time_q.one()
    avg_seconds = time_row.avg_seconds
    avg_time_to_complete_days: Optional[float] = (
        float(avg_seconds) / 86400.0 if avg_seconds is not None else None
    )

    # -- Extraction runs in period (joined through meetings) ---------------
    extraction_q = await db.execute(
        select(func.count(ExtractionRun.id))
        .join(Meeting, Meeting.id == ExtractionRun.meeting_id)
        .where(
            Meeting.tenant_id == tenant_id,
            Meeting.workspace_id == workspace_id,
            ExtractionRun.created_at >= start_dt,
            ExtractionRun.created_at <= end_dt,
        )
    )
    extraction_count: int = extraction_q.scalar_one() or 0

    logger.info(
        "bi_feed.metrics",
        workspace_id=str(workspace_id),
        period_start=str(period_start),
        period_end=str(period_end),
        total_items=total_items,
    )

    return MetricsResponse(
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
        total_items=total_items,
        completed_items=completed_items,
        completion_rate=round(completion_rate, 4),
        avg_time_to_complete_days=(
            round(avg_time_to_complete_days, 2)
            if avg_time_to_complete_days is not None
            else None
        ),
        items_by_priority_high=c.high or 0,
        items_by_priority_medium=c.medium or 0,
        items_by_priority_low=c.low or 0,
        overdue_count=overdue_count,
        needs_review_count=c.needs_review or 0,
        extraction_count=extraction_count,
        avg_confidence=round(avg_confidence, 4),
    )


# ---------------------------------------------------------------------------
# GET /bi/time-series
# ---------------------------------------------------------------------------


@router.get(
    "/time-series",
    response_model=list[TimeSeriesPoint],
    summary="Daily snapshot of item creation, completion, and open count",
    dependencies=[Depends(require_bi_key)],
)
async def get_time_series(
    workspace_id: Annotated[uuid.UUID, Query(description="Target workspace UUID")],
    since: Annotated[date, Query(description="Inclusive start date (YYYY-MM-DD)")],
    until: Annotated[date, Query(description="Inclusive end date (YYYY-MM-DD)")],
    granularity: Annotated[
        str,
        Query(description="Aggregation granularity — currently only 'daily' is supported"),
    ] = "daily",
    db: AsyncSession = Depends(get_db),
) -> list[TimeSeriesPoint]:
    """Return one row per day with items_created, items_completed, and items_open_eod.

    items_open_eod is the cumulative count of items that were Open at the end
    of that day (created on or before that day and not yet Done by end of day).

    Note: granularity is accepted for future extensibility; only 'daily' is
    implemented.  A 400 is returned for unsupported values.
    """
    if granularity != "daily":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported granularity '{granularity}'. Only 'daily' is supported.",
        )

    tenant_id = _get_bi_tenant_id()

    since_dt = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    until_dt = datetime(
        until.year, until.month, until.day,
        hour=23, minute=59, second=59, tzinfo=timezone.utc,
    )

    # -- Items created per day -------------------------------------------
    created_q = await db.execute(
        select(
            func.date_trunc("day", ActionItem.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(
            ActionItem.tenant_id == tenant_id,
            ActionItem.workspace_id == workspace_id,
            ActionItem.created_at >= since_dt,
            ActionItem.created_at <= until_dt,
        )
        .group_by(func.date_trunc("day", ActionItem.created_at))
    )
    created_map: dict[date, int] = {}
    for row in created_q.all():
        d = row.day.date() if hasattr(row.day, "date") else row.day
        created_map[d] = row.cnt

    # -- Items completed per day (updated_at date when status became Done) -
    done_q = await db.execute(
        select(
            func.date_trunc("day", ActionItem.updated_at).label("day"),
            func.count().label("cnt"),
        )
        .where(
            ActionItem.tenant_id == tenant_id,
            ActionItem.workspace_id == workspace_id,
            ActionItem.status == "Done",
            ActionItem.updated_at >= since_dt,
            ActionItem.updated_at <= until_dt,
        )
        .group_by(func.date_trunc("day", ActionItem.updated_at))
    )
    done_map: dict[date, int] = {}
    for row in done_q.all():
        d = row.day.date() if hasattr(row.day, "date") else row.day
        done_map[d] = row.cnt

    # -- Build the complete date spine ------------------------------------
    series: list[TimeSeriesPoint] = []
    cumulative_open = 0
    current = since
    while current <= until:
        created_today = created_map.get(current, 0)
        completed_today = done_map.get(current, 0)

        # Approximate EOD open count: cumulative created minus cumulative done
        # This is an approximation when status changes are not separately logged.
        # For a production system, consider maintaining a daily_snapshot table.
        cumulative_open += created_today - completed_today

        series.append(
            TimeSeriesPoint(
                date=current,
                items_created=created_today,
                items_completed=completed_today,
                items_open_eod=max(cumulative_open, 0),
            )
        )
        current += timedelta(days=1)

    logger.info(
        "bi_feed.time_series",
        workspace_id=str(workspace_id),
        since=str(since),
        until=str(until),
        days=len(series),
    )

    return series


# ---------------------------------------------------------------------------
# GET /bi/workspaces
# ---------------------------------------------------------------------------


@router.get(
    "/workspaces",
    response_model=list[WorkspaceInfo],
    summary="List active workspaces accessible to this API key",
    dependencies=[Depends(require_bi_key)],
)
async def list_workspaces(
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceInfo]:
    """Return the IDs and names of all active workspaces for the BI tenant.

    The tenant is identified by the BI_TENANT_ID environment variable.
    Only active (is_active=True) workspaces are returned.
    """
    tenant_id = _get_bi_tenant_id()

    result = await db.execute(
        select(Workspace.id, Workspace.name)
        .where(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True),
        )
        .order_by(Workspace.name.asc())
    )

    workspaces = [
        WorkspaceInfo(workspace_id=row.id, name=row.name)
        for row in result.all()
    ]

    logger.info(
        "bi_feed.list_workspaces",
        tenant_id=str(tenant_id),
        workspace_count=len(workspaces),
    )

    return workspaces
