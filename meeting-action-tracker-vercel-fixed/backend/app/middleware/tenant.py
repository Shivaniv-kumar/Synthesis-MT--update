"""Tenant context FastAPI dependency.

Provides ``get_tenant_context``, a dependency that extracts tenant/workspace/user
identity from the authenticated user and returns a ``TenantContext`` dataclass.

Usage::

    @router.get("/resource")
    async def endpoint(ctx: TenantContext = Depends(get_tenant_context)):
        # ctx.tenant_id, ctx.workspace_id, ctx.user_id, ctx.role
        ...
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.models import User


@dataclass
class TenantContext:
    """Bundle of identity values derived from the authenticated user."""

    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str


async def get_tenant_context(
    current_user: User = Depends(get_current_user),
) -> TenantContext:
    """FastAPI dependency that builds a ``TenantContext`` from the current user.

    ``workspace_id`` falls back to ``tenant_id`` when the user does not yet
    have a dedicated workspace_id column (backward-compatible with existing
    User rows that predate workspace support).
    """
    workspace_id: uuid.UUID = getattr(
        current_user, "workspace_id", current_user.tenant_id
    )
    role: str = getattr(current_user, "role", "Viewer") or "Viewer"

    return TenantContext(
        tenant_id=current_user.tenant_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        role=role,
    )
