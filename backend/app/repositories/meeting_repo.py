"""Meeting repository — tenant-scoped data access for Meeting records."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.meeting import Meeting
from app.repositories.base import TenantScopedRepository


class MeetingRepository(TenantScopedRepository[Meeting]):
    """All database operations for the Meeting model.

    Every method filters by tenant_id so cross-tenant data access is
    structurally impossible at the repository layer.
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, Meeting)

    # ------------------------------------------------------------------
    # Domain-specific queries
    # ------------------------------------------------------------------

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        status_filter: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[Meeting], int]:
        """Return paginated meetings for a specific workspace.

        Args:
            workspace_id: Target workspace; combined with tenant_id for safety.
            tenant_id: Owning tenant — applied unconditionally.
            status_filter: Optional meeting status string (e.g. "extracted").
            page: 1-based page number.
            size: Records per page.

        Returns:
            ``(meetings, total_count)`` tuple.
        """
        stmt = (
            select(Meeting)
            .where(Meeting.tenant_id == tenant_id)
            .where(Meeting.workspace_id == workspace_id)
        )

        if status_filter is not None:
            stmt = stmt.where(Meeting.status == status_filter)

        # Total before pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await self._db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = stmt.order_by(Meeting.created_at.desc()).offset(offset).limit(size)
        result = await self._db.execute(stmt)
        meetings = list(result.scalars().all())

        return meetings, total

    async def get_with_items(
        self,
        meeting_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Meeting:
        """Fetch a meeting together with its action items in a single query.

        Raises HTTP 404 when the meeting is not found or does not belong to
        the specified tenant (never reveals cross-tenant existence).

        Args:
            meeting_id: Primary key of the meeting.
            tenant_id: Owning tenant — prevents cross-tenant enumeration.

        Returns:
            The Meeting ORM object with ``action_items`` eagerly loaded.
        """
        stmt = (
            select(Meeting)
            .where(Meeting.id == meeting_id)
            .where(Meeting.tenant_id == tenant_id)
            .options(selectinload(Meeting.action_items))
        )
        result = await self._db.execute(stmt)
        meeting = result.scalar_one_or_none()
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found.")
        return meeting
