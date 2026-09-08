"""Celery beat tasks for the compliance and data-retention subsystem.

Scheduled tasks
---------------
tasks.apply_all_retention_policies  — daily at 02:00 UTC
    Iterates over all active workspaces and applies their configured
    data retention policy via ``RetentionService.apply_retention_policy``.

tasks.process_pending_erasures      — daily at 03:00 UTC
    Placeholder task.  In production, erasure requests would be queued to
    this task for deferred processing (e.g. during off-peak hours or after
    export windows close).  Currently, erasures are processed immediately
    and synchronously in the API handler, so this task only logs a
    confirmation.

Both tasks bridge into async code via ``asyncio.run``, the same pattern
used by ``extraction_worker.py`` and ``notification_worker.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery.schedules import crontab

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Register beat schedules
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule.update(
    {
        "apply-retention": {
            "task": "tasks.apply_all_retention_policies",
            "schedule": crontab(hour=2, minute=0),  # daily 02:00 UTC
        },
        "process-erasures": {
            "task": "tasks.process_pending_erasures",
            "schedule": crontab(hour=3, minute=0),  # daily 03:00 UTC
        },
    }
)

# Route compliance tasks to the default queue (no dedicated queue needed
# at this scale; add a "compliance" queue and update docker-compose if needed)
celery_app.conf.task_routes.update(
    {
        "tasks.apply_all_retention_policies": {"queue": "default"},
        "tasks.process_pending_erasures": {"queue": "default"},
    }
)


# ---------------------------------------------------------------------------
# Async implementation — apply_all_retention_policies
# ---------------------------------------------------------------------------


async def _run_apply_all_retention_policies() -> dict[str, Any]:
    """Async core: iterate workspaces and apply retention policies.

    Steps
    -----
    1. Query all active ``Workspace`` rows.
    2. For each workspace call ``RetentionService.apply_retention_policy``.
    3. Accumulate and return summary totals.
    """
    from app.database import AsyncSessionLocal
    from app.models.workspace import Workspace
    from app.services.retention import RetentionService
    from sqlalchemy import select

    service = RetentionService()

    total_transcripts_cleared = 0
    total_items_deleted = 0
    total_meetings_deleted = 0
    workspaces_processed = 0
    workspaces_failed = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.is_active.is_(True))
        )
        workspaces: list[Workspace] = list(result.scalars().all())

        logger.info(
            "compliance_worker.retention.start",
            workspace_count=len(workspaces),
        )

        for workspace in workspaces:
            try:
                # Each workspace gets its own nested transaction scope so a
                # single workspace failure does not abort the entire batch.
                async with AsyncSessionLocal() as ws_session:
                    summary = await service.apply_retention_policy(
                        workspace_id=workspace.id,
                        tenant_id=workspace.tenant_id,
                        db=ws_session,
                    )
                    await ws_session.commit()

                total_transcripts_cleared += summary.get("transcripts_cleared", 0)
                total_items_deleted += summary.get("items_deleted", 0)
                total_meetings_deleted += summary.get("meetings_deleted", 0)
                workspaces_processed += 1

                logger.debug(
                    "compliance_worker.retention.workspace_done",
                    workspace_id=str(workspace.id),
                    tenant_id=str(workspace.tenant_id),
                    transcripts_cleared=summary.get("transcripts_cleared", 0),
                    items_deleted=summary.get("items_deleted", 0),
                    meetings_deleted=summary.get("meetings_deleted", 0),
                )

            except Exception as exc:  # noqa: BLE001
                workspaces_failed += 1
                logger.error(
                    "compliance_worker.retention.workspace_error",
                    workspace_id=str(workspace.id),
                    tenant_id=str(workspace.tenant_id),
                    error=str(exc),
                    exc_info=True,
                )

    summary_log = {
        "workspaces_processed": workspaces_processed,
        "workspaces_failed": workspaces_failed,
        "total_transcripts_cleared": total_transcripts_cleared,
        "total_items_deleted": total_items_deleted,
        "total_meetings_deleted": total_meetings_deleted,
    }

    logger.info("compliance_worker.retention.complete", **summary_log)
    return summary_log


# ---------------------------------------------------------------------------
# Async implementation — process_pending_erasures (placeholder)
# ---------------------------------------------------------------------------


async def _run_process_pending_erasures() -> dict[str, Any]:
    """Placeholder: log that erasures are handled immediately in the API.

    In a real production system this task would:
    - Query an ErasureRequest queue table.
    - For each pending request call RetentionService.process_erasure_request.
    - Mark the request as completed and notify the DPO / data subject.

    For the current implementation, erasures are processed synchronously in
    the POST /compliance/gdpr/erasure API handler, so no queue processing is
    needed here.
    """
    logger.info(
        "compliance_worker.erasures.ran",
        message=(
            "Erasure worker ran — all erasures processed immediately via API. "
            "No pending queue items to process."
        ),
    )
    return {"status": "ok", "pending_erasures_processed": 0}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.apply_all_retention_policies",
    bind=False,
    max_retries=2,
    default_retry_delay=300,  # 5-minute retry delay
)
def apply_all_retention_policies() -> dict[str, Any]:
    """Daily task: apply data retention policies across all active workspaces.

    Runs at 02:00 UTC every day via the beat schedule.  Each workspace is
    processed in its own session so a single failure does not abort the batch.

    Returns a summary dict with aggregate counts of cleared/deleted data.
    """
    logger.info("compliance_worker.retention.task_start")
    try:
        return asyncio.run(_run_apply_all_retention_policies())
    except Exception as exc:
        logger.exception(
            "compliance_worker.retention.task_failed",
            error=str(exc),
        )
        return {
            "error": str(exc),
            "total_transcripts_cleared": 0,
            "total_items_deleted": 0,
            "total_meetings_deleted": 0,
        }


@celery_app.task(
    name="tasks.process_pending_erasures",
    bind=False,
)
def process_pending_erasures() -> dict[str, Any]:
    """Daily task: process any queued GDPR erasure requests.

    Runs at 03:00 UTC every day via the beat schedule.

    Currently a placeholder — erasures are processed immediately in the API
    handler (POST /compliance/gdpr/erasure).  This task exists so the beat
    schedule slot is reserved for when a proper erasure queue is implemented.
    """
    logger.info("compliance_worker.erasures.task_start")
    try:
        return asyncio.run(_run_process_pending_erasures())
    except Exception as exc:
        logger.exception(
            "compliance_worker.erasures.task_failed",
            error=str(exc),
        )
        return {"error": str(exc)}
