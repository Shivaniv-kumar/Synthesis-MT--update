"""Celery tasks for synchronising action items with external PM platforms.

Tasks
-----
tasks.sync_item_to_integrations
    Push a single action item to all active PM integrations for its workspace.
    Creates or updates the corresponding ``ItemIntegrationRef`` rows.

tasks.sync_all_items_for_workspace
    Bulk-push all items in a workspace that do not yet have a synced ref on
    each active integration.  Enqueued by the /sync/bulk API endpoint.

tasks.pull_status_updates
    Periodic task (every 15 minutes via beat) that polls every external
    platform for status changes.  If the external item is marked Done, the
    local ActionItem is updated accordingly.

All tasks bridge into async code via ``asyncio.run``, matching the pattern
used by the other workers in this project.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from celery.schedules import crontab

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Register beat schedule and task routing
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule.update(
    {
        "pull-status-updates": {
            "task": "tasks.pull_status_updates",
            "schedule": crontab(minute="*/15"),  # every 15 minutes
        },
    }
)

celery_app.conf.task_routes.update(
    {
        "tasks.sync_item_to_integrations": {"queue": "integrations"},
        "tasks.sync_all_items_for_workspace": {"queue": "integrations"},
        "tasks.pull_status_updates": {"queue": "integrations"},
    }
)

# ---------------------------------------------------------------------------
# Async implementation helpers
# ---------------------------------------------------------------------------


async def _run_sync_item_to_integrations(item_id: str) -> dict[str, Any]:
    """Core async logic: push one item to all active integrations."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.integration_config import IntegrationConfig, ItemIntegrationRef
    from app.services.integrations.factory import get_integration
    from app.utils.encryption import decrypt_credentials

    item_uuid = uuid.UUID(item_id)
    results: list[dict] = []

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        # Load the item
        item_result = await session.execute(
            select(ActionItem).where(ActionItem.id == item_uuid)
        )
        item: Optional[ActionItem] = item_result.scalar_one_or_none()
        if item is None:
            logger.warning(
                "integration_sync_worker.item_not_found",
                extra={"item_id": item_id},
            )
            return {"item_id": item_id, "error": "item not found", "results": []}

        # Load active integrations for this workspace
        configs_result = await session.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.workspace_id == item.workspace_id,
                IntegrationConfig.is_active.is_(True),
            )
        )
        configs = list(configs_result.scalars().all())

        if not configs:
            logger.info(
                "integration_sync_worker.no_active_integrations",
                extra={"item_id": item_id, "workspace_id": str(item.workspace_id)},
            )
            return {"item_id": item_id, "results": []}

        for config in configs:
            # Decrypt credentials
            try:
                credentials = decrypt_credentials(config.credentials_encrypted)
            except Exception as exc:
                logger.error(
                    "integration_sync_worker.decrypt_error",
                    extra={"integration_id": str(config.id), "error": str(exc)},
                )
                results.append({"platform": config.platform, "status": "failed", "error": "decrypt_failed"})
                continue

            merged_config = {**(config.target_config or {}), **credentials}
            integration = get_integration(config.platform)

            # Find existing ref
            ref_result = await session.execute(
                select(ItemIntegrationRef).where(
                    ItemIntegrationRef.item_id == item.id,
                    ItemIntegrationRef.integration_id == config.id,
                )
            )
            existing_ref: Optional[ItemIntegrationRef] = ref_result.scalar_one_or_none()

            now = datetime.now(tz=timezone.utc)

            try:
                ext_ref = await integration.push_item(item, merged_config)
            except Exception as exc:
                logger.error(
                    "integration_sync_worker.push_error",
                    extra={
                        "platform": config.platform,
                        "item_id": item_id,
                        "error": str(exc),
                    },
                )
                if existing_ref is not None:
                    existing_ref.sync_status = "failed"
                    existing_ref.last_error = str(exc)
                    existing_ref.last_synced_at = now
                else:
                    session.add(
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
                await session.flush()
                results.append({"platform": config.platform, "status": "failed", "error": str(exc)})
                continue

            # Success — upsert ref
            if existing_ref is not None:
                existing_ref.external_id = ext_ref.external_id
                existing_ref.external_url = ext_ref.external_url
                existing_ref.sync_status = "synced"
                existing_ref.last_synced_at = now
                existing_ref.last_error = None
            else:
                session.add(
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
            await session.flush()

            logger.info(
                "integration_sync_worker.push_success",
                extra={
                    "platform": config.platform,
                    "item_id": item_id,
                    "external_id": ext_ref.external_id,
                },
            )
            results.append({
                "platform": config.platform,
                "status": "synced",
                "external_id": ext_ref.external_id,
                "external_url": ext_ref.external_url,
            })

        await session.commit()

    return {"item_id": item_id, "results": results}


async def _run_sync_all_items_for_workspace(workspace_id: str) -> dict[str, Any]:
    """Bulk-push all un-synced items in *workspace_id* to every active integration."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.integration_config import IntegrationConfig, ItemIntegrationRef
    from app.services.integrations.factory import get_integration
    from app.utils.encryption import decrypt_credentials

    ws_uuid = uuid.UUID(workspace_id)
    total_synced = 0
    total_failed = 0

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        # Load active integrations
        configs_result = await session.execute(
            select(IntegrationConfig).where(
                IntegrationConfig.workspace_id == ws_uuid,
                IntegrationConfig.is_active.is_(True),
            )
        )
        configs = list(configs_result.scalars().all())

        if not configs:
            logger.info(
                "integration_sync_worker.bulk.no_active_integrations",
                extra={"workspace_id": workspace_id},
            )
            return {"workspace_id": workspace_id, "synced": 0, "failed": 0}

        # For each integration, find items that don't yet have a synced ref
        for config in configs:
            try:
                credentials = decrypt_credentials(config.credentials_encrypted)
            except Exception as exc:
                logger.error(
                    "integration_sync_worker.bulk.decrypt_error",
                    extra={"integration_id": str(config.id), "error": str(exc)},
                )
                continue

            merged_config = {**(config.target_config or {}), **credentials}
            integration = get_integration(config.platform)

            # Items in this workspace
            items_result = await session.execute(
                select(ActionItem).where(
                    ActionItem.workspace_id == ws_uuid,
                    ActionItem.status != "Done",
                )
            )
            all_items = list(items_result.scalars().all())

            # Filter to items without a synced ref for this integration
            for item in all_items:
                ref_result = await session.execute(
                    select(ItemIntegrationRef).where(
                        ItemIntegrationRef.item_id == item.id,
                        ItemIntegrationRef.integration_id == config.id,
                        ItemIntegrationRef.sync_status == "synced",
                    )
                )
                if ref_result.scalar_one_or_none() is not None:
                    # Already synced — skip
                    continue

                now = datetime.now(tz=timezone.utc)

                # Find any existing (failed/pending) ref to update
                existing_ref_result = await session.execute(
                    select(ItemIntegrationRef).where(
                        ItemIntegrationRef.item_id == item.id,
                        ItemIntegrationRef.integration_id == config.id,
                    )
                )
                existing_ref: Optional[ItemIntegrationRef] = existing_ref_result.scalar_one_or_none()

                try:
                    ext_ref = await integration.push_item(item, merged_config)
                except Exception as exc:
                    logger.error(
                        "integration_sync_worker.bulk.push_error",
                        extra={
                            "platform": config.platform,
                            "item_id": str(item.id),
                            "error": str(exc),
                        },
                    )
                    if existing_ref is not None:
                        existing_ref.sync_status = "failed"
                        existing_ref.last_error = str(exc)
                        existing_ref.last_synced_at = now
                    else:
                        session.add(
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
                    await session.flush()
                    total_failed += 1
                    continue

                if existing_ref is not None:
                    existing_ref.external_id = ext_ref.external_id
                    existing_ref.external_url = ext_ref.external_url
                    existing_ref.sync_status = "synced"
                    existing_ref.last_synced_at = now
                    existing_ref.last_error = None
                else:
                    session.add(
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
                await session.flush()
                total_synced += 1

        await session.commit()

    logger.info(
        "integration_sync_worker.bulk.complete",
        extra={
            "workspace_id": workspace_id,
            "synced": total_synced,
            "failed": total_failed,
        },
    )
    return {"workspace_id": workspace_id, "synced": total_synced, "failed": total_failed}


async def _run_pull_status_updates() -> dict[str, Any]:
    """Core async logic: poll external platforms and update local statuses."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.integration_config import IntegrationConfig, ItemIntegrationRef
    from app.services.integrations.factory import get_integration
    from app.utils.encryption import decrypt_credentials

    total_checked = 0
    total_updated = 0
    total_errors = 0

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        # Load all synced refs
        refs_result = await session.execute(
            select(ItemIntegrationRef).where(
                ItemIntegrationRef.sync_status == "synced",
            )
        )
        refs = list(refs_result.scalars().all())

        logger.info(
            "integration_sync_worker.pull_status.start",
            extra={"ref_count": len(refs)},
        )

        for ref in refs:
            total_checked += 1

            # Load the integration config
            config_result = await session.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.id == ref.integration_id,
                    IntegrationConfig.is_active.is_(True),
                )
            )
            config: Optional[IntegrationConfig] = config_result.scalar_one_or_none()

            if config is None:
                # Integration was deactivated — skip without error
                continue

            try:
                credentials = decrypt_credentials(config.credentials_encrypted)
            except Exception as exc:
                logger.error(
                    "integration_sync_worker.pull_status.decrypt_error",
                    extra={"integration_id": str(config.id), "error": str(exc)},
                )
                total_errors += 1
                continue

            merged_config = {**(config.target_config or {}), **credentials}
            integration = get_integration(config.platform)

            now = datetime.now(tz=timezone.utc)

            try:
                external_status = await integration.get_item_status(ref.external_id, merged_config)
            except Exception as exc:
                logger.error(
                    "integration_sync_worker.pull_status.fetch_error",
                    extra={
                        "platform": config.platform,
                        "external_id": ref.external_id,
                        "error": str(exc),
                    },
                )
                total_errors += 1
                continue

            ref.last_synced_at = now

            if external_status is None:
                # External item deleted — mark conflict so it can be reviewed
                ref.sync_status = "conflict"
                ref.last_error = "External item not found (may have been deleted)"
                await session.flush()
                logger.warning(
                    "integration_sync_worker.pull_status.item_deleted",
                    extra={
                        "platform": config.platform,
                        "external_id": ref.external_id,
                        "item_id": str(ref.item_id),
                    },
                )
                continue

            # Load the local action item
            item_result = await session.execute(
                select(ActionItem).where(ActionItem.id == ref.item_id)
            )
            item: Optional[ActionItem] = item_result.scalar_one_or_none()

            if item is None:
                # Item was deleted locally — nothing to update
                await session.flush()
                continue

            # Only auto-update local status when the external item is Done
            # to avoid overwriting deliberate local changes with external status
            external_is_done = external_status.lower() in ("done", "complete", "completed", "closed")

            if external_is_done and item.status != "Done":
                logger.info(
                    "integration_sync_worker.pull_status.marking_done",
                    extra={
                        "platform": config.platform,
                        "external_id": ref.external_id,
                        "item_id": str(item.id),
                        "external_status": external_status,
                    },
                )
                item.status = "Done"
                total_updated += 1

            await session.flush()

        await session.commit()

    logger.info(
        "integration_sync_worker.pull_status.complete",
        extra={
            "checked": total_checked,
            "updated": total_updated,
            "errors": total_errors,
        },
    )
    return {
        "checked": total_checked,
        "updated": total_updated,
        "errors": total_errors,
    }


# ---------------------------------------------------------------------------
# Celery task definitions
# ---------------------------------------------------------------------------


@celery_app.task(name="tasks.sync_item_to_integrations")
def sync_item_to_integrations(item_id: str) -> dict[str, Any]:
    """Push a single action item to all active PM integrations.

    Parameters
    ----------
    item_id:
        String representation of the ``ActionItem`` UUID.
    """
    logger.info(
        "integration_sync_worker.sync_item.start",
        extra={"item_id": item_id},
    )
    try:
        return asyncio.run(_run_sync_item_to_integrations(item_id))
    except Exception as exc:
        logger.exception(
            "integration_sync_worker.sync_item.failed",
            extra={"item_id": item_id, "error": str(exc)},
        )
        return {"item_id": item_id, "error": str(exc), "results": []}


@celery_app.task(name="tasks.sync_all_items_for_workspace")
def sync_all_items_for_workspace(workspace_id: str) -> dict[str, Any]:
    """Bulk-push all un-synced items in *workspace_id* to every active integration.

    Enqueued by ``POST /integrations/sync/bulk``.

    Parameters
    ----------
    workspace_id:
        String representation of the workspace UUID.
    """
    logger.info(
        "integration_sync_worker.sync_all.start",
        extra={"workspace_id": workspace_id},
    )
    try:
        return asyncio.run(_run_sync_all_items_for_workspace(workspace_id))
    except Exception as exc:
        logger.exception(
            "integration_sync_worker.sync_all.failed",
            extra={"workspace_id": workspace_id, "error": str(exc)},
        )
        return {"workspace_id": workspace_id, "error": str(exc), "synced": 0, "failed": 0}


@celery_app.task(name="tasks.pull_status_updates")
def pull_status_updates() -> dict[str, Any]:
    """Periodic task: poll external platforms for status changes.

    Runs every 15 minutes via the beat schedule.  When an external item is
    found to be Done, the local ``ActionItem.status`` is updated to ``"Done"``.
    """
    logger.info("integration_sync_worker.pull_status.start")
    try:
        return asyncio.run(_run_pull_status_updates())
    except Exception as exc:
        logger.exception(
            "integration_sync_worker.pull_status.failed",
            extra={"error": str(exc)},
        )
        return {"error": str(exc), "checked": 0, "updated": 0, "errors": 0}
