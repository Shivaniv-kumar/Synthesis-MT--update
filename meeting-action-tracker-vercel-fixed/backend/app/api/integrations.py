"""Integration management API router.

Prefix: /integrations

Endpoints
---------
GET    /                       — list active IntegrationConfig rows for workspace (no credentials)
POST   /{platform}/configure   — create/replace an integration config (encrypts credentials)
PATCH  /{platform}/configure   — update target_config only
DELETE /{platform}             — deactivate (soft-delete) an integration
POST   /sync/{item_id}         — push one item to all active integrations now
POST   /sync/bulk              — enqueue background bulk-sync for all un-synced items
GET    /refs/{item_id}         — list ItemIntegrationRef rows for one item
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.database import get_db
from app.models.action_item import ActionItem
from app.models.integration_config import IntegrationConfig, ItemIntegrationRef
from app.models.user import User
from app.services.integrations.base import ExternalRef
from app.services.integrations.factory import get_integration
from app.utils.encryption import decrypt_credentials, encrypt_credentials

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

_VALID_PLATFORMS = {"jira", "asana", "linear", "clickup"}

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class IntegrationConfigOut(BaseModel):
    """Public representation of an IntegrationConfig (credentials omitted)."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    platform: str
    is_active: bool
    target_config: Optional[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConfigureIntegrationRequest(BaseModel):
    """Body for POST /{platform}/configure."""

    credentials: dict[str, Any] = Field(..., description="Platform credentials (will be encrypted)")
    target_config: dict[str, Any] = Field(default_factory=dict, description="Non-secret settings")


class UpdateTargetConfigRequest(BaseModel):
    """Body for PATCH /{platform}/configure."""

    target_config: dict[str, Any] = Field(..., description="Non-secret settings to update")


class ItemIntegrationRefOut(BaseModel):
    """Public representation of an ItemIntegrationRef."""

    id: uuid.UUID
    item_id: uuid.UUID
    integration_id: uuid.UUID
    external_id: str
    external_url: str
    platform: str
    sync_status: str
    last_synced_at: Optional[datetime]
    last_error: Optional[str]

    class Config:
        from_attributes = True


class SyncResultItem(BaseModel):
    platform: str
    status: str  # "synced" | "failed"
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    error: Optional[str] = None


class SyncResponse(BaseModel):
    item_id: uuid.UUID
    results: list[SyncResultItem]


class BulkSyncResponse(BaseModel):
    message: str
    task_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_platform(platform: str) -> str:
    p = platform.lower()
    if p not in _VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown platform '{platform}'. Valid values: {sorted(_VALID_PLATFORMS)}",
        )
    return p


def _config_to_out(cfg: IntegrationConfig) -> IntegrationConfigOut:
    return IntegrationConfigOut(
        id=cfg.id,
        workspace_id=cfg.workspace_id,
        platform=cfg.platform,
        is_active=cfg.is_active,
        target_config=cfg.target_config,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _ref_to_out(ref: ItemIntegrationRef) -> ItemIntegrationRefOut:
    return ItemIntegrationRefOut(
        id=ref.id,
        item_id=ref.item_id,
        integration_id=ref.integration_id,
        external_id=ref.external_id,
        external_url=ref.external_url,
        platform=ref.platform,
        sync_status=ref.sync_status,
        last_synced_at=ref.last_synced_at,
        last_error=ref.last_error,
    )


async def _get_active_config(
    workspace_id: uuid.UUID,
    platform: str,
    db: AsyncSession,
) -> Optional[IntegrationConfig]:
    """Return the active IntegrationConfig for *workspace_id* + *platform*, or None."""
    result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.workspace_id == workspace_id,
            IntegrationConfig.platform == platform,
            IntegrationConfig.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _push_item_to_config(
    item: ActionItem,
    config: IntegrationConfig,
    db: AsyncSession,
) -> SyncResultItem:
    """Push *item* to the external platform described by *config*.

    Creates or updates the corresponding ``ItemIntegrationRef`` row.
    Returns a ``SyncResultItem`` describing the outcome.
    """
    # Decrypt credentials
    try:
        credentials = decrypt_credentials(config.credentials_encrypted)
    except Exception as exc:
        logger.error(
            "integrations.push_item.decrypt_error",
            extra={"integration_id": str(config.id), "error": str(exc)},
        )
        return SyncResultItem(platform=config.platform, status="failed", error="credential decryption failed")

    # Merge credentials + target_config into one dict for the adapter
    merged_config: dict = {**(config.target_config or {}), **credentials}

    integration = get_integration(config.platform)

    # Find existing ref (if any)
    ref_result = await db.execute(
        select(ItemIntegrationRef).where(
            ItemIntegrationRef.item_id == item.id,
            ItemIntegrationRef.integration_id == config.id,
        )
    )
    existing_ref: Optional[ItemIntegrationRef] = ref_result.scalar_one_or_none()

    try:
        ext_ref: ExternalRef = await integration.push_item(item, merged_config)
    except Exception as exc:
        logger.error(
            "integrations.push_item.error",
            extra={
                "platform": config.platform,
                "item_id": str(item.id),
                "error": str(exc),
            },
        )
        # Record failure in ref
        now = datetime.now(tz=timezone.utc)
        if existing_ref is not None:
            existing_ref.sync_status = "failed"
            existing_ref.last_error = str(exc)
            existing_ref.last_synced_at = now
        else:
            db.add(
                ItemIntegrationRef(
                    id=uuid.uuid4(),
                    item_id=item.id,
                    integration_id=config.id,
                    external_id="",
                    external_url="",
                    platform=config.platform,
                    sync_status="failed",
                    last_synced_at=now,
                    last_error=str(exc),
                )
            )
        await db.flush()
        return SyncResultItem(platform=config.platform, status="failed", error=str(exc))

    # Success — upsert ref
    now = datetime.now(tz=timezone.utc)
    if existing_ref is not None:
        existing_ref.external_id = ext_ref.external_id
        existing_ref.external_url = ext_ref.external_url
        existing_ref.sync_status = "synced"
        existing_ref.last_synced_at = now
        existing_ref.last_error = None
    else:
        db.add(
            ItemIntegrationRef(
                id=uuid.uuid4(),
                item_id=item.id,
                integration_id=config.id,
                external_id=ext_ref.external_id,
                external_url=ext_ref.external_url,
                platform=ext_ref.platform,
                sync_status="synced",
                last_synced_at=now,
                last_error=None,
            )
        )
    await db.flush()

    logger.info(
        "integrations.push_item.synced",
        extra={
            "platform": config.platform,
            "item_id": str(item.id),
            "external_id": ext_ref.external_id,
        },
    )
    return SyncResultItem(
        platform=ext_ref.platform,
        status="synced",
        external_id=ext_ref.external_id,
        external_url=ext_ref.external_url,
    )


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=list[IntegrationConfigOut],
    summary="List active integration configs for the current workspace",
)
async def list_integrations(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[IntegrationConfigOut]:
    result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.workspace_id == ctx.workspace_id,
            IntegrationConfig.is_active.is_(True),
        )
    )
    configs = result.scalars().all()
    return [_config_to_out(c) for c in configs]


# ---------------------------------------------------------------------------
# POST /{platform}/configure
# ---------------------------------------------------------------------------


@router.post(
    "/{platform}/configure",
    response_model=IntegrationConfigOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create or replace an integration config (credentials encrypted at rest)",
)
async def configure_integration(
    platform: str,
    body: ConfigureIntegrationRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> IntegrationConfigOut:
    platform = _validate_platform(platform)

    # Deactivate any existing config for this workspace+platform first
    existing = await _get_active_config(ctx.workspace_id, platform, db)
    if existing is not None:
        existing.is_active = False
        await db.flush([existing])

    encrypted = encrypt_credentials(body.credentials)

    config = IntegrationConfig(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        platform=platform,
        is_active=True,
        credentials_encrypted=encrypted,
        target_config=body.target_config or {},
    )
    db.add(config)
    await db.flush([config])

    logger.info(
        "integrations.configured",
        extra={"platform": platform, "workspace_id": str(ctx.workspace_id)},
    )
    return _config_to_out(config)


# ---------------------------------------------------------------------------
# PATCH /{platform}/configure
# ---------------------------------------------------------------------------


@router.patch(
    "/{platform}/configure",
    response_model=IntegrationConfigOut,
    summary="Update the target_config of an existing integration",
)
async def update_integration_config(
    platform: str,
    body: UpdateTargetConfigRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> IntegrationConfigOut:
    platform = _validate_platform(platform)

    config = await _get_active_config(ctx.workspace_id, platform, db)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active {platform} integration found for this workspace.",
        )

    # Merge new keys into existing target_config
    existing_tc = config.target_config or {}
    config.target_config = {**existing_tc, **body.target_config}
    await db.flush([config])

    logger.info(
        "integrations.target_config_updated",
        extra={"platform": platform, "workspace_id": str(ctx.workspace_id)},
    )
    return _config_to_out(config)


# ---------------------------------------------------------------------------
# DELETE /{platform}
# ---------------------------------------------------------------------------


@router.delete(
    "/{platform}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate (soft-delete) an integration",
)
async def deactivate_integration(
    platform: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    platform = _validate_platform(platform)

    config = await _get_active_config(ctx.workspace_id, platform, db)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active {platform} integration found for this workspace.",
        )

    config.is_active = False
    await db.flush([config])

    logger.info(
        "integrations.deactivated",
        extra={"platform": platform, "workspace_id": str(ctx.workspace_id)},
    )


# ---------------------------------------------------------------------------
# POST /sync/{item_id}
# ---------------------------------------------------------------------------


@router.post(
    "/sync/{item_id}",
    response_model=SyncResponse,
    summary="Push one action item to all active integrations immediately",
)
async def sync_item(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SyncResponse:
    # Load item (tenant-scoped)
    item_result = await db.execute(
        select(ActionItem).where(
            ActionItem.id == item_id,
            ActionItem.tenant_id == ctx.tenant_id,
        )
    )
    item: Optional[ActionItem] = item_result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found.",
        )

    # Load active integrations for this workspace
    configs_result = await db.execute(
        select(IntegrationConfig).where(
            IntegrationConfig.workspace_id == ctx.workspace_id,
            IntegrationConfig.is_active.is_(True),
        )
    )
    configs = configs_result.scalars().all()

    if not configs:
        return SyncResponse(item_id=item_id, results=[])

    results: list[SyncResultItem] = []
    for config in configs:
        result = await _push_item_to_config(item, config, db)
        results.append(result)

    logger.info(
        "integrations.sync_item.complete",
        extra={
            "item_id": str(item_id),
            "platforms": [r.platform for r in results],
            "synced": sum(1 for r in results if r.status == "synced"),
            "failed": sum(1 for r in results if r.status == "failed"),
        },
    )
    return SyncResponse(item_id=item_id, results=results)


# ---------------------------------------------------------------------------
# POST /sync/bulk
# ---------------------------------------------------------------------------


@router.post(
    "/sync/bulk",
    response_model=BulkSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a background bulk-sync for all items without a synced ref",
)
async def sync_bulk(
    background_tasks: BackgroundTasks,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BulkSyncResponse:
    """Enqueue the Celery task that syncs all un-synced items in this workspace."""
    try:
        from app.workers.integration_sync_worker import sync_all_items_for_workspace

        task = sync_all_items_for_workspace.delay(str(ctx.workspace_id))
        task_id = task.id
    except Exception as exc:
        logger.error(
            "integrations.sync_bulk.enqueue_error",
            extra={"workspace_id": str(ctx.workspace_id), "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue bulk sync task.",
        )

    logger.info(
        "integrations.sync_bulk.enqueued",
        extra={"workspace_id": str(ctx.workspace_id), "task_id": task_id},
    )
    return BulkSyncResponse(
        message="Bulk sync task enqueued successfully.",
        task_id=task_id,
    )


# ---------------------------------------------------------------------------
# GET /refs/{item_id}
# ---------------------------------------------------------------------------


@router.get(
    "/refs/{item_id}",
    response_model=list[ItemIntegrationRefOut],
    summary="List external integration references for one action item",
)
async def list_item_refs(
    item_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ItemIntegrationRefOut]:
    # Verify the item belongs to this tenant
    item_result = await db.execute(
        select(ActionItem.id).where(
            ActionItem.id == item_id,
            ActionItem.tenant_id == ctx.tenant_id,
        )
    )
    if item_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found.",
        )

    refs_result = await db.execute(
        select(ItemIntegrationRef).where(
            ItemIntegrationRef.item_id == item_id,
        )
    )
    refs = refs_result.scalars().all()
    return [_ref_to_out(r) for r in refs]
