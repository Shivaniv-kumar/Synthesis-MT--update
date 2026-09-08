"""ReportingService — aggregated analytics for the Meeting Action Tracker.

All public methods are async and accept (workspace_id, tenant_id, db) so they
can be called from any FastAPI endpoint without coupling to the request context.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import case, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_item import ActionItem
from app.models.extraction_run import ExtractionRun
from app.models.meeting import Meeting

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Typed return shapes
# ---------------------------------------------------------------------------


@dataclass
class DailyThroughput:
    """Items created and completed on a single calendar day."""

    date: date
    created_count: int
    completed_count: int


@dataclass
class MeetingStat:
    """Per-meeting action-item summary."""

    meeting_id: uuid.UUID
    title: str
    occurred_at: Optional[datetime]
    item_count: int
    extracted_at: Optional[datetime]


@dataclass
class OwnerStat:
    """Per-owner workload snapshot."""

    owner_label: str
    open_count: int
    done_count: int
    overdue_count: int


@dataclass
class ExtractionQualityMetrics:
    """Aggregate quality indicators across recent extraction runs."""

    avg_confidence: float
    review_rate: float          # fraction of items flagged needs_review
    total_runs: int             # extraction runs in last 30 days
    avg_items_per_run: float
    model_usage: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReportingService:
    """Stateless service; instantiate once per request or as a singleton."""

    # ------------------------------------------------------------------
    # Dashboard summary (mirrors DashboardSummary schema)
    # ------------------------------------------------------------------

    async def get_dashboard_summary(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        """Return the same shape as the DashboardSummary Pydantic schema."""

        today = date.today()
        week_ago = datetime.now(tz=timezone.utc) - timedelta(days=7)

        # -- Status counts -------------------------------------------------
        status_q = await db.execute(
            select(
                func.count(case((ActionItem.status == "Open", 1))).label("open"),
                func.count(case((ActionItem.status == "In progress", 1))).label(
                    "in_progress"
                ),
                func.count(case((ActionItem.status == "Done", 1))).label("done"),
            ).where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
            )
        )
        status_row = status_q.one()
        total_open = status_row.open or 0
        total_in_progress = status_row.in_progress or 0
        total_done = status_row.done or 0

        # -- Overdue -------------------------------------------------------
        overdue_q = await db.execute(
            select(func.count()).where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.due_date < today,
                ActionItem.status != "Done",
            )
        )
        overdue_count: int = overdue_q.scalar_one()

        # -- Needs review --------------------------------------------------
        review_q = await db.execute(
            select(func.count()).where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.needs_review.is_(True),
            )
        )
        needs_review_count: int = review_q.scalar_one()

        # -- Meetings this week --------------------------------------------
        meetings_q = await db.execute(
            select(func.count()).where(
                Meeting.tenant_id == tenant_id,
                Meeting.workspace_id == workspace_id,
                Meeting.created_at >= week_ago,
            )
        )
        meetings_this_week: int = meetings_q.scalar_one()

        # -- Items by priority ---------------------------------------------
        priority_q = await db.execute(
            select(
                ActionItem.priority,
                func.count().label("cnt"),
            )
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
            )
            .group_by(ActionItem.priority)
        )
        items_by_priority: dict[str, int] = {
            row.priority: row.cnt for row in priority_q.all()
        }
        # Ensure all three priority keys are present
        for p in ("High", "Medium", "Low"):
            items_by_priority.setdefault(p, 0)

        # -- Open items by owner -------------------------------------------
        owner_q = await db.execute(
            select(
                ActionItem.owner_label,
                func.count().label("open_cnt"),
            )
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.status == "Open",
            )
            .group_by(ActionItem.owner_label)
            .order_by(func.count().desc())
        )
        items_by_owner: dict[str, int] = {
            (row.owner_label or "(unassigned)"): row.open_cnt
            for row in owner_q.all()
        }

        logger.debug(
            "reporting.dashboard_summary",
            workspace_id=str(workspace_id),
            total_open=total_open,
            overdue_count=overdue_count,
        )

        return {
            "total_open": total_open,
            "total_in_progress": total_in_progress,
            "total_done": total_done,
            "overdue_count": overdue_count,
            "needs_review_count": needs_review_count,
            "meetings_this_week": meetings_this_week,
            "items_by_priority": items_by_priority,
            "items_by_owner": items_by_owner,
        }

    # ------------------------------------------------------------------
    # Throughput — daily created / completed counts
    # ------------------------------------------------------------------

    async def get_throughput(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
        days: int = 30,
    ) -> list[DailyThroughput]:
        """Return one DailyThroughput per calendar day for the last *days* days.

        Uses PostgreSQL ``DATE_TRUNC`` for created counts and queries
        ``updated_at`` cast to date for completion (status=Done) counts,
        then merges the two series client-side to guarantee every day in the
        window appears even if there was no activity.
        """
        since = datetime.now(tz=timezone.utc) - timedelta(days=days)

        # Created per day
        created_q = await db.execute(
            select(
                func.date_trunc("day", ActionItem.created_at).label("day"),
                func.count().label("cnt"),
            )
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.created_at >= since,
            )
            .group_by(func.date_trunc("day", ActionItem.created_at))
        )
        created_map: dict[date, int] = {}
        for row in created_q.all():
            d = row.day.date() if hasattr(row.day, "date") else row.day
            created_map[d] = row.cnt

        # Completed (set to Done) per day — keyed by updated_at date
        done_q = await db.execute(
            select(
                func.date_trunc("day", ActionItem.updated_at).label("day"),
                func.count().label("cnt"),
            )
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.status == "Done",
                ActionItem.updated_at >= since,
            )
            .group_by(func.date_trunc("day", ActionItem.updated_at))
        )
        done_map: dict[date, int] = {}
        for row in done_q.all():
            d = row.day.date() if hasattr(row.day, "date") else row.day
            done_map[d] = row.cnt

        # Build a complete series covering all days in the window
        all_days: set[date] = set(created_map) | set(done_map)
        start_date = (datetime.now(tz=timezone.utc) - timedelta(days=days - 1)).date()
        end_date = date.today()
        current = start_date
        while current <= end_date:
            all_days.add(current)
            current += timedelta(days=1)

        result: list[DailyThroughput] = [
            DailyThroughput(
                date=d,
                created_count=created_map.get(d, 0),
                completed_count=done_map.get(d, 0),
            )
            for d in sorted(all_days)
        ]
        return result

    # ------------------------------------------------------------------
    # Overdue items
    # ------------------------------------------------------------------

    async def get_overdue_items(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[ActionItem]:
        """Return all non-Done items whose due_date is in the past."""
        today = date.today()
        result = await db.execute(
            select(ActionItem)
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
                ActionItem.due_date < today,
                ActionItem.status != "Done",
            )
            .order_by(ActionItem.due_date.asc())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Per-meeting statistics
    # ------------------------------------------------------------------

    async def get_per_meeting_stats(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[MeetingStat]:
        """JOIN meetings with action-item counts, optionally filtered by date range."""

        stmt = (
            select(
                Meeting.id.label("meeting_id"),
                Meeting.title,
                Meeting.occurred_at,
                Meeting.created_at.label("extracted_at"),
                func.count(ActionItem.id).label("item_count"),
            )
            .outerjoin(
                ActionItem,
                and_(
                    ActionItem.meeting_id == Meeting.id,
                    ActionItem.tenant_id == tenant_id,
                    ActionItem.workspace_id == workspace_id,
                ),
            )
            .where(
                Meeting.tenant_id == tenant_id,
                Meeting.workspace_id == workspace_id,
            )
            .group_by(Meeting.id, Meeting.title, Meeting.occurred_at, Meeting.created_at)
            .order_by(Meeting.occurred_at.desc().nulls_last())
            .limit(50)
        )

        if since is not None:
            stmt = stmt.where(Meeting.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(Meeting.occurred_at <= until)

        rows = await db.execute(stmt)
        return [
            MeetingStat(
                meeting_id=row.meeting_id,
                title=row.title,
                occurred_at=row.occurred_at,
                item_count=row.item_count or 0,
                extracted_at=row.extracted_at,
            )
            for row in rows.all()
        ]

    # ------------------------------------------------------------------
    # Per-owner statistics
    # ------------------------------------------------------------------

    async def get_per_owner_stats(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[OwnerStat]:
        """GROUP BY owner_label with open, done, and overdue counts."""
        today = date.today()

        result = await db.execute(
            select(
                ActionItem.owner_label,
                func.count(
                    case((ActionItem.status == "Open", 1))
                ).label("open_count"),
                func.count(
                    case((ActionItem.status == "Done", 1))
                ).label("done_count"),
                func.count(
                    case(
                        (
                            and_(
                                ActionItem.status != "Done",
                                ActionItem.due_date < today,
                            ),
                            1,
                        )
                    )
                ).label("overdue_count"),
            )
            .where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
            )
            .group_by(ActionItem.owner_label)
            .order_by(
                func.count(case((ActionItem.status == "Open", 1))).desc()
            )
        )

        return [
            OwnerStat(
                owner_label=row.owner_label or "(unassigned)",
                open_count=row.open_count or 0,
                done_count=row.done_count or 0,
                overdue_count=row.overdue_count or 0,
            )
            for row in result.all()
        ]

    # ------------------------------------------------------------------
    # Extraction quality metrics
    # ------------------------------------------------------------------

    async def get_extraction_quality(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> ExtractionQualityMetrics:
        """Aggregate quality stats from action_items and extraction_runs.

        ExtractionRun has no tenant/workspace columns — it is linked via
        Meeting, so we JOIN through meetings to enforce workspace isolation.
        """
        since_30d = datetime.now(tz=timezone.utc) - timedelta(days=30)

        # --- Confidence and review rate from action_items ----------------
        quality_q = await db.execute(
            select(
                func.avg(ActionItem.confidence).label("avg_confidence"),
                func.count(
                    case((ActionItem.needs_review.is_(True), 1))
                ).label("review_count"),
                func.count().label("total_count"),
            ).where(
                ActionItem.tenant_id == tenant_id,
                ActionItem.workspace_id == workspace_id,
            )
        )
        quality_row = quality_q.one()
        avg_confidence: float = float(quality_row.avg_confidence or 0.0)
        total_items: int = quality_row.total_count or 0
        review_count: int = quality_row.review_count or 0
        review_rate: float = (review_count / total_items) if total_items > 0 else 0.0

        # --- Extraction run counts via meeting JOIN -----------------------
        runs_q = await db.execute(
            select(
                func.count(ExtractionRun.id).label("total_runs"),
                func.avg(ExtractionRun.item_count).label("avg_items"),
            )
            .join(Meeting, Meeting.id == ExtractionRun.meeting_id)
            .where(
                Meeting.tenant_id == tenant_id,
                Meeting.workspace_id == workspace_id,
                ExtractionRun.created_at >= since_30d,
            )
        )
        runs_row = runs_q.one()
        total_runs: int = runs_row.total_runs or 0
        avg_items_per_run: float = float(runs_row.avg_items or 0.0)

        # --- Model usage breakdown ----------------------------------------
        model_q = await db.execute(
            select(
                ExtractionRun.model,
                func.count().label("cnt"),
            )
            .join(Meeting, Meeting.id == ExtractionRun.meeting_id)
            .where(
                Meeting.tenant_id == tenant_id,
                Meeting.workspace_id == workspace_id,
                ExtractionRun.created_at >= since_30d,
            )
            .group_by(ExtractionRun.model)
        )
        model_usage: dict[str, int] = {
            row.model: row.cnt for row in model_q.all()
        }

        return ExtractionQualityMetrics(
            avg_confidence=avg_confidence,
            review_rate=review_rate,
            total_runs=total_runs,
            avg_items_per_run=avg_items_per_run,
            model_usage=model_usage,
        )
