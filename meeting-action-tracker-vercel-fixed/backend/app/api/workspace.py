"""Workspace management API.

Prefix: /api/workspaces

All endpoints require a valid JWT.  Administrative operations are gated by
the RBAC permission system (``require_permission``).

Endpoints
---------
GET    /workspaces/                              — list workspaces for current tenant
POST   /workspaces/                              — create a new workspace (Admin)
GET    /workspaces/{workspace_id}/members        — list members (Admin/Member)
POST   /workspaces/{workspace_id}/members        — invite member by email (Admin)
PATCH  /workspaces/{workspace_id}/members/{uid} — change member role (Admin)
DELETE /workspaces/{workspace_id}/members/{uid} — remove member (Admin)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.rbac import Permission, require_permission
from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import TenantContext, get_tenant_context
from app.models import User, Workspace, WorkspaceMember
from app.models.password_reset_token import PasswordResetToken
from app.models.project import Project
from app.models.user_project_access import UserProjectAccess

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# ---------------------------------------------------------------------------
# Inline Pydantic schemas
# ---------------------------------------------------------------------------


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    is_active: bool

    model_config = {"from_attributes": True}


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    workspace_id: uuid.UUID
    invite_pending: bool = False  # True until the invited user sets their password

    model_config = {"from_attributes": True}


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="Member", pattern="^(Admin|Member|Viewer)$")
    project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., pattern="^(Admin|Member|Viewer)$")


class CreateUserRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    role: str = Field(default="Member", pattern="^(Admin|Member|Viewer)$")
    password: str = Field(..., min_length=8, max_length=128)
    project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


# ---------------------------------------------------------------------------
# GET /workspaces/  — list workspaces for the current tenant
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=list[WorkspaceOut],
    summary="List workspaces for the current tenant",
)
async def list_workspaces(
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    result = await db.execute(
        select(Workspace)
        .where(
            Workspace.tenant_id == ctx.tenant_id,
            Workspace.is_active.is_(True),
        )
        .order_by(Workspace.created_at)
    )
    workspaces = result.scalars().all()
    return [WorkspaceOut.model_validate(ws) for ws in workspaces]


# ---------------------------------------------------------------------------
# POST /workspaces/  — create workspace (Admin only)
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    # Enforce Admin-only beyond the permission gate
    if current_user.role != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admins can create workspaces.",
        )

    workspace = Workspace(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        name=body.name,
        is_active=True,
    )
    db.add(workspace)

    # Add the creator as an Admin member of the new workspace
    membership = WorkspaceMember(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=current_user.id,
        role="Admin",
    )
    db.add(membership)
    await db.flush()

    logger.info(
        "workspace.created",
        workspace_id=str(workspace.id),
        tenant_id=str(ctx.tenant_id),
        created_by=str(current_user.id),
    )
    return WorkspaceOut.model_validate(workspace)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_workspace_added_email(
    to_email: str, invited_by_name: str, workspace_name: str, login_url: str
) -> None:
    """Notify an already-active user that they've been added to a new workspace."""
    from app.services.notification_providers import EmailProvider

    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#1e3a5f;">You've been added to a new workspace</h2>
      <p>{invited_by_name} has added you to the <strong>{workspace_name}</strong> workspace on Synthesis.</p>
      <p>Log in and use the workspace switcher in the top bar to access it.</p>
      <p style="margin:24px 0;">
        <a href="{login_url}"
           style="background:#1e3a5f;color:#fff;padding:12px 24px;border-radius:6px;
                  text-decoration:none;font-weight:600;">
          Go to Synthesis
        </a>
      </p>
    </div>
    """
    text_body = (
        f"{invited_by_name} has added you to the '{workspace_name}' workspace on Synthesis.\n\n"
        f"Log in and use the workspace switcher to access it:\n{login_url}"
    )

    provider = EmailProvider()
    await provider.send_email(
        to=to_email,
        subject=f"You've been added to {workspace_name} on Synthesis",
        html_body=html_body,
        text_body=text_body,
    )


async def _send_invite_email(to_email: str, invited_by_name: str, raw_token: str) -> None:
    from app.services.notification_providers import EmailProvider

    settings = get_settings()
    base_url = settings.frontend_url.rstrip("/")
    invite_url = f"{base_url}/accept-invite?token={raw_token}"

    html_body = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;">
      <h2 style="color:#1e3a5f;">You've been invited to Synthesis</h2>
      <p>{invited_by_name} has invited you to join their team on Synthesis.</p>
      <p>Click the button below to set up your password and access your account.
         This link expires in 7 days.</p>
      <p style="margin:24px 0;">
        <a href="{invite_url}"
           style="background:#1e3a5f;color:#fff;padding:12px 24px;border-radius:6px;
                  text-decoration:none;font-weight:600;">
          Accept invitation
        </a>
      </p>
      <p style="font-size:12px;color:#6b7280;">
        Or copy this link: {invite_url}
      </p>
    </div>
    """
    text_body = (
        f"You've been invited to Synthesis by {invited_by_name}.\n\n"
        f"Set up your password here (link expires in 7 days):\n{invite_url}"
    )

    provider = EmailProvider()
    await provider.send_email(
        to=to_email,
        subject="You've been invited to Synthesis",
        html_body=html_body,
        text_body=text_body,
    )


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


# ---------------------------------------------------------------------------
# GET /workspaces/{workspace_id}/members
# ---------------------------------------------------------------------------


@router.get(
    "/{workspace_id}/members",
    response_model=list[MemberOut],
    summary="List members of a workspace",
)
async def list_members(
    workspace_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.VIEW_ITEM)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> list[MemberOut]:
    # Admin and Member roles can list members; Viewer cannot access workspace mgmt.
    if current_user.role not in ("Admin", "Member"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admins and Members can list workspace members.",
        )

    await _get_workspace_or_404(workspace_id, ctx.tenant_id, db)

    result = await db.execute(
        select(WorkspaceMember, User)
        .join(User, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at)
    )
    rows = result.all()

    return [
        MemberOut(
            user_id=member.user_id,
            email=user.email,
            display_name=user.display_name or user.email,
            role=member.role,
            workspace_id=member.workspace_id,
            invite_pending=user.hashed_password is None and user.sso_subject is None,
        )
        for member, user in rows
    ]


# ---------------------------------------------------------------------------
# POST /workspaces/{workspace_id}/members  — invite by email (Admin only)
# ---------------------------------------------------------------------------


@router.post(
    "/{workspace_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a user to a workspace by email",
)
async def invite_member(
    workspace_id: uuid.UUID,
    body: InviteMemberRequest,
    current_user: User = Depends(require_permission(Permission.MANAGE_MEMBERS)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    workspace = await _get_workspace_or_404(workspace_id, ctx.tenant_id, db)

    # Check if a user with this email already exists in the tenant
    result = await db.execute(
        select(User).where(
            User.email == body.email,
            User.tenant_id == ctx.tenant_id,
        )
    )
    invited_user: User | None = result.scalar_one_or_none()
    is_existing_active_user = invited_user is not None and invited_user.is_active

    if invited_user is None:
        # Create a pending / placeholder user record (no password, no SSO subject)
        invited_user = User(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            email=body.email,
            display_name=body.email,
            hashed_password=None,
            sso_subject=None,
            role=body.role,
            is_active=False,  # becomes active on first login
        )
        db.add(invited_user)
        await db.flush()

    # Verify the user is not already a member
    existing_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == invited_user.id,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this workspace.",
        )

    membership = WorkspaceMember(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=invited_user.id,
        role=body.role,
    )
    db.add(membership)
    await db.flush()

    # Grant project access for each requested project_id
    if body.project_ids:
        projects_result = await db.execute(
            select(Project).where(
                Project.id.in_(body.project_ids),
                Project.workspace_id == workspace_id,
                Project.tenant_id == ctx.tenant_id,
                Project.is_active.is_(True),
            )
        )
        valid_ids = {p.id for p in projects_result.scalars().all()}
        for pid in body.project_ids:
            if pid not in valid_ids:
                continue
            db.add(UserProjectAccess(
                id=uuid.uuid4(),
                tenant_id=ctx.tenant_id,
                workspace_id=workspace_id,
                user_id=invited_user.id,
                project_id=pid,
                granted_by=current_user.id,
            ))
        await db.flush()

    settings = get_settings()
    inviter_name = current_user.display_name or current_user.email

    if is_existing_active_user:
        # User already has an account — just notify them, no password setup needed
        login_url = settings.frontend_url.rstrip("/") + "/login"
        try:
            await _send_workspace_added_email(
                to_email=body.email,
                invited_by_name=inviter_name,
                workspace_name=workspace.name,
                login_url=login_url,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace.added_email_failed", email=body.email, error=str(exc))
    else:
        # New user — issue a 7-day invite token so they can set their password
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now_utc = datetime.now(tz=timezone.utc)
        db.add(PasswordResetToken(
            id=uuid.uuid4(),
            token_hash=token_hash,
            token_type="invite",
            user_id=invited_user.id,
            expires_at=now_utc + timedelta(days=7),
        ))
        await db.flush()
        try:
            await _send_invite_email(
                to_email=body.email,
                invited_by_name=inviter_name,
                raw_token=raw_token,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("workspace.invite_email_failed", email=body.email, error=str(exc))

    logger.info(
        "workspace.member_invited",
        workspace_id=str(workspace_id),
        invited_email=body.email,
        invited_by=str(current_user.id),
        role=body.role,
    )

    return MemberOut(
        user_id=invited_user.id,
        email=invited_user.email,
        display_name=invited_user.display_name or invited_user.email,
        role=membership.role,
        workspace_id=workspace_id,
        invite_pending=not is_existing_active_user,
    )


# ---------------------------------------------------------------------------
# POST /workspaces/{workspace_id}/members/create — create active user (Admin)
# ---------------------------------------------------------------------------


@router.post(
    "/{workspace_id}/members/create",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new active user and add them to the workspace",
)
async def create_workspace_user(
    workspace_id: uuid.UUID,
    body: CreateUserRequest,
    current_user: User = Depends(require_permission(Permission.MANAGE_MEMBERS)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    await _get_workspace_or_404(workspace_id, ctx.tenant_id, db)

    # Reject if email already exists in this tenant
    existing = await db.execute(
        select(User).where(
            User.email == body.email,
            User.tenant_id == ctx.tenant_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists in the workspace.",
        )

    hashed_pw = _bcrypt.hashpw(body.password.encode(), _bcrypt.gensalt()).decode()

    new_user = User(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        email=body.email,
        display_name=body.display_name,
        hashed_password=hashed_pw,
        sso_subject=None,
        role=body.role,
        is_active=True,
    )
    db.add(new_user)
    await db.flush()

    membership = WorkspaceMember(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=new_user.id,
        role=body.role,
    )
    db.add(membership)
    await db.flush()

    # Grant project access for each requested project_id (validate ownership first)
    if body.project_ids:
        projects_result = await db.execute(
            select(Project).where(
                Project.id.in_(body.project_ids),
                Project.workspace_id == workspace_id,
                Project.tenant_id == ctx.tenant_id,
                Project.is_active.is_(True),
            )
        )
        valid_projects = projects_result.scalars().all()
        valid_ids = {p.id for p in valid_projects}

        for pid in body.project_ids:
            if pid not in valid_ids:
                continue  # silently skip unknown / cross-tenant IDs
            access = UserProjectAccess(
                id=uuid.uuid4(),
                tenant_id=ctx.tenant_id,
                workspace_id=workspace_id,
                user_id=new_user.id,
                project_id=pid,
                granted_by=current_user.id,
            )
            db.add(access)

        await db.flush()

    logger.info(
        "workspace.user_created",
        workspace_id=str(workspace_id),
        new_user_email=body.email,
        created_by=str(current_user.id),
        role=body.role,
        project_count=len(body.project_ids),
    )

    return MemberOut(
        user_id=new_user.id,
        email=new_user.email,
        display_name=new_user.display_name,
        role=membership.role,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# PATCH /workspaces/{workspace_id}/members/{user_id} — change role (Admin)
# ---------------------------------------------------------------------------


@router.patch(
    "/{workspace_id}/members/{member_user_id}",
    response_model=MemberOut,
    summary="Change a member's role in a workspace",
)
async def update_member_role(
    workspace_id: uuid.UUID,
    member_user_id: uuid.UUID,
    body: UpdateRoleRequest,
    current_user: User = Depends(require_permission(Permission.MANAGE_MEMBERS)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    await _get_workspace_or_404(workspace_id, ctx.tenant_id, db)

    # Fetch membership row
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_user_id,
        )
    )
    membership: WorkspaceMember | None = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this workspace.",
        )

    # Prevent the last Admin from demoting themselves
    if member_user_id == current_user.id and body.role != "Admin":
        # Count remaining admins — cast ENUM to String to avoid asyncpg type-mismatch
        admin_count_result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                cast(WorkspaceMember.role, String) == "Admin",
            )
        )
        admins = admin_count_result.scalars().all()
        if len(admins) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the only Admin in a workspace.",
            )

    old_role = membership.role
    membership.role = body.role
    db.add(membership)

    # Also sync the role on the User row for consistency
    user_result = await db.execute(
        select(User).where(
            User.id == member_user_id,
            User.tenant_id == ctx.tenant_id,
        )
    )
    target_user: User | None = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    target_user.role = body.role
    db.add(target_user)
    await db.flush()

    logger.info(
        "workspace.member_role_changed",
        workspace_id=str(workspace_id),
        user_id=str(member_user_id),
        old_role=old_role,
        new_role=body.role,
        changed_by=str(current_user.id),
    )

    return MemberOut(
        user_id=target_user.id,
        email=target_user.email,
        display_name=target_user.display_name or target_user.email,
        role=membership.role,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# DELETE /workspaces/{workspace_id}/members/{user_id} — remove member (Admin)
# ---------------------------------------------------------------------------


@router.delete(
    "/{workspace_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a workspace",
)
async def remove_member(
    workspace_id: uuid.UUID,
    member_user_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.MANAGE_MEMBERS)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_workspace_or_404(workspace_id, ctx.tenant_id, db)

    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == member_user_id,
        )
    )
    membership: WorkspaceMember | None = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this workspace.",
        )

    # Prevent removal of the last Admin
    if membership.role == "Admin":
        admin_count_result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                cast(WorkspaceMember.role, String) == "Admin",
            )
        )
        admins = admin_count_result.scalars().all()
        if len(admins) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the only Admin from a workspace.",
            )

    await db.delete(membership)
    await db.flush()

    logger.info(
        "workspace.member_removed",
        workspace_id=str(workspace_id),
        user_id=str(member_user_id),
        removed_by=str(current_user.id),
    )
