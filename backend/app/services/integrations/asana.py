"""Asana integration adapter.

Uses the Asana REST API v1.0.  Authentication is via a personal access token
(PAT) sent as a Bearer token.

Config keys expected in ``config``:
    asana_token     — Asana personal access token
    workspace_gid   — Asana workspace GID
    project_gid     — Asana project GID to add the task to
    owner_email     — (optional) email of the task assignee
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.models.action_item import ActionItem
from app.services.integrations.base import ExternalRef, PMIntegration

logger = logging.getLogger(__name__)

_ASANA_BASE = "https://app.asana.com/api/1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _lookup_assignee_gid(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    workspace_gid: str,
    owner_email: str,
) -> Optional[str]:
    """Return the Asana user GID for *owner_email* within *workspace_gid*."""
    url = f"{_ASANA_BASE}/workspaces/{workspace_gid}/typeahead"
    try:
        resp = await client.get(
            url,
            headers=headers,
            params={"resource_type": "user", "query": owner_email, "opt_fields": "gid,email"},
        )
        resp.raise_for_status()
        users = resp.json().get("data", [])
        # Find exact email match
        for user in users:
            if user.get("email", "").lower() == owner_email.lower():
                return user["gid"]
        # Fall back to first result
        if users:
            return users[0]["gid"]
    except httpx.HTTPError as exc:
        logger.warning(
            "asana.lookup_assignee.error",
            extra={"error": str(exc), "email": owner_email},
        )
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AsanaIntegration(PMIntegration):
    """Push/pull action items as Asana tasks."""

    async def push_item(self, item: ActionItem, config: dict) -> ExternalRef:
        """Create an Asana task for *item* and return its GID and permalink."""
        token: str = config["asana_token"]
        workspace_gid: str = config["workspace_gid"]
        project_gid: str = config["project_gid"]
        owner_email: Optional[str] = config.get("owner_email")

        headers = _build_headers(token)

        async with httpx.AsyncClient(timeout=30) as client:
            # Optionally resolve assignee
            assignee_gid: Optional[str] = None
            if owner_email:
                assignee_gid = await _lookup_assignee_gid(
                    client, headers, workspace_gid, owner_email
                )

            task_data: dict = {
                "workspace": workspace_gid,
                "projects": [project_gid],
                "name": item.task,
                "notes": item.context or "",
            }

            if item.due_date is not None:
                task_data["due_on"] = item.due_date.isoformat()

            if assignee_gid is not None:
                task_data["assignee"] = assignee_gid

            payload = {"data": task_data}

            try:
                resp = await client.post(
                    f"{_ASANA_BASE}/tasks",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "asana.push_item.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "item_id": str(item.id),
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "asana.push_item.network_error",
                    extra={"error": str(exc), "item_id": str(item.id)},
                )
                raise

            task = resp.json()["data"]
            task_gid: str = task["gid"]
            permalink_url: str = task.get("permalink_url", f"https://app.asana.com/0/{project_gid}/{task_gid}")

            logger.info(
                "asana.push_item.success",
                extra={"task_gid": task_gid, "item_id": str(item.id)},
            )
            return ExternalRef(
                external_id=task_gid,
                external_url=permalink_url,
                platform="asana",
            )

    async def update_item_status(
        self,
        external_ref: str,
        new_status: str,
        config: dict,
    ) -> None:
        """Mark the Asana task completed or incomplete based on *new_status*."""
        token: str = config["asana_token"]
        headers = _build_headers(token)

        completed = new_status == "Done"

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.put(
                    f"{_ASANA_BASE}/tasks/{external_ref}",
                    headers=headers,
                    json={"data": {"completed": completed}},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "asana.update_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "task_gid": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "asana.update_status.network_error",
                    extra={"error": str(exc), "task_gid": external_ref},
                )
                raise

            logger.info(
                "asana.update_status.success",
                extra={"task_gid": external_ref, "completed": completed},
            )

    async def get_item_status(
        self,
        external_ref: str,
        config: dict,
    ) -> Optional[str]:
        """Return ``"Done"`` or ``"Open"`` based on the Asana task's completed flag.

        Returns ``None`` if the task no longer exists.
        """
        token: str = config["asana_token"]
        headers = _build_headers(token)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{_ASANA_BASE}/tasks/{external_ref}",
                    headers=headers,
                    params={"opt_fields": "completed,name"},
                )
                if resp.status_code == 404:
                    logger.info(
                        "asana.get_status.not_found",
                        extra={"task_gid": external_ref},
                    )
                    return None
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                logger.error(
                    "asana.get_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "task_gid": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "asana.get_status.network_error",
                    extra={"error": str(exc), "task_gid": external_ref},
                )
                raise

            task = resp.json()["data"]
            status = "Done" if task.get("completed") else "Open"
            logger.debug(
                "asana.get_status.result",
                extra={"task_gid": external_ref, "status": status},
            )
            return status
