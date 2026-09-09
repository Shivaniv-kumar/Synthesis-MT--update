"""Role-Based Access Control (RBAC) for the Meeting Action Tracker."""

from __future__ import annotations

from enum import Enum
from typing import Callable

import structlog
from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.models import User

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Permission catalogue
# ---------------------------------------------------------------------------


class Permission(str, Enum):
    """All discrete actions that can be permitted or denied."""

    # Meeting lifecycle
    CREATE_MEETING = "create_meeting"
    DELETE_MEETING = "delete_meeting"
    EXTRACT = "extract"

    # Action item CRUD
    EDIT_ITEM = "edit_item"
    DELETE_ITEM = "delete_item"
    VIEW_ITEM = "view_item"

    # Workspace / tenant administration
    MANAGE_WORKSPACE = "manage_workspace"
    MANAGE_MEMBERS = "manage_members"
    CONFIGURE_RETENTION = "configure_retention"

    # Dashboards / reporting
    VIEW_DASHBOARD = "view_dashboard"
    EXPORT_DATA = "export_data"   # bulk CSV/PDF export (Members+, not Viewers)
    VIEW_ADMIN = "view_admin"


# ---------------------------------------------------------------------------
# Role → permission mappings
# ---------------------------------------------------------------------------

_ALL_PERMISSIONS: set[Permission] = set(Permission)

ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "Admin": _ALL_PERMISSIONS,
    "Member": {
        Permission.CREATE_MEETING,
        Permission.DELETE_MEETING,
        Permission.EXTRACT,
        Permission.EDIT_ITEM,
        Permission.DELETE_ITEM,
        Permission.VIEW_ITEM,
        Permission.VIEW_DASHBOARD,
        Permission.EXPORT_DATA,
    },
    "Viewer": {
        Permission.VIEW_ITEM,
        Permission.VIEW_DASHBOARD,
        # EXPORT_DATA intentionally omitted: Viewers can see dashboards but not bulk-export
    },
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def has_permission(role: str, permission: Permission) -> bool:
    """Return True if *role* is granted *permission*.

    Unknown roles are treated as having no permissions.
    """
    return permission in ROLE_PERMISSIONS.get(role, set())


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def require_permission(permission: Permission) -> Callable[..., User]:
    """Return a FastAPI dependency that enforces *permission* on the caller."""

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        # C8: role is a required column on User — no silent fallback
        role: str = current_user.role
        if not role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User role is not configured.",
            )
        if not has_permission(role, permission):
            logger.warning(
                "rbac.denied",
                user_id=str(current_user.id),
                role=role,
                permission=permission.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' does not have the '{permission.value}' permission.",
            )
        return current_user

    _check.__name__ = f"require_{permission.value}"
    return _check
