"""Tenant provisioning and user management service.

Responsibilities:
- provision_tenant: Create a new tenant namespace (workspace) for an org.
- get_or_create_user: SSO user reconciliation — upsert with profile sync.
- validate_workspace_access: Guard to confirm membership before authorising actions.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


class TenantService:
    """Service layer for multi-tenant lifecycle operations.

    All methods are static/class-level — there is no instance state; callers
    pass the AsyncSession explicitly so the service integrates cleanly with
    FastAPI's dependency-injection pattern.
    """

    # ------------------------------------------------------------------
    # Tenant provisioning
    # ------------------------------------------------------------------

    @staticmethod
    async def provision_tenant(
        org_id: str,
        org_name: str,
        db: AsyncSession,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Bootstrap a new tenant with a default workspace.

        The ``tenant_id`` is a deterministic UUID v5 derived from the
        ``org_id`` string so that re-provisioning the same org is idempotent
        at the identifier level (the caller must still handle the case where
        the workspace already exists in the database).

        Args:
            org_id: Stable external identifier for the organisation (e.g. SSO
                    tenant/directory ID, Stripe customer ID).
            org_name: Human-readable name shown in the UI.
            db: Active async database session.

        Returns:
            ``(tenant_id, workspace_id)`` — both are UUIDs.
        """
        # Derive a stable tenant UUID from the external org identifier.
        tenant_id: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, org_id)

        # Create the default workspace for this tenant.
        workspace_id = uuid.uuid4()
        workspace = Workspace(
            id=workspace_id,
            tenant_id=tenant_id,
            name=f"{org_name} — Default",
            is_active=True,
        )
        db.add(workspace)
        await db.flush()

        return tenant_id, workspace_id

    # ------------------------------------------------------------------
    # SSO user reconciliation
    # ------------------------------------------------------------------

    @staticmethod
    async def get_or_create_user(
        sso_subject: str,
        email: str,
        display_name: str,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        db: AsyncSession,
    ) -> User:
        """Upsert a user identified by their SSO subject claim.

        Lookup order:
        1. SELECT by ``sso_subject`` (globally unique per identity provider).
        2. If found, sync ``email`` and ``display_name`` if they have changed.
        3. If not found, INSERT a new User with role=Member and add them to
           the target workspace as a Member.

        Args:
            sso_subject: Stable subject claim from the identity provider
                         (e.g. ``sub`` in an OIDC ID token).
            email: Current email from the identity provider.
            display_name: Current display name from the identity provider.
            tenant_id: Tenant the user belongs to.
            workspace_id: Workspace to add the user to on first creation.
            db: Active async database session.

        Returns:
            The User ORM object (existing or newly created).
        """
        # Try to find an existing user by SSO subject.
        stmt = select(User).where(User.sso_subject == sso_subject)
        result = await db.execute(stmt)
        user: Optional[User] = result.scalar_one_or_none()

        if user is not None:
            # Sync profile fields if the identity provider has updated them.
            changed = False
            if user.email != email:
                user.email = email
                changed = True
            if user.display_name != display_name:
                user.display_name = display_name
                changed = True
            if changed:
                db.add(user)
                await db.flush()
            return user

        # First-time login: create the user.
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=email,
            display_name=display_name,
            sso_subject=sso_subject,
            role="Member",
            is_active=True,
        )
        db.add(user)
        await db.flush()  # Obtain user.id before creating membership

        # Add to the target workspace as a Member.
        membership = WorkspaceMember(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user.id,
            role="Member",
        )
        db.add(membership)
        await db.flush()

        return user

    # ------------------------------------------------------------------
    # Workspace access guard
    # ------------------------------------------------------------------

    @staticmethod
    async def validate_workspace_access(
        user_id: uuid.UUID,
        workspace_id: uuid.UUID,
        db: AsyncSession,
    ) -> bool:
        """Check that a user has a WorkspaceMember record for the given workspace.

        This is a lightweight guard used at the API layer before delegating
        to repository methods.  It does NOT enforce role-level permissions —
        that is the responsibility of the router/policy layer.

        Args:
            user_id: User whose membership is being verified.
            workspace_id: Target workspace.
            db: Active async database session.

        Returns:
            ``True`` if a membership record exists, ``False`` otherwise.
        """
        stmt = select(WorkspaceMember).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.workspace_id == workspace_id,
        )
        result = await db.execute(stmt)
        member = result.scalar_one_or_none()
        return member is not None
