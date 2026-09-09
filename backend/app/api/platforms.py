"""Platform integration management API.

Prefix: /platforms

Handles OAuth connect flows, credential storage, manual sync triggering, and
disconnecting meeting platform integrations (Zoom, Teams, Google Meet).

All endpoints require MANAGE_WORKSPACE permission (Admin only).

Endpoints
---------
GET    /platforms/                             — list connected platforms for the workspace
GET    /platforms/{platform}/oauth/authorize   — return OAuth authorization URL
GET    /platforms/{platform}/oauth/callback    — exchange auth code, store tokens
POST   /platforms/{platform}/sync             — enqueue an immediate sync job
DELETE /platforms/{platform}                  — disconnect and delete config
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Any, Literal
from datetime import datetime, timezone

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import Permission, require_permission
from app.config import get_settings
from app.database import get_db
from app.middleware.tenant import TenantContext, get_tenant_context
from app.models import User
from app.models.platform_config import PlatformConfig, PlatformType
from app.utils.encryption import decrypt_credentials, encrypt_credentials

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/platforms", tags=["platforms"])

# Supported platform literal
SupportedPlatform = Literal["zoom", "teams", "google_meet"]

# ---------------------------------------------------------------------------
# Response schemas (no credentials_encrypted)
# ---------------------------------------------------------------------------


class PlatformConfigOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    platform: str
    is_active: bool
    last_sync_at: datetime | None
    sync_enabled: bool

    model_config = {"from_attributes": True}


class AuthorizeResponse(BaseModel):
    authorization_url: str


class SyncResponse(BaseModel):
    task_id: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_platform(platform: str) -> PlatformType:
    """Normalise and validate the platform path param, raising 400 on unknown."""
    try:
        return PlatformType(platform.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown platform '{platform}'. Must be one of: zoom, teams, google_meet.",
        )


async def _get_config_or_404(
    workspace_id: uuid.UUID,
    platform: PlatformType,
    db: AsyncSession,
) -> PlatformConfig:
    result = await db.execute(
        select(PlatformConfig).where(
            PlatformConfig.workspace_id == workspace_id,
            PlatformConfig.platform == platform,
        )
    )
    config: PlatformConfig | None = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {platform.value} integration found for this workspace.",
        )
    return config


def _build_zoom_auth_url(settings: Any, state: str) -> str:
    redirect_uri = f"{settings.frontend_url}/api/platforms/zoom/oauth/callback"
    return (
        "https://zoom.us/oauth/authorize"
        f"?response_type=code"
        f"&client_id={settings.zoom_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )


def _build_teams_auth_url(settings: Any, state: str) -> str:
    tenant_id = getattr(settings, "azure_tenant_id", "common")
    redirect_uri = f"{settings.frontend_url}/api/platforms/teams/oauth/callback"
    scopes = (
        "https://graph.microsoft.com/OnlineMeetings.Read "
        "https://graph.microsoft.com/OnlineMeetingTranscript.Read.All "
        "offline_access"
    )
    import urllib.parse
    return (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
        f"?response_type=code"
        f"&client_id={getattr(settings, 'azure_client_id', '')}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&scope={urllib.parse.quote(scopes, safe='')}"
        f"&state={state}"
        f"&response_mode=query"
    )


def _build_google_auth_url(settings: Any, state: str) -> str:
    redirect_uri = f"{settings.frontend_url}/api/platforms/google_meet/oauth/callback"
    scopes = (
        "https://www.googleapis.com/auth/meetings.space.readonly "
        "https://www.googleapis.com/auth/meetings.space.created "
        "https://www.googleapis.com/auth/drive.readonly"
    )
    import urllib.parse
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?response_type=code"
        f"&client_id={getattr(settings, 'google_client_id', '')}"
        f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
        f"&scope={urllib.parse.quote(scopes, safe='')}"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=consent"
    )


# ---------------------------------------------------------------------------
# Token exchange helpers
# ---------------------------------------------------------------------------


async def _exchange_zoom_code(code: str, settings: Any) -> dict:
    """Exchange a Zoom auth code for access + refresh tokens."""
    import base64
    redirect_uri = f"{settings.frontend_url}/api/platforms/zoom/oauth/callback"
    credentials = base64.b64encode(
        f"{settings.zoom_client_id}:{settings.zoom_client_secret}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://zoom.us/oauth/token",
            params={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Authorization": f"Basic {credentials}"},
        )
        resp.raise_for_status()
        token_data = resp.json()

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "client_id": settings.zoom_client_id,
        "client_secret": settings.zoom_client_secret,
    }


async def _exchange_teams_code(code: str, settings: Any) -> dict:
    """Exchange a Teams/Graph auth code for access + refresh tokens."""
    import urllib.parse
    tenant_id = getattr(settings, "azure_tenant_id", "common")
    redirect_uri = f"{settings.frontend_url}/api/platforms/teams/oauth/callback"
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": getattr(settings, "azure_client_id", ""),
                "client_secret": getattr(settings, "azure_client_secret", ""),
                "scope": (
                    "https://graph.microsoft.com/OnlineMeetings.Read "
                    "https://graph.microsoft.com/OnlineMeetingTranscript.Read.All "
                    "offline_access"
                ),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_data = resp.json()

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "tenant_azure_id": tenant_id,
        "client_id": getattr(settings, "azure_client_id", ""),
        "client_secret": getattr(settings, "azure_client_secret", ""),
    }


async def _exchange_google_code(code: str, settings: Any) -> dict:
    """Exchange a Google auth code for access + refresh tokens."""
    redirect_uri = f"{settings.frontend_url}/api/platforms/google_meet/oauth/callback"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": getattr(settings, "google_client_id", ""),
                "client_secret": getattr(settings, "google_client_secret", ""),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_data = resp.json()

    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "client_id": getattr(settings, "google_client_id", ""),
        "client_secret": getattr(settings, "google_client_secret", ""),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=list[PlatformConfigOut],
    summary="List connected meeting platforms for the current workspace",
)
async def list_platforms(
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> list[PlatformConfigOut]:
    """Return all platform integrations connected to the current workspace.

    Credentials are never returned — only metadata.
    """
    result = await db.execute(
        select(PlatformConfig).where(
            PlatformConfig.workspace_id == ctx.workspace_id,
        ).order_by(PlatformConfig.created_at)
    )
    configs = result.scalars().all()
    return [PlatformConfigOut.model_validate(c) for c in configs]


@router.get(
    "/{platform}/oauth/authorize",
    response_model=AuthorizeResponse,
    summary="Get the OAuth authorization URL for a meeting platform",
)
async def get_oauth_authorize_url(
    platform: str,
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
) -> AuthorizeResponse:
    """Return the OAuth authorization URL for the specified platform.

    The ``state`` parameter encodes ``{workspace_id}:{random}`` to allow the
    callback handler to identify the correct workspace.
    """
    platform_type = _validate_platform(platform)
    settings = get_settings()

    # Encode workspace context in the state token
    state = f"{ctx.workspace_id}:{secrets.token_urlsafe(16)}"

    if platform_type == PlatformType.zoom:
        url = _build_zoom_auth_url(settings, state)
    elif platform_type == PlatformType.teams:
        url = _build_teams_auth_url(settings, state)
    else:  # google_meet
        url = _build_google_auth_url(settings, state)

    logger.info(
        "platforms.oauth_authorize",
        platform=platform_type.value,
        workspace_id=str(ctx.workspace_id),
    )
    return AuthorizeResponse(authorization_url=url)


@router.get(
    "/{platform}/oauth/callback",
    summary="Handle OAuth callback: exchange code and store tokens",
)
async def oauth_callback(
    platform: str,
    code: str = Query(..., description="Authorization code from the OAuth provider"),
    state: str = Query(..., description="State token containing workspace context"),
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Exchange an OAuth authorization code for tokens and persist the config.

    Creates a new ``PlatformConfig`` row if one does not exist, or updates
    the credentials on an existing row (re-connect flow).
    """
    platform_type = _validate_platform(platform)
    settings = get_settings()

    # Exchange the code for tokens
    try:
        if platform_type == PlatformType.zoom:
            credentials = await _exchange_zoom_code(code, settings)
        elif platform_type == PlatformType.teams:
            credentials = await _exchange_teams_code(code, settings)
        else:  # google_meet
            credentials = await _exchange_google_code(code, settings)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "platforms.oauth_exchange_failed",
            platform=platform_type.value,
            status_code=exc.response.status_code,
            detail=exc.response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to exchange authorization code with {platform}: {exc.response.text}",
        )

    encrypted = encrypt_credentials(credentials)

    # Upsert PlatformConfig
    result = await db.execute(
        select(PlatformConfig).where(
            PlatformConfig.workspace_id == ctx.workspace_id,
            PlatformConfig.platform == platform_type,
        )
    )
    config: PlatformConfig | None = result.scalar_one_or_none()

    if config is None:
        config = PlatformConfig(
            id=uuid.uuid4(),
            workspace_id=ctx.workspace_id,
            tenant_id=ctx.tenant_id,
            platform=platform_type,
            is_active=True,
            credentials_encrypted=encrypted,
            sync_enabled=True,
        )
        db.add(config)
    else:
        config.credentials_encrypted = encrypted
        config.is_active = True

    await db.flush()

    logger.info(
        "platforms.oauth_connected",
        platform=platform_type.value,
        workspace_id=str(ctx.workspace_id),
        config_id=str(config.id),
    )

    return {
        "message": f"{platform} connected successfully.",
        "platform": platform_type.value,
        "config_id": str(config.id),
    }


@router.post(
    "/{platform}/sync",
    response_model=SyncResponse,
    summary="Enqueue an immediate recording sync for a platform",
)
async def trigger_sync(
    platform: str,
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Enqueue a Celery task to sync recordings from the specified platform.

    Returns immediately with the enqueued task ID.
    """
    platform_type = _validate_platform(platform)

    # Verify the config exists and is active
    config = await _get_config_or_404(ctx.workspace_id, platform_type, db)
    if not config.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The {platform} integration is not active.",
        )

    # Import here to avoid circular imports
    from app.workers.platform_sync_worker import sync_platform_recordings

    task = sync_platform_recordings.delay(
        str(ctx.workspace_id), platform_type.value
    )

    logger.info(
        "platforms.sync_enqueued",
        platform=platform_type.value,
        workspace_id=str(ctx.workspace_id),
        task_id=task.id,
    )

    return SyncResponse(
        task_id=task.id,
        message=f"Sync job enqueued for {platform}. Task ID: {task.id}",
    )


@router.delete(
    "/{platform}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect and delete a platform integration",
)
async def disconnect_platform(
    platform: str,
    current_user: User = Depends(require_permission(Permission.MANAGE_WORKSPACE)),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete the ``PlatformConfig`` row for the specified platform.

    This removes the stored credentials and disables future syncs.
    """
    platform_type = _validate_platform(platform)
    config = await _get_config_or_404(ctx.workspace_id, platform_type, db)

    await db.delete(config)
    await db.flush()

    logger.info(
        "platforms.disconnected",
        platform=platform_type.value,
        workspace_id=str(ctx.workspace_id),
        config_id=str(config.id),
        disconnected_by=str(current_user.id),
    )
