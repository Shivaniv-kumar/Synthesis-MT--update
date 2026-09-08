"""SSO / OIDC integration for the Meeting Action Tracker.

Supports two providers:
  - WorkOS  (SSO_PROVIDER=workos)
  - Auth0   (SSO_PROVIDER=auth0)

The factory ``get_oidc_provider(settings)`` returns the correct concrete
provider.  ``provision_user_from_oidc`` upserts a User row and—on first login
for a new org—creates a Tenant-scoped Workspace.
"""

from __future__ import annotations

import secrets
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models import User, Workspace, WorkspaceMember

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class OIDCUserInfo:
    """Normalised identity returned by any OIDC provider."""

    sub: str                       # stable, provider-scoped subject identifier
    email: str
    name: str
    org_id: Optional[str] = None   # organisation / connection identifier


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class OIDCProvider(ABC):
    """Common interface every provider must implement."""

    @abstractmethod
    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Return the URL the browser should be sent to for login."""

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> OIDCUserInfo:
        """Exchange an auth-code for normalised user info."""


# ---------------------------------------------------------------------------
# WorkOS provider
# ---------------------------------------------------------------------------


class WorkOSProvider(OIDCProvider):
    """WorkOS SSO integration using the WorkOS REST API.

    Required env vars:
        WORKOS_API_KEY      — server-side API key
        WORKOS_CLIENT_ID    — OAuth client ID shown in the WorkOS dashboard
    """

    _BASE_URL = "https://api.workos.com"

    def __init__(self, api_key: str, client_id: str) -> None:
        if not api_key:
            raise ValueError("WORKOS_API_KEY must be set when SSO_PROVIDER=workos")
        if not client_id:
            raise ValueError("WORKOS_CLIENT_ID must be set when SSO_PROVIDER=workos")
        self._api_key = api_key
        self._client_id = client_id

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the WorkOS authorization URL."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"{self._BASE_URL}/sso/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OIDCUserInfo:
        """POST to WorkOS /sso/token, then GET /sso/profile."""
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Exchange authorization code for access token
            token_resp = await client.post(
                f"{self._BASE_URL}/sso/token",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": self._client_id,
                    "client_secret": self._api_key,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            token_resp.raise_for_status()
            token_data: dict[str, Any] = token_resp.json()
            access_token: str = token_data["access_token"]

            # 2. Fetch the user profile
            profile_resp = await client.get(
                f"{self._BASE_URL}/sso/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_resp.raise_for_status()
            profile: dict[str, Any] = profile_resp.json()

        return OIDCUserInfo(
            sub=profile["id"],
            email=profile["email"],
            name=f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            or profile["email"],
            org_id=profile.get("organization_id") or profile.get("connection_id"),
        )


# ---------------------------------------------------------------------------
# Auth0 provider
# ---------------------------------------------------------------------------


class Auth0Provider(OIDCProvider):
    """Standard OIDC / Auth0 integration.

    Required env vars:
        AUTH0_DOMAIN          — e.g. ``your-tenant.us.auth0.com``
        AUTH0_CLIENT_ID       — OAuth2 client ID
        AUTH0_CLIENT_SECRET   — OAuth2 client secret
    """

    def __init__(self, domain: str, client_id: str, client_secret: str) -> None:
        if not domain:
            raise ValueError("AUTH0_DOMAIN must be set when SSO_PROVIDER=auth0")
        if not client_id:
            raise ValueError("AUTH0_CLIENT_ID must be set when SSO_PROVIDER=auth0")
        if not client_secret:
            raise ValueError("AUTH0_CLIENT_SECRET must be set when SSO_PROVIDER=auth0")
        # Strip any trailing slash / protocol so we can build URLs safely
        self._domain = domain.rstrip("/")
        if not self._domain.startswith("https://"):
            self._domain = f"https://{self._domain}"
        self._client_id = client_id
        self._client_secret = client_secret

    async def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
        return f"{self._domain}/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OIDCUserInfo:
        async with httpx.AsyncClient(timeout=15) as client:
            # 1. Exchange authorization code for tokens
            token_resp = await client.post(
                f"{self._domain}/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            token_resp.raise_for_status()
            token_data: dict[str, Any] = token_resp.json()
            access_token: str = token_data["access_token"]

            # 2. Fetch the user info from the OIDC userinfo endpoint
            userinfo_resp = await client.get(
                f"{self._domain}/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo: dict[str, Any] = userinfo_resp.json()

        # Auth0 exposes the organisation ID in the `org_id` claim (requires
        # the "Add Organization to tokens" action to be enabled).
        return OIDCUserInfo(
            sub=userinfo["sub"],
            email=userinfo["email"],
            name=userinfo.get("name") or userinfo.get("nickname") or userinfo["email"],
            org_id=userinfo.get("org_id"),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_oidc_provider(settings: Optional[Settings] = None) -> OIDCProvider:
    """Return the OIDC provider configured via SSO_PROVIDER env var.

    Reads SSO_PROVIDER from the environment (via pydantic-settings / .env).
    Supported values: ``"workos"``, ``"auth0"``.

    Raises:
        ValueError if SSO_PROVIDER is missing or not recognised.
    """
    import os

    if settings is None:
        settings = get_settings()

    provider_name = os.environ.get("SSO_PROVIDER", "").lower()
    if not provider_name:
        raise ValueError(
            "SSO_PROVIDER environment variable is required "
            "(set to 'workos' or 'auth0')"
        )

    if provider_name == "workos":
        return WorkOSProvider(
            api_key=os.environ.get("WORKOS_API_KEY", ""),
            client_id=os.environ.get("WORKOS_CLIENT_ID", ""),
        )

    if provider_name == "auth0":
        return Auth0Provider(
            domain=os.environ.get("AUTH0_DOMAIN", ""),
            client_id=os.environ.get("AUTH0_CLIENT_ID", ""),
            client_secret=os.environ.get("AUTH0_CLIENT_SECRET", ""),
        )

    raise ValueError(
        f"Unknown SSO_PROVIDER '{provider_name}'. "
        "Supported values: 'workos', 'auth0'"
    )


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


async def provision_user_from_oidc(
    oidc_info: OIDCUserInfo,
    db: AsyncSession,
) -> User:
    """Upsert a User from OIDC identity info.

    Algorithm:
    1. Look up existing user by ``sso_subject`` (stable provider subject).
    2. If found, refresh display_name / email and return.
    3. If not found, look up by email within any existing tenant (handles
       cases where a user was pre-invited before their first SSO login).
    4. If still not found, create a new tenant + default Workspace, then the
       User and a WorkspaceMember row.

    The caller is responsible for committing the session.
    """
    # --- 1. Look up by SSO subject ----------------------------------------
    result = await db.execute(
        select(User).where(User.sso_subject == oidc_info.sub)
    )
    existing_user: User | None = result.scalar_one_or_none()

    if existing_user is not None:
        # Refresh mutable fields that may have changed in the IdP
        existing_user.email = oidc_info.email
        existing_user.display_name = oidc_info.name or oidc_info.email
        db.add(existing_user)
        logger.info(
            "oidc.user_updated",
            user_id=str(existing_user.id),
            sub=oidc_info.sub,
        )
        return existing_user

    # --- 2. Look up by email (pre-invited user) ----------------------------
    result = await db.execute(
        select(User).where(User.email == oidc_info.email)
    )
    email_user: User | None = result.scalar_one_or_none()

    if email_user is not None:
        # Bind the SSO subject to the existing account
        email_user.sso_subject = oidc_info.sub
        email_user.display_name = oidc_info.name or oidc_info.email
        email_user.is_active = True
        db.add(email_user)
        logger.info(
            "oidc.user_linked",
            user_id=str(email_user.id),
            sub=oidc_info.sub,
        )
        return email_user

    # --- 3. Brand-new user: create tenant, workspace, user, member ---------
    tenant_id = uuid.uuid4()

    # Create a default workspace for this organisation
    workspace = Workspace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=f"{oidc_info.name or oidc_info.email}'s Workspace",
        is_active=True,
    )
    db.add(workspace)

    # Create the user record
    new_user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=oidc_info.email,
        display_name=oidc_info.name or oidc_info.email,
        sso_subject=oidc_info.sub,
        hashed_password=None,
        role="Admin",   # First user in a new org is the admin
        is_active=True,
    )
    db.add(new_user)

    # Add the user as a workspace member
    membership = WorkspaceMember(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=new_user.id,
        role="Admin",
    )
    db.add(membership)

    # Flush so that all IDs are populated but the session is not yet committed
    await db.flush()

    logger.info(
        "oidc.user_provisioned",
        user_id=str(new_user.id),
        tenant_id=str(tenant_id),
        workspace_id=str(workspace.id),
        sub=oidc_info.sub,
    )
    return new_user
