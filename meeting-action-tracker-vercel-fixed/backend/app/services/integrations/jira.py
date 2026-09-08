"""Jira Cloud integration adapter.

Uses the Jira REST API v3.  Authentication is Basic auth (email + API token).

Config keys expected in ``config``:
    jira_base_url   — e.g. "https://mycompany.atlassian.net"
    jira_email      — the Atlassian account email
    jira_api_token  — the API token generated at id.atlassian.com
    project_key     — Jira project key, e.g. "MAT"
    owner_email     — (optional) email of the assignee to look up
"""

from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from app.models.action_item import ActionItem
from app.services.integrations.base import ExternalRef, PMIntegration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

# Local status → Jira transition name
_STATUS_TO_JIRA: dict[str, str] = {
    "Open": "To Do",
    "In progress": "In Progress",
    "Done": "Done",
}

# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------


def _map_priority(priority: str) -> str:
    """Map local priority to Jira priority name (they happen to be identical)."""
    return {"High": "High", "Medium": "Medium", "Low": "Low"}.get(priority, "Medium")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _basic_auth_header(email: str, api_token: str) -> str:
    """Encode email:api_token as a Base64 Basic-auth header value."""
    raw = f"{email}:{api_token}"
    encoded = base64.b64encode(raw.encode()).decode()
    return f"Basic {encoded}"


def _build_headers(email: str, api_token: str) -> dict[str, str]:
    return {
        "Authorization": _basic_auth_header(email, api_token),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def _lookup_account_id(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    owner_email: str,
) -> Optional[str]:
    """Return the Jira accountId for *owner_email*, or None if not found."""
    url = f"{base_url}/rest/api/3/user/search"
    try:
        resp = await client.get(url, headers=headers, params={"query": owner_email})
        resp.raise_for_status()
        users = resp.json()
        if users:
            return users[0].get("accountId")
    except httpx.HTTPError as exc:
        logger.warning("jira.lookup_account_id.error", extra={"error": str(exc), "email": owner_email})
    return None


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class JiraIntegration(PMIntegration):
    """Push/pull action items as Jira Cloud issues."""

    async def push_item(self, item: ActionItem, config: dict) -> ExternalRef:
        """Create a Jira issue for *item* and return its key and URL."""
        base_url: str = config["jira_base_url"].rstrip("/")
        email: str = config["jira_email"]
        api_token: str = config["jira_api_token"]
        project_key: str = config["project_key"]
        owner_email: Optional[str] = config.get("owner_email")

        headers = _build_headers(email, api_token)

        async with httpx.AsyncClient(timeout=30) as client:
            # Optionally resolve assignee
            assignee_field: Optional[dict] = None
            if owner_email:
                account_id = await _lookup_account_id(client, headers, base_url, owner_email)
                if account_id:
                    assignee_field = {"accountId": account_id}

            # Build Atlassian Document Format description
            description = {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": item.context or "",
                            }
                        ],
                    }
                ],
            }

            fields: dict = {
                "project": {"key": project_key},
                "summary": item.task,
                "description": description,
                "priority": {"name": _map_priority(item.priority)},
            }

            if item.due_date is not None:
                fields["duedate"] = item.due_date.isoformat()

            if assignee_field is not None:
                fields["assignee"] = assignee_field

            payload = {"fields": fields}

            try:
                resp = await client.post(
                    f"{base_url}/rest/api/3/issue",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "jira.push_item.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "item_id": str(item.id),
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "jira.push_item.network_error",
                    extra={"error": str(exc), "item_id": str(item.id)},
                )
                raise

            data = resp.json()
            issue_key: str = data["key"]
            external_url = f"{base_url}/browse/{issue_key}"

            logger.info(
                "jira.push_item.success",
                extra={"issue_key": issue_key, "item_id": str(item.id)},
            )
            return ExternalRef(
                external_id=issue_key,
                external_url=external_url,
                platform="jira",
            )

    async def update_item_status(
        self,
        external_ref: str,
        new_status: str,
        config: dict,
    ) -> None:
        """Transition a Jira issue to the state matching *new_status*."""
        base_url: str = config["jira_base_url"].rstrip("/")
        email: str = config["jira_email"]
        api_token: str = config["jira_api_token"]

        target_name = _STATUS_TO_JIRA.get(new_status, new_status)
        headers = _build_headers(email, api_token)

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Fetch available transitions
            try:
                resp = await client.get(
                    f"{base_url}/rest/api/3/issue/{external_ref}/transitions",
                    headers=headers,
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.error(
                    "jira.update_status.fetch_transitions_error",
                    extra={"error": str(exc), "issue_key": external_ref},
                )
                raise

            transitions = resp.json().get("transitions", [])
            transition_id: Optional[str] = None
            for t in transitions:
                if t.get("name", "").lower() == target_name.lower():
                    transition_id = t["id"]
                    break

            if transition_id is None:
                # Try a case-insensitive substring match as a fallback
                for t in transitions:
                    if target_name.lower() in t.get("name", "").lower():
                        transition_id = t["id"]
                        break

            if transition_id is None:
                logger.warning(
                    "jira.update_status.transition_not_found",
                    extra={
                        "issue_key": external_ref,
                        "target_name": target_name,
                        "available": [t.get("name") for t in transitions],
                    },
                )
                return

            # 2. Execute the transition
            try:
                resp = await client.post(
                    f"{base_url}/rest/api/3/issue/{external_ref}/transitions",
                    headers=headers,
                    json={"transition": {"id": transition_id}},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "jira.update_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "issue_key": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "jira.update_status.network_error",
                    extra={"error": str(exc), "issue_key": external_ref},
                )
                raise

            logger.info(
                "jira.update_status.success",
                extra={"issue_key": external_ref, "new_status": new_status},
            )

    async def get_item_status(
        self,
        external_ref: str,
        config: dict,
    ) -> Optional[str]:
        """Return the current Jira status name, or None if the issue was deleted."""
        base_url: str = config["jira_base_url"].rstrip("/")
        email: str = config["jira_email"]
        api_token: str = config["jira_api_token"]

        headers = _build_headers(email, api_token)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(
                    f"{base_url}/rest/api/3/issue/{external_ref}",
                    headers=headers,
                    params={"fields": "status"},
                )
                if resp.status_code == 404:
                    logger.info(
                        "jira.get_status.not_found",
                        extra={"issue_key": external_ref},
                    )
                    return None
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                logger.error(
                    "jira.get_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "issue_key": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "jira.get_status.network_error",
                    extra={"error": str(exc), "issue_key": external_ref},
                )
                raise

            data = resp.json()
            status_name: str = data["fields"]["status"]["name"]
            logger.debug(
                "jira.get_status.result",
                extra={"issue_key": external_ref, "status": status_name},
            )
            return status_name
