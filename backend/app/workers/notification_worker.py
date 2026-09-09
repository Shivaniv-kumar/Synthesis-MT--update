"""Celery beat tasks for the notification subsystem.

Scheduled tasks
---------------
tasks.check_due_soon       — daily at 09:00 UTC  (items due within 2 days)
tasks.check_overdue        — daily at 09:00 UTC  (items past due_date, not Done)
tasks.send_daily_digests   — every Monday at 08:00 UTC (open-item digest)

All tasks bridge into async code via ``asyncio.run``, the same pattern used by
``extraction_worker.py``.  A fresh ``AsyncSession`` is opened per-task inside
the async helper so SQLAlchemy's connection pool is not shared across OS threads.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta
from typing import Any

from celery.schedules import crontab
from sqlalchemy import select

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Register tasks with the beat schedule
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule.update(
    {
        "check-due-soon": {
            "task": "tasks.check_due_soon",
            "schedule": crontab(hour=9, minute=0),  # daily 09:00 UTC
        },
        "check-overdue": {
            "task": "tasks.check_overdue",
            "schedule": crontab(hour=9, minute=0),  # daily 09:00 UTC
        },
        "send-daily-digests": {
            "task": "tasks.send_daily_digests",
            "schedule": crontab(hour=8, minute=0, day_of_week=1),  # Monday 08:00 UTC
        },
    }
)

# Add notification tasks to the routing table
celery_app.conf.task_routes.update(
    {
        "tasks.check_due_soon": {"queue": "notifications"},
        "tasks.check_overdue": {"queue": "notifications"},
        "tasks.send_daily_digests": {"queue": "notifications"},
    }
)


# ---------------------------------------------------------------------------
# Async implementation helpers
# ---------------------------------------------------------------------------


async def _run_check_due_soon() -> dict[str, Any]:
    """Core async logic for the due-soon check task."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.notification import NotificationRule
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.services.notifications import NotificationService

    today = date.today()
    window_end = today + timedelta(days=2)

    service = NotificationService()
    total_notifications = 0

    async with AsyncSessionLocal() as session:
        # 1. Find all workspaces that have at least one active due_soon rule
        rules_result = await session.execute(
            select(NotificationRule).where(
                NotificationRule.rule_type == "due_soon",
                NotificationRule.is_active.is_(True),
            )
        )
        all_rules: list[NotificationRule] = list(rules_result.scalars().all())

        # Group rules by workspace_id
        workspace_rules: dict[uuid.UUID, list[NotificationRule]] = {}
        for rule in all_rules:
            workspace_rules.setdefault(rule.workspace_id, []).append(rule)

        for workspace_id, ws_rules in workspace_rules.items():
            # 2. Find items due within the window for this workspace
            items_result = await session.execute(
                select(ActionItem).where(
                    ActionItem.workspace_id == workspace_id,
                    ActionItem.due_date >= today,
                    ActionItem.due_date <= window_end,
                    ActionItem.status != "Done",
                    ActionItem.owner_user_id.is_not(None),
                )
            )
            items: list[ActionItem] = list(items_result.scalars().all())

            if not items:
                continue

            # 3. Group by owner
            owner_items: dict[uuid.UUID, list[ActionItem]] = {}
            for item in items:
                if item.owner_user_id:
                    owner_items.setdefault(item.owner_user_id, []).append(item)

            # 4. Notify each owner
            for owner_id, owner_item_list in owner_items.items():
                user_result = await session.execute(
                    select(User).where(User.id == owner_id, User.is_active.is_(True))
                )
                user: User | None = user_result.scalar_one_or_none()
                if user is None:
                    continue

                try:
                    await service.notify_due_soon(
                        items=owner_item_list,
                        user=user,
                        rules=ws_rules,
                        db=session,
                    )
                    total_notifications += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "notification_worker.due_soon_error",
                        user_id=str(owner_id),
                        workspace_id=str(workspace_id),
                        error=str(exc),
                    )

        await session.commit()

    logger.info(
        "notification_worker.check_due_soon_complete",
        total_notifications=total_notifications,
    )
    return {"total_notifications": total_notifications}


async def _run_check_overdue() -> dict[str, Any]:
    """Core async logic for the overdue check task."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.notification import NotificationRule
    from app.models.user import User
    from app.services.notifications import NotificationService

    today = date.today()
    service = NotificationService()
    total_notifications = 0

    async with AsyncSessionLocal() as session:
        # 1. Find active overdue rules grouped by workspace
        rules_result = await session.execute(
            select(NotificationRule).where(
                NotificationRule.rule_type == "overdue",
                NotificationRule.is_active.is_(True),
            )
        )
        all_rules: list[NotificationRule] = list(rules_result.scalars().all())

        workspace_rules: dict[uuid.UUID, list[NotificationRule]] = {}
        for rule in all_rules:
            workspace_rules.setdefault(rule.workspace_id, []).append(rule)

        for workspace_id, ws_rules in workspace_rules.items():
            # 2. Find overdue items in this workspace
            items_result = await session.execute(
                select(ActionItem).where(
                    ActionItem.workspace_id == workspace_id,
                    ActionItem.due_date < today,
                    ActionItem.status != "Done",
                    ActionItem.owner_user_id.is_not(None),
                )
            )
            items: list[ActionItem] = list(items_result.scalars().all())

            if not items:
                continue

            # 3. Group by owner
            owner_items: dict[uuid.UUID, list[ActionItem]] = {}
            for item in items:
                if item.owner_user_id:
                    owner_items.setdefault(item.owner_user_id, []).append(item)

            # 4. Notify each owner
            for owner_id, owner_item_list in owner_items.items():
                user_result = await session.execute(
                    select(User).where(User.id == owner_id, User.is_active.is_(True))
                )
                user: User | None = user_result.scalar_one_or_none()
                if user is None:
                    continue

                try:
                    await service.notify_overdue(
                        items=owner_item_list,
                        user=user,
                        rules=ws_rules,
                        db=session,
                    )
                    total_notifications += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "notification_worker.overdue_error",
                        user_id=str(owner_id),
                        workspace_id=str(workspace_id),
                        error=str(exc),
                    )

        await session.commit()

    logger.info(
        "notification_worker.check_overdue_complete",
        total_notifications=total_notifications,
    )
    return {"total_notifications": total_notifications}


async def _run_send_daily_digests() -> dict[str, Any]:
    """Core async logic for the weekly digest task."""
    from app.database import AsyncSessionLocal
    from app.models.action_item import ActionItem
    from app.models.notification import NotificationRule
    from app.models.user import User
    from app.models.workspace import WorkspaceMember
    from app.services.notifications import NotificationService

    service = NotificationService()
    total_digests = 0

    async with AsyncSessionLocal() as session:
        # 1. Find all active digest rules grouped by workspace
        rules_result = await session.execute(
            select(NotificationRule).where(
                NotificationRule.rule_type == "digest",
                NotificationRule.is_active.is_(True),
            )
        )
        all_rules: list[NotificationRule] = list(rules_result.scalars().all())

        workspace_rules: dict[uuid.UUID, list[NotificationRule]] = {}
        for rule in all_rules:
            workspace_rules.setdefault(rule.workspace_id, []).append(rule)

        for workspace_id, ws_rules in workspace_rules.items():
            # 2. Find all members of this workspace
            members_result = await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace_id,
                )
            )
            members: list[WorkspaceMember] = list(members_result.scalars().all())

            for member in members:
                # 3. Load the user
                user_result = await session.execute(
                    select(User).where(
                        User.id == member.user_id,
                        User.is_active.is_(True),
                    )
                )
                user: User | None = user_result.scalar_one_or_none()
                if user is None:
                    continue

                # 4. Check if user has any open items (send_digest also checks,
                #    but we skip here to avoid unnecessary session work)
                count_result = await session.execute(
                    select(ActionItem).where(
                        ActionItem.workspace_id == workspace_id,
                        ActionItem.owner_user_id == user.id,
                        ActionItem.status != "Done",
                    ).limit(1)
                )
                has_items = count_result.scalar_one_or_none() is not None
                if not has_items:
                    continue

                try:
                    await service.send_digest(
                        user=user,
                        workspace_id=workspace_id,
                        rules=ws_rules,
                        db=session,
                    )
                    total_digests += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "notification_worker.digest_error",
                        user_id=str(user.id),
                        workspace_id=str(workspace_id),
                        error=str(exc),
                    )

        await session.commit()

    logger.info(
        "notification_worker.send_daily_digests_complete",
        total_digests=total_digests,
    )
    return {"total_digests": total_digests}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="tasks.check_due_soon")
def check_due_soon() -> dict[str, Any]:
    """Celery task: notify users of action items due within 2 days.

    Runs daily at 09:00 UTC via the beat schedule.
    """
    logger.info("notification_worker.check_due_soon.start")
    try:
        return asyncio.run(_run_check_due_soon())
    except Exception as exc:
        logger.exception(
            "notification_worker.check_due_soon.failed",
            error=str(exc),
        )
        return {"error": str(exc)}


@celery_app.task(name="tasks.check_overdue")
def check_overdue() -> dict[str, Any]:
    """Celery task: notify users of overdue action items.

    Runs daily at 09:00 UTC via the beat schedule.
    """
    logger.info("notification_worker.check_overdue.start")
    try:
        return asyncio.run(_run_check_overdue())
    except Exception as exc:
        logger.exception(
            "notification_worker.check_overdue.failed",
            error=str(exc),
        )
        return {"error": str(exc)}


@celery_app.task(name="tasks.send_daily_digests")
def send_daily_digests() -> dict[str, Any]:
    """Celery task: send weekly open-item digest to all workspace members.

    Runs every Monday at 08:00 UTC via the beat schedule.
    """
    logger.info("notification_worker.send_daily_digests.start")
    try:
        return asyncio.run(_run_send_daily_digests())
    except Exception as exc:
        logger.exception(
            "notification_worker.send_daily_digests.failed",
            error=str(exc),
        )
        return {"error": str(exc)}
