"""ActionItem repository — tenant-scoped data access for ActionItem records."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_item import ActionItem
from app.repositories.base import TenantScopedRepository


class ItemRepository(TenantScopedRepository[ActionItem]):
    """All database operations for the ActionItem model.

    Every method filters by tenant_id so cross-tenant data access is
    structurally impossible at the repository layer.
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db, ActionItem)

    # ------------------------------------------------------------------
    # Domain-specific queries
    # ------------------------------------------------------------------

    async def list_filtered(
        self,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        owner_user_id: Optional[uuid.UUID] = None,
        meeting_id: Optional[uuid.UUID] = None,
        needs_review: Optional[bool] = None,
        search: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[ActionItem], int]:
        """Return a filtered, paginated list of action items.

        All filters are optional and combined with AND.  The ``search``
        parameter performs a case-insensitive substring match on ``task`` text.

        SQLite (used in tests) does not have ILIKE; we fall back to
        ``LIKE`` with lower() on both sides for cross-dialect compatibility.

        Args:
            tenant_id: Owning tenant — applied unconditionally.
            workspace_id: Target workspace — applied unconditionally.
            status: Filter by action item status enum value.
            priority: Filter by priority enum value.
            owner_user_id: Filter by assigned user UUID.
            meeting_id: Filter to items from a specific meeting.
            needs_review: Filter by the needs_review boolean flag.
            search: Case-insensitive substring search on the task field.
            page: 1-based page number.
            size: Records per page.

        Returns:
            ``(items, total_count)`` tuple.
        """
        stmt = (
            select(ActionItem)
            .where(ActionItem.tenant_id == tenant_id)
            .where(ActionItem.workspace_id == workspace_id)
        )

        if status is not None:
            stmt = stmt.where(ActionItem.status == status)
        if priority is not None:
            stmt = stmt.where(ActionItem.priority == priority)
        if owner_user_id is not None:
            stmt = stmt.where(ActionItem.owner_user_id == owner_user_id)
        if meeting_id is not None:
            stmt = stmt.where(ActionItem.meeting_id == meeting_id)
        if needs_review is not None:
            stmt = stmt.where(ActionItem.needs_review == needs_review)
        if search is not None:
            # Use lower() for cross-dialect case-insensitive search.
            # On PostgreSQL this compiles to a LIKE with lowered values;
            # on SQLite (tests) it uses SQLite's built-in LIKE case folding.
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(
                func.lower(ActionItem.task).like(pattern)
            )

        # Total before pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await self._db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * size
        stmt = stmt.order_by(ActionItem.created_at.desc()).offset(offset).limit(size)
        result = await self._db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def bulk_create(
        self,
        items: list[dict],
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        created_by: uuid.UUID,
    ) -> list[ActionItem]:
        """Insert multiple ActionItem records in a single flush.

        Each item dict is merged with the provided tenant context so that
        tenant_id, workspace_id, and created_by are always correct regardless
        of what the caller passes in the dicts.

        Args:
            items: List of field dicts describing each action item.
            tenant_id: Owning tenant — stamped onto every record.
            workspace_id: Target workspace — stamped onto every record.
            created_by: User who initiated the bulk creation.

        Returns:
            List of persisted ActionItem ORM objects (after flush + refresh).
        """
        created: list[ActionItem] = []
        for item_data in items:
            payload = {
                **item_data,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "created_by": created_by,
            }
            obj = ActionItem(**payload)
            self._db.add(obj)
            created.append(obj)

        await self._db.flush()

        # Refresh each object to populate server-set defaults (e.g. created_at)
        for obj in created:
            await self._db.refresh(obj)

        return created

    async def get_open_for_workspace(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> list[ActionItem]:
        """Return all Open or In-progress items for a workspace.

        Intended for duplicate-detection: the caller compares these items
        against newly extracted items before deciding whether to persist them.

        Args:
            workspace_id: Target workspace.
            tenant_id: Owning tenant — applied unconditionally.

        Returns:
            List of ActionItem ORM objects with status Open or In progress.
        """
        stmt = (
            select(ActionItem)
            .where(ActionItem.tenant_id == tenant_id)
            .where(ActionItem.workspace_id == workspace_id)
            .where(
                or_(
                    ActionItem.status == "Open",
                    ActionItem.status == "In progress",
                )
            )
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
