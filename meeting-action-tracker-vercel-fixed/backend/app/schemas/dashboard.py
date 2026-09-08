"""Pydantic schemas for dashboard endpoints."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Sub-schemas (used by other endpoints)
# ---------------------------------------------------------------------------


class StatusCounts(BaseModel):
    """Used by owner-stats and other breakdown endpoints."""

    open: int
    in_progress: int
    done: int


class OwnerWorkload(BaseModel):
    """Detailed per-owner workload used by /dashboard/owner-stats."""

    owner_label: str
    owner_user_id: Optional[uuid.UUID]
    open_count: int
    overdue_count: int


# ---------------------------------------------------------------------------
# DashboardSummary — flat shape matching frontend DashboardSummary type
# ---------------------------------------------------------------------------


class ItemsByOwner(BaseModel):
    owner_label: str
    count: int


class ItemsByPriority(BaseModel):
    High: int
    Medium: int
    Low: int


class DashboardSummary(BaseModel):
    """Aggregated workspace summary returned by GET /dashboard/summary.

    Field names match the frontend ``DashboardSummary`` TypeScript interface.
    """

    total_open: int
    total_in_progress: int
    total_done: int
    overdue_count: int
    needs_review_count: int
    meetings_this_week: int
    items_by_priority: ItemsByPriority
    items_by_owner: list[ItemsByOwner]
