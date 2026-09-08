"""ClickUp integration adapter.

Uses the ClickUp REST API v2.  Authentication is via an API key sent directly
in the ``Authorization`` header.

Config keys expected in ``config``:
    clickup_api_key — ClickUp personal API key
    list_id         — ClickUp list ID to create tasks in
    owner_user_id   — (optional) ClickUp user ID integer to assign the task to

Priority mapping (ClickUp integers):
    1 = Urgent
    2 = High
    3 = Normal
    4 = Low

We map local: High → 2, Medium → 3, Low → 4.

Status mapping (ClickUp uses custom status strings per list/space):
    "Open"        → "to do"
    "In progress" → "in progress"
    "Done"        → "complete"
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Optional

import httpx

from app.models.action_item import ActionItem
from app.services.integrations.base import ExternalRef, PMIntegration

logger = logging.getLogger(__name__)

_CLICKUP_BASE = "https://api.clickup.com/api/v2"

# ---------------------------------------------------------------------------
# Priority / status mapping
# ---------------------------------------------------------------------------


def _map_priority(priority: str) -> int:
    """Map local priority to ClickUp priority integer."""
    return {"High": 2, "Medium": 3, "Low": 4}.get(priority, 3)


def _map_status(status: str) -> str:
    """Map local status to ClickUp status string."""
    return {
        "Open": "to do",
        "In progress": "in progress",
        "Done": "complete",
    }.get(status, "to do")


def _due_date_to_ms(due_date) -> Optional[int]:
    """Convert a ``date`` to a Unix timestamp in milliseconds (ClickUp format)."""
    if due_date is None:
        return None
    dt = datetime.combine(due_date, time(0, 0, 0), tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ClickUpIntegration(PMIntegration):
    """Push/pull action items as ClickUp tasks."""

    async def push_item(self, item: ActionItem, config: dict) -> ExternalRef:
        """Create a ClickUp task for *item* and return its ID and URL."""
        api_key: str = config["clickup_api_key"]
        list_id: str = config["list_id"]
        owner_user_id: Optional[int] = config.get("owner_user_id")

        headers = _build_headers(api_key)

        task_data: dict = {
            "name": item.task,
            "description": item.context or "",
            "priority": _map_priority(item.priority),
        }

        due_ms = _due_date_to_ms(item.due_date)
        if due_ms is not None:
            task_data["due_date"] = due_ms

        if owner_user_id is not None:
            task_data["assignees"] = [int(owner_user_id)]
        else:
            task_data["assignees"] = []

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    f"{_CLICKUP_BASE}/list/{list_id}/task",
                    headers=headers,
                    json=task_data,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "clickup.push_item.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "item_id": str(item.id),
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "clickup.push_item.network_error",
                    extra={"error": str(exc), "item_id": str(item.id)},
                )
                raise

            task = resp.json()
            task_id: str = task["id"]
            task_url: str = task.get("url", f"https://app.clickup.com/t/{task_id}")

            logger.info(
                "clickup.push_item.success",
                extra={"task_id": task_id, "item_id": str(item.id)},
            )
            return ExternalRef(
                external_id=task_id,
                external_url=task_url,
                platform="clickup",
            )

    async def update_item_status(
        self,
        external_ref: str,
        new_status: str,
        config: dict,
    ) -> None:
        """Update the ClickUp task status to match *new_status*."""
        api_key: str = config["clickup_api_key"]
        headers = _build_headers(api_key)

        mapped_status = _map_status(new_status)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.put(
                    f"{_CLICKUP_BASE}/task/{external_ref}",
                    headers=headers,
                    json={"status": mapped_status},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "clickup.update_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "task_id": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "clickup.update_status.network_error",
                    extra={"error": str(exc), "task_id": external_ref},
                )
                raise

            logger.info(
                "clickup.update_status.success",
                extra={"task_id": external_ref, "new_status": new_status},
            )

    async def get_item_status(
        self,
        external_ref: str,
        config: dict,
    ) -> Optional[str]:
        """Return the ClickUp task's current status string, or None if deleted."""
        api_key: str = config["clickup_api_key"]
        headers = _build_headers(api_key)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{_CLICKUP_BASE}/task/{external_ref}",
                    headers=headers,
                )
                if resp.status_code == 404:
                    logger.info(
                        "clickup.get_status.not_found",
                        extra={"task_id": external_ref},
                    )
                    return None
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                logger.error(
                    "clickup.get_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "task_id": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "clickup.get_status.network_error",
                    extra={"error": str(exc), "task_id": external_ref},
                )
                raise

            task = resp.json()
            status_status: str = task.get("status", {}).get("status", "")
            logger.debug(
                "clickup.get_status.result",
                extra={"task_id": external_ref, "status": status_status},
            )
            return status_status
