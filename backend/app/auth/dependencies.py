"""FastAPI dependency factories for authentication and authorisation.

Usage:
    @router.get("/")
    async def my_endpoint(
        current_user: User = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
    ): ...

    @router.delete("/admin-only")
    async def admin_only(
        _: User = Depends(require_role(["Admin"])),
    ): ...
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.database import get_db
from app.models import User, WorkspaceMember
from app.models.revoked_token import RevokedToken

logger = structlog.get_logger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


@dataclass
class TenantContext:
    """Convenience bundle of identity values derived from the current JWT."""

    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str  # H11: role is required for RBAC checks downstream


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Decode the bearer token and return the active User ORM object.

    Raises 401 if the token is invalid, the user does not exist, the user
    account is deactivated, or the JWT tenant claim does not match the DB row.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token)  # raises 401 on bad token

    # Check token revocation. Tokens issued before jti was added (pre-deploy)
    # have no jti claim — allow them through until they expire naturally.
    jti = payload.get("jti")
    if jti:
        revoked_result = await db.execute(
            select(RevokedToken).where(RevokedToken.jti == jti)
        )
        if revoked_result.scalar_one_or_none() is not None:
            logger.warning("auth.token_revoked", jti=jti)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked — please log in again",
                headers={"WWW-Authenticate": "Bearer"},
            )

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        logger.warning("jwt.invalid_sub", sub=payload.get("sub"))
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        logger.warning("auth.user_not_found", user_id=str(user_id))
        raise credentials_exception

    if not user.is_active:
        logger.warning("auth.user_inactive", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive account",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # C7: Verify the JWT's tenant claim matches the user's actual tenant in DB.
    # This catches stale tokens issued before a tenant migration or if the token
    # was forged with a different tid claim.
    jwt_tid_str = payload.get("tid", "")
    try:
        jwt_tenant_id = uuid.UUID(jwt_tid_str)
    except ValueError:
        logger.warning("jwt.invalid_tid", tid=jwt_tid_str, user_id=str(user_id))
        raise credentials_exception
    if jwt_tenant_id != user.tenant_id:
        logger.warning(
            "auth.tenant_mismatch",
            jwt_tid=jwt_tid_str,
            db_tenant_id=str(user.tenant_id),
            user_id=str(user_id),
        )
        raise credentials_exception

    return user


def require_role(roles: list[str]) -> Callable[..., User]:
    """Return a dependency factory that ensures the user has one of *roles*.

    Usage::

        @router.post("/admin")
        async def admin_ep(user: User = Depends(require_role(["Admin"]))):
            ...
    """

    async def _check(current_user: User = Depends(get_current_user)) -> User:
        # C8: role is a required column on User — no getattr/or fallback
        user_role: str = current_user.role
        if user_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user_role}' is not authorised for this operation",
            )
        return current_user

    return _check


async def get_tenant_context(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """Extract and verify tenant/workspace context from the authenticated JWT.

    Raises 401 if the workspace_id (wid) claim is missing or malformed.
    Raises 403 if the user is not an active member of the claimed workspace.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # H1: Extract workspace_id from the JWT wid claim. The User model does not
    # store workspace_id; the JWT is the authoritative source per-request.
    payload = decode_token(token)
    wid_str = payload.get("wid", "")
    try:
        workspace_id = uuid.UUID(wid_str)
    except ValueError:
        logger.warning("jwt.invalid_wid", wid=wid_str, user_id=str(current_user.id))
        raise credentials_exception

    # H2: Verify the user is actually a member of the claimed workspace.
    # This prevents a user from accessing a workspace they were removed from
    # while their token was still valid.
    membership_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == current_user.id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    )
    if membership_result.scalar_one_or_none() is None:
        logger.warning(
            "auth.workspace_not_member",
            user_id=str(current_user.id),
            workspace_id=wid_str,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace.",
        )

    return TenantContext(
        tenant_id=current_user.tenant_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        role=current_user.role,  # H11: role is available downstream for RBAC
    )
