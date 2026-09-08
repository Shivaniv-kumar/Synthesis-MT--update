"""Linear integration adapter.

Uses the Linear GraphQL API.  Authentication is via an API key sent directly
in the ``Authorization`` header (no "Bearer " prefix for personal API keys).

Config keys expected in ``config``:
    linear_api_key  — Linear API key (starts with "lin_api_…")
    team_id         — Linear team ID (UUID string)

Priority mapping (Linear integers):
    0 = No priority
    1 = Urgent
    2 = High
    3 = Medium
    4 = Low

We map local: High → 2 (High), Medium → 3 (Medium), Low → 4 (Low).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.models.action_item import ActionItem
from app.services.integrations.base import ExternalRef, PMIntegration

logger = logging.getLogger(__name__)

_LINEAR_GRAPHQL = "https://api.linear.app/graphql"

# ---------------------------------------------------------------------------
# Priority mapping
# ---------------------------------------------------------------------------


def _map_priority(priority: str) -> int:
    """Map local priority to Linear priority integer."""
    return {"High": 2, "Medium": 3, "Low": 4}.get(priority, 3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


async def _graphql(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    variables: dict[str, Any],
) -> dict[str, Any]:
    """Execute a GraphQL request and return the ``data`` payload.

    Raises ``httpx.HTTPStatusError`` on non-2xx HTTP responses and
    ``ValueError`` if the response contains GraphQL-level errors.
    """
    resp = await client.post(
        _LINEAR_GRAPHQL,
        headers=headers,
        json={"query": query, "variables": variables},
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise ValueError(f"Linear GraphQL errors: {body['errors']}")
    return body.get("data", {})


# ---------------------------------------------------------------------------
# GraphQL documents
# ---------------------------------------------------------------------------

_CREATE_ISSUE_MUTATION = """
mutation CreateIssue(
  $title: String!
  $description: String
  $teamId: String!
  $priority: Int
  $dueDate: TimelessDate
) {
  issueCreate(input: {
    title: $title
    description: $description
    teamId: $teamId
    priority: $priority
    dueDate: $dueDate
  }) {
    success
    issue {
      id
      url
    }
  }
}
"""

_UPDATE_ISSUE_MUTATION = """
mutation UpdateIssue($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue {
      id
      state {
        name
      }
    }
  }
}
"""

_GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id
    state {
      name
    }
  }
}
"""

_GET_WORKFLOW_STATES_QUERY = """
query GetWorkflowStates($teamId: String!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }) {
    nodes {
      id
      name
      type
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class LinearIntegration(PMIntegration):
    """Push/pull action items as Linear issues."""

    async def push_item(self, item: ActionItem, config: dict) -> ExternalRef:
        """Create a Linear issue for *item* and return its ID and URL."""
        api_key: str = config["linear_api_key"]
        team_id: str = config["team_id"]

        headers = _build_headers(api_key)

        variables: dict[str, Any] = {
            "title": item.task,
            "description": item.context or "",
            "teamId": team_id,
            "priority": _map_priority(item.priority),
        }
        if item.due_date is not None:
            variables["dueDate"] = item.due_date.isoformat()

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                data = await _graphql(client, headers, _CREATE_ISSUE_MUTATION, variables)
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "linear.push_item.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "response_text": exc.response.text,
                        "item_id": str(item.id),
                    },
                )
                raise
            except (httpx.HTTPError, ValueError) as exc:
                logger.error(
                    "linear.push_item.error",
                    extra={"error": str(exc), "item_id": str(item.id)},
                )
                raise

            result = data["issueCreate"]
            if not result.get("success"):
                raise ValueError(f"Linear issueCreate returned success=false for item {item.id}")

            issue = result["issue"]
            issue_id: str = issue["id"]
            issue_url: str = issue["url"]

            logger.info(
                "linear.push_item.success",
                extra={"issue_id": issue_id, "item_id": str(item.id)},
            )
            return ExternalRef(
                external_id=issue_id,
                external_url=issue_url,
                platform="linear",
            )

    async def update_item_status(
        self,
        external_ref: str,
        new_status: str,
        config: dict,
    ) -> None:
        """Transition a Linear issue to the workflow state matching *new_status*."""
        api_key: str = config["linear_api_key"]
        team_id: str = config["team_id"]

        headers = _build_headers(api_key)

        # Status name mappings to Linear workflow state types / names
        target_name_map = {
            "Open": "Todo",
            "In progress": "In Progress",
            "Done": "Done",
        }
        target_name = target_name_map.get(new_status, new_status)

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Fetch workflow states for the team
            try:
                states_data = await _graphql(
                    client,
                    headers,
                    _GET_WORKFLOW_STATES_QUERY,
                    {"teamId": team_id},
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.error(
                    "linear.update_status.fetch_states_error",
                    extra={"error": str(exc), "issue_id": external_ref},
                )
                raise

            states = states_data.get("workflowStates", {}).get("nodes", [])
            state_id: Optional[str] = None

            # Exact match first
            for state in states:
                if state.get("name", "").lower() == target_name.lower():
                    state_id = state["id"]
                    break

            # Substring fallback
            if state_id is None:
                for state in states:
                    if target_name.lower() in state.get("name", "").lower():
                        state_id = state["id"]
                        break

            if state_id is None:
                logger.warning(
                    "linear.update_status.state_not_found",
                    extra={
                        "issue_id": external_ref,
                        "target_name": target_name,
                        "available": [s.get("name") for s in states],
                    },
                )
                return

            # 2. Apply the state update
            try:
                data = await _graphql(
                    client,
                    headers,
                    _UPDATE_ISSUE_MUTATION,
                    {"id": external_ref, "stateId": state_id},
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.error(
                    "linear.update_status.mutation_error",
                    extra={"error": str(exc), "issue_id": external_ref},
                )
                raise

            if not data.get("issueUpdate", {}).get("success"):
                logger.warning(
                    "linear.update_status.mutation_not_successful",
                    extra={"issue_id": external_ref, "new_status": new_status},
                )
                return

            logger.info(
                "linear.update_status.success",
                extra={"issue_id": external_ref, "new_status": new_status},
            )

    async def get_item_status(
        self,
        external_ref: str,
        config: dict,
    ) -> Optional[str]:
        """Return the Linear issue's current state name, or None if not found."""
        api_key: str = config["linear_api_key"]
        headers = _build_headers(api_key)

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                data = await _graphql(
                    client,
                    headers,
                    _GET_ISSUE_QUERY,
                    {"id": external_ref},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                logger.error(
                    "linear.get_status.http_error",
                    extra={
                        "status_code": exc.response.status_code,
                        "issue_id": external_ref,
                    },
                )
                raise
            except httpx.HTTPError as exc:
                logger.error(
                    "linear.get_status.network_error",
                    extra={"error": str(exc), "issue_id": external_ref},
                )
                raise
            except ValueError as exc:
                # GraphQL errors — treat as not found (e.g. Entity not found)
                err_msg = str(exc).lower()
                if "not found" in err_msg or "not exist" in err_msg:
                    return None
                logger.error(
                    "linear.get_status.graphql_error",
                    extra={"error": str(exc), "issue_id": external_ref},
                )
                raise

            issue = data.get("issue")
            if issue is None:
                return None

            status_name: str = issue["state"]["name"]
            logger.debug(
                "linear.get_status.result",
                extra={"issue_id": external_ref, "status": status_name},
            )
            return status_name
