"""Admin cost-tracking and rate-limit configuration API.

Router prefix : /admin/costs
Auth required : Admin role (Permission.VIEW_ADMIN)

Endpoints
---------
GET  /today          — Today's spend summary with per-model breakdown
GET  /history        — Last 30 days of daily spend from Redis
GET  /projection     — 30-day spend projection based on last 7-day average
PATCH /limits        — Update workspace daily spend cap and extraction rate limit
GET  /rate-limits    — Current rate-limit configuration for the workspace

The ``breakdown_by_model`` computation for ``GET /today`` queries the
``extraction_runs`` table and multiplies token counts by the pricing table
in :mod:`app.observability.cost`.  Redis is used for the aggregated daily
totals in ``GET /history`` and as the fast-path for ``check_spend_cap``.

Workspace spend limits and rate overrides are stored in the ``Workspace.config``
JSON column (added if not yet present on the model via ``getattr`` with a
default so existing rows without the column still work).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Permission, require_permission
from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import TenantContext, get_tenant_context
from app.models import ExtractionRun, Workspace
from app.models import User
from app.observability.cost import MODEL_PRICING, CostTracker, compute_cost

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/costs", tags=["admin-costs"])


# ---------------------------------------------------------------------------
# Redis client dependency
# ---------------------------------------------------------------------------


def _get_redis() -> aioredis.Redis:
    """Return a Redis client using the application settings."""
    settings = get_settings()
    return aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)


# ---------------------------------------------------------------------------
# Pydantic response / request models
# ---------------------------------------------------------------------------


class TodaySpendResponse(BaseModel):
    tenant_id: str
    date: str
    total_spend_usd: float
    breakdown_by_model: dict[str, float]


class DailySpend(BaseModel):
    date: str
    spend_usd: float


class HistoryResponse(BaseModel):
    tenant_id: str
    history: list[DailySpend]


class ProjectionResponse(BaseModel):
    tenant_id: str
    avg_daily_usd: float
    projected_monthly_usd: float
    based_on_days: int


class UpdateLimitsRequest(BaseModel):
    max_daily_spend_usd: float = Field(..., gt=0, description="Daily spend cap in USD")
    max_extractions_per_hour: int = Field(
        ..., gt=0, description="Maximum extraction requests per hour"
    )


class RateLimitConfigResponse(BaseModel):
    tenant_id: str
    workspace_id: str
    max_daily_spend_usd: float | None
    max_extractions_per_hour: int
    requests_per_hour: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_workspace_or_404(
    workspace_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> Workspace:
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True),
        )
    )
    workspace: Workspace | None = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found.",
        )
    return workspace


def _workspace_config(workspace: Workspace) -> dict[str, Any]:
    """Return the workspace's ``config`` JSON dict, defaulting to ``{}``."""
    return getattr(workspace, "config", None) or {}


# ---------------------------------------------------------------------------
# GET /admin/costs/today
# ---------------------------------------------------------------------------


@router.get(
    "/today",
    response_model=TodaySpendResponse,
    summary="Today's LLM spend with per-model breakdown",
)
async def get_today_spend(
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> TodaySpendResponse:
    """Return today's cumulative LLM spend and a breakdown by model.

    The ``total_spend_usd`` is computed fresh from the ``extraction_runs``
    table so it is always accurate even if Redis keys have expired.  The
    per-model breakdown aggregates ``SUM(input_tokens)`` and
    ``SUM(output_tokens)`` grouped by ``model``, then applies the pricing
    table from :mod:`app.observability.cost`.
    """
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    # Query extraction_runs created today, grouped by model
    result = await db.execute(
        select(
            ExtractionRun.model,
            func.sum(ExtractionRun.input_tokens).label("total_input"),
            func.sum(ExtractionRun.output_tokens).label("total_output"),
        )
        .join(ExtractionRun.meeting)
        .where(
            ExtractionRun.created_at >= today_start,
        )
        .group_by(ExtractionRun.model)
    )
    rows = result.all()

    breakdown: dict[str, float] = {}
    total = 0.0
    for row in rows:
        model_name: str = row.model
        cost = compute_cost(
            model_name,
            int(row.total_input or 0),
            int(row.total_output or 0),
        )
        breakdown[model_name] = round(cost, 6)
        total += cost

    logger.info(
        "admin.costs.today",
        tenant_id=str(ctx.tenant_id),
        total_spend_usd=round(total, 6),
        model_count=len(breakdown),
    )

    return TodaySpendResponse(
        tenant_id=str(ctx.tenant_id),
        date=date.today().isoformat(),
        total_spend_usd=round(total, 6),
        breakdown_by_model=breakdown,
    )


# ---------------------------------------------------------------------------
# GET /admin/costs/history
# ---------------------------------------------------------------------------


@router.get(
    "/history",
    response_model=HistoryResponse,
    summary="Last 30 days of daily spend",
)
async def get_spend_history(
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> HistoryResponse:
    """Return 30 days of per-day spend aggregates read from Redis.

    Days with no recorded spend are returned with ``spend_usd: 0.0``.
    """
    redis_client = _get_redis()
    tracker = CostTracker(redis_client)

    history_raw = await tracker.get_spend_history(str(ctx.tenant_id), days=30)

    history = [
        DailySpend(date=entry["date"], spend_usd=entry["spend_usd"])
        for entry in history_raw
    ]

    return HistoryResponse(tenant_id=str(ctx.tenant_id), history=history)


# ---------------------------------------------------------------------------
# GET /admin/costs/projection
# ---------------------------------------------------------------------------


@router.get(
    "/projection",
    response_model=ProjectionResponse,
    summary="30-day spend projection based on last 7-day average",
)
async def get_spend_projection(
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> ProjectionResponse:
    """Extrapolate monthly spend from the 7-day rolling average.

    Only days that have at least some spend are included in the average so
    that weekends or maintenance windows do not artificially deflate the
    projection.  If there are no data points the average defaults to 0.
    """
    redis_client = _get_redis()
    tracker = CostTracker(redis_client)

    history_raw = await tracker.get_spend_history(str(ctx.tenant_id), days=7)

    # Only count days with non-zero spend for a realistic average
    days_with_spend = [e for e in history_raw if e["spend_usd"] > 0]
    if days_with_spend:
        avg_daily = sum(e["spend_usd"] for e in days_with_spend) / len(days_with_spend)
    else:
        avg_daily = 0.0

    projected_monthly = avg_daily * 30

    return ProjectionResponse(
        tenant_id=str(ctx.tenant_id),
        avg_daily_usd=round(avg_daily, 6),
        projected_monthly_usd=round(projected_monthly, 4),
        based_on_days=len(days_with_spend),
    )


# ---------------------------------------------------------------------------
# PATCH /admin/costs/limits
# ---------------------------------------------------------------------------


@router.patch(
    "/limits",
    summary="Update workspace spend cap and extraction rate limit",
    status_code=status.HTTP_200_OK,
)
async def update_limits(
    body: UpdateLimitsRequest,
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Persist rate-limit and spend-cap settings to the Workspace.config column.

    The ``config`` column is expected to be a JSONB / JSON dict on the
    Workspace model.  If the column does not exist on the current model
    (older schema), the endpoint returns 501.

    Stored keys:
        ``max_daily_spend_usd``       — float
        ``max_extractions_per_hour``  — int
    """
    workspace = await _get_workspace_or_404(ctx.workspace_id, ctx.tenant_id, db)

    if not hasattr(workspace, "config"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Workspace.config column is not present in this schema version. "
                "Run the Alembic migration that adds the config JSONB column."
            ),
        )

    current_config: dict[str, Any] = dict(_workspace_config(workspace))
    current_config["max_daily_spend_usd"] = body.max_daily_spend_usd
    current_config["max_extractions_per_hour"] = body.max_extractions_per_hour

    workspace.config = current_config  # type: ignore[attr-defined]
    db.add(workspace)
    await db.flush()

    logger.info(
        "admin.costs.limits_updated",
        workspace_id=str(ctx.workspace_id),
        tenant_id=str(ctx.tenant_id),
        max_daily_spend_usd=body.max_daily_spend_usd,
        max_extractions_per_hour=body.max_extractions_per_hour,
        updated_by=str(current_user.id),
    )

    return {
        "workspace_id": str(ctx.workspace_id),
        "max_daily_spend_usd": body.max_daily_spend_usd,
        "max_extractions_per_hour": body.max_extractions_per_hour,
        "updated": True,
    }


# ---------------------------------------------------------------------------
# GET /admin/costs/rate-limits
# ---------------------------------------------------------------------------


@router.get(
    "/rate-limits",
    response_model=RateLimitConfigResponse,
    summary="Current rate-limit configuration for the workspace",
)
async def get_rate_limits(
    current_user: User = Depends(require_permission(Permission.VIEW_ADMIN)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> RateLimitConfigResponse:
    """Return the effective rate-limit settings for the caller's workspace.

    Values come from ``Workspace.config`` when present; otherwise the
    application defaults defined in :class:`~app.middleware.rate_limit.RateLimitMiddleware`
    are returned.
    """
    workspace = await _get_workspace_or_404(ctx.workspace_id, ctx.tenant_id, db)

    config = _workspace_config(workspace)

    return RateLimitConfigResponse(
        tenant_id=str(ctx.tenant_id),
        workspace_id=str(ctx.workspace_id),
        max_daily_spend_usd=config.get("max_daily_spend_usd"),
        max_extractions_per_hour=int(config.get("max_extractions_per_hour", 100)),
        requests_per_hour=int(config.get("requests_per_hour", 1000)),
    )
