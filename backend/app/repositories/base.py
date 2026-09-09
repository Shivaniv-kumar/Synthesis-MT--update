"""Tenant-scoped base repository.

Every repository that works with tenant-isolated data should extend
TenantScopedRepository.  All query methods require a TenantContext so that
the tenant_id filter is applied unconditionally — the caller cannot accidentally
omit it.

Security note: get_by_id raises HTTP 404 (not 403) when the record does not
belong to the requesting tenant.  This avoids leaking whether a given UUID
exists in another tenant's data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Context object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantContext:
    """Carries the caller's tenant / workspace / user identity.

    All three fields are required; the repository uses them to scope every
    query and to stamp new records with the correct tenant_id / workspace_id.
    """

    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID


# ---------------------------------------------------------------------------
# Generic base repository
# ---------------------------------------------------------------------------


class TenantScopedRepository(Generic[T]):
    """Generic CRUD repository that enforces tenant isolation on every operation.

    Subclasses call ``super().__init__(db, MyModel)`` and then add domain-
    specific query methods on top.
    """

    def __init__(self, db: AsyncSession, model_class: type[T]) -> None:
        self._db = db
        self._model = model_class

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_query(self, tenant_id: uuid.UUID):
        """Return a SELECT statement pre-filtered by tenant_id."""
        return select(self._model).where(
            self._model.tenant_id == tenant_id  # type: ignore[attr-defined]
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_by_id(self, id: uuid.UUID, tenant_ctx: TenantContext) -> T:
        """Fetch a single record by primary key, enforcing tenant ownership.

        Raises HTTP 404 when the record is missing or belongs to a different
        tenant.  Never raises 403 — doing so would confirm that the UUID exists
        in another tenant's data (oracle / enumeration attack).
        """
        stmt = self._base_query(tenant_ctx.tenant_id).where(
            self._model.id == id  # type: ignore[attr-defined]
        )
        result = await self._db.execute(stmt)
        obj = result.scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="Record not found.")
        return obj

    async def list(
        self,
        tenant_ctx: TenantContext,
        workspace_id: Optional[uuid.UUID] = None,
        page: int = 1,
        size: int = 50,
        **filters: Any,
    ) -> tuple[list[T], int]:
        """Return a paginated list of records scoped to the tenant.

        Args:
            tenant_ctx: Caller identity — tenant_id is always applied.
            workspace_id: When provided, adds an additional workspace filter.
            page: 1-based page number.
            size: Records per page.
            **filters: Extra equality filters applied as ``column == value``.
                       Only filters whose value is not None are applied.

        Returns:
            A ``(items, total_count)`` tuple suitable for building paginated
            API responses.
        """
        stmt = self._base_query(tenant_ctx.tenant_id)

        if workspace_id is not None:
            stmt = stmt.where(
                self._model.workspace_id == workspace_id  # type: ignore[attr-defined]
            )

        for column_name, value in filters.items():
            if value is not None:
                column = getattr(self._model, column_name, None)
                if column is not None:
                    stmt = stmt.where(column == value)

        # Count before pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        count_result = await self._db.execute(count_stmt)
        total = count_result.scalar_one()

        # Apply pagination
        offset = (page - 1) * size
        stmt = stmt.offset(offset).limit(size)
        result = await self._db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def create(self, data: dict[str, Any], tenant_ctx: TenantContext) -> T:
        """Insert a new record, stamping it with tenant_id and workspace_id.

        The caller's ``data`` dict is merged with the context values so that
        tenant_id and workspace_id are always set correctly even if omitted or
        overridden in ``data``.
        """
        payload = {
            **data,
            "tenant_id": tenant_ctx.tenant_id,
            "workspace_id": tenant_ctx.workspace_id,
        }
        obj = self._model(**payload)  # type: ignore[call-arg]
        self._db.add(obj)
        await self._db.flush()
        await self._db.refresh(obj)
        return obj

    async def update(
        self, id: uuid.UUID, data: dict[str, Any], tenant_ctx: TenantContext
    ) -> T:
        """Update a record's fields, enforcing tenant ownership via get_by_id.

        Only fields present in ``data`` are modified; fields with ``None``
        values are skipped unless the intent is to explicitly null them (callers
        should use a sentinel or filter before passing).
        """
        obj = await self.get_by_id(id, tenant_ctx)
        for field, value in data.items():
            if value is not None and hasattr(obj, field):
                setattr(obj, field, value)
        self._db.add(obj)
        await self._db.flush()
        await self._db.refresh(obj)
        return obj

    async def delete(self, id: uuid.UUID, tenant_ctx: TenantContext) -> None:
        """Delete a record, enforcing tenant ownership via get_by_id."""
        obj = await self.get_by_id(id, tenant_ctx)
        await self._db.delete(obj)
        await self._db.flush()
