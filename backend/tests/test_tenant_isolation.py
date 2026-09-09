"""Tenant isolation tests for the repository layer.

These tests verify that the tenant-scoped repository methods enforce strict
data isolation — a user from Tenant A must never be able to read, enumerate,
or otherwise access records belonging to Tenant B, even when they know the
exact UUID.

Test database: SQLite in-memory (configured in conftest.py via aiosqlite).

Fixtures used from conftest.py:
  - async_session          — per-test async session with rollback isolation
  - test_tenant            — Tenant fixture (Tenant ORM object)
  - test_workspace         — Workspace in test_tenant
  - test_user              — User in test_tenant / test_workspace
  - other_tenant           — Second distinct Tenant
  - other_workspace        — Workspace in other_tenant
  - other_tenant_user      — User in other_tenant
  - sample_meeting         — Meeting in test_tenant / test_workspace
  - other_meeting          — Meeting in other_tenant / other_workspace
  - sample_items           — Three ActionItems attached to sample_meeting
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_item import ActionItem
from app.models.meeting import Meeting
from app.models.workspace import Workspace, WorkspaceMember
from app.repositories.base import TenantContext
from app.repositories.item_repo import ItemRepository
from app.repositories.meeting_repo import MeetingRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> TenantContext:
    """Build a TenantContext with an optional synthetic user_id."""
    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id or uuid.uuid4(),
    )


# ---------------------------------------------------------------------------
# Additional fixtures (complement conftest.py fixtures)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def second_workspace_in_test_tenant(
    async_session: AsyncSession,
    test_tenant,
    test_user,
) -> Workspace:
    """A second workspace inside test_tenant for cross-workspace isolation tests."""
    ws = Workspace(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        name="Second Workspace (same tenant)",
        is_active=True,
    )
    async_session.add(ws)
    await async_session.flush()
    return ws


@pytest_asyncio.fixture()
async def meeting_in_second_workspace(
    async_session: AsyncSession,
    test_tenant,
    second_workspace_in_test_tenant: Workspace,
    test_user,
) -> Meeting:
    """A meeting belonging to the second workspace within test_tenant."""
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        workspace_id=second_workspace_in_test_tenant.id,
        created_by=test_user.id,
        title="Second-workspace Meeting",
        source_type="paste",
        status="draft",
        attendees=[],
    )
    async_session.add(meeting)
    await async_session.flush()
    return meeting


@pytest_asyncio.fixture()
async def item_in_second_workspace(
    async_session: AsyncSession,
    test_tenant,
    second_workspace_in_test_tenant: Workspace,
    meeting_in_second_workspace: Meeting,
    test_user,
) -> ActionItem:
    """An ActionItem in the second workspace (same tenant as test_workspace)."""
    item = ActionItem(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        workspace_id=second_workspace_in_test_tenant.id,
        meeting_id=meeting_in_second_workspace.id,
        task="Task visible only in workspace 2",
        owner_label="Someone",
        priority="Medium",
        status="Open",
        context="",
        confidence=1.0,
        needs_review=False,
        created_by=test_user.id,
    )
    async_session.add(item)
    await async_session.flush()
    return item


# ---------------------------------------------------------------------------
# Test 1 — cross-tenant meeting list isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_a_cannot_see_tenant_b_meetings(
    async_session: AsyncSession,
    test_tenant,
    test_workspace,
    test_user,
    sample_meeting,   # belongs to test_tenant
    other_tenant,
    other_workspace,
    other_meeting,    # belongs to other_tenant
) -> None:
    """Listing meetings for Tenant A must never return Tenant B's records.

    Both tenants have exactly one meeting each.  Querying from Tenant A's
    context should return exactly 1 result (its own) and that result must
    not be the meeting owned by Tenant B.
    """
    repo = MeetingRepository(async_session)
    ctx_a = _make_ctx(test_tenant.id, test_workspace.id, test_user.id)

    meetings, total = await repo.list(ctx_a, workspace_id=test_workspace.id)

    assert total == 1, (
        f"Expected 1 meeting for Tenant A, got {total}. "
        "Cross-tenant leakage suspected."
    )
    assert len(meetings) == 1
    assert meetings[0].id == sample_meeting.id, (
        "The returned meeting should be Tenant A's sample_meeting."
    )
    assert meetings[0].tenant_id == test_tenant.id

    # Explicitly confirm the other tenant's meeting is absent
    returned_ids = {m.id for m in meetings}
    assert other_meeting.id not in returned_ids, (
        "Tenant B's meeting must not appear in Tenant A's listing."
    )


# ---------------------------------------------------------------------------
# Test 2 — UUID guessing / cross-tenant get_by_id returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_id_guessing_returns_404(
    async_session: AsyncSession,
    test_tenant,
    test_workspace,
    test_user,
    other_meeting,  # valid UUID — but belongs to other_tenant
) -> None:
    """Fetching another tenant's meeting by its valid UUID must return HTTP 404.

    This prevents an attacker from confirming that a UUID exists in another
    tenant's data (oracle / enumeration attack).  The repository must raise
    HTTPException(404) regardless of whether the record exists — never 403.
    """
    from fastapi import HTTPException

    repo = MeetingRepository(async_session)

    # Tenant A's context — trying to fetch Tenant B's meeting id
    ctx_a = _make_ctx(test_tenant.id, test_workspace.id, test_user.id)

    with pytest.raises(HTTPException) as exc_info:
        await repo.get_by_id(other_meeting.id, ctx_a)

    assert exc_info.value.status_code == 404, (
        "Repository must raise HTTP 404 (not 403) to avoid leaking existence "
        f"of cross-tenant records. Got: {exc_info.value.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 3 — cross-workspace isolation within the same tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_workspace_isolation(
    async_session: AsyncSession,
    test_tenant,
    test_workspace,
    test_user,
    sample_items,                         # items in test_workspace
    second_workspace_in_test_tenant,      # ws2 — same tenant
    item_in_second_workspace,             # item in ws2
) -> None:
    """A workspace-scoped query must not bleed items across workspaces.

    Both workspaces belong to the same tenant.  A user querying workspace 1
    must see only workspace 1's items; they must not see workspace 2's items,
    even though all records share the same tenant_id.
    """
    repo = ItemRepository(async_session)

    # Query scoped to test_workspace (workspace 1)
    items_ws1, total_ws1 = await repo.list_filtered(
        tenant_id=test_tenant.id,
        workspace_id=test_workspace.id,
    )

    # Query scoped to second_workspace (workspace 2)
    items_ws2, total_ws2 = await repo.list_filtered(
        tenant_id=test_tenant.id,
        workspace_id=second_workspace_in_test_tenant.id,
    )

    # Workspace 1 should contain only sample_items (3 items)
    assert total_ws1 == len(sample_items), (
        f"Expected {len(sample_items)} items in workspace 1, got {total_ws1}."
    )
    ws1_ids = {item.id for item in items_ws1}
    assert item_in_second_workspace.id not in ws1_ids, (
        "Workspace 2's item must not appear in Workspace 1 query results."
    )

    # Workspace 2 should contain only the one item we created there
    assert total_ws2 == 1, (
        f"Expected 1 item in workspace 2, got {total_ws2}."
    )
    assert items_ws2[0].id == item_in_second_workspace.id

    # Confirm no cross-contamination in either direction
    ws2_ids = {item.id for item in items_ws2}
    sample_ids = {item.id for item in sample_items}
    assert not ws1_ids.intersection(ws2_ids), (
        "No item should appear in both workspace query results."
    )
    assert not sample_ids.intersection(ws2_ids), (
        "Workspace 1's items must not appear in Workspace 2 results."
    )


# ---------------------------------------------------------------------------
# Test 4 — bulk_create stamps correct tenant context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_create_sets_tenant_context(
    async_session: AsyncSession,
    test_tenant,
    test_workspace,
    test_user,
    sample_meeting,
) -> None:
    """bulk_create must stamp every record with the caller-supplied tenant context.

    Even if the caller passes no tenant_id / workspace_id in the item dicts,
    the repository method must enforce the correct values from its parameters.
    This prevents a confused-deputy attack where malicious input dicts could
    plant records into a different tenant's namespace.
    """
    repo = ItemRepository(async_session)

    # Item dicts deliberately omit tenant_id and workspace_id —
    # the repository must fill them in from its parameters.
    raw_items = [
        {
            "id": uuid.uuid4(),
            "meeting_id": sample_meeting.id,
            "task": f"Bulk task {i}",
            "owner_label": "Automated",
            "priority": "Low",
            "status": "Open",
            "context": "Created via bulk_create",
            "confidence": 0.95,
            "needs_review": False,
        }
        for i in range(3)
    ]

    created = await repo.bulk_create(
        items=raw_items,
        tenant_id=test_tenant.id,
        workspace_id=test_workspace.id,
        created_by=test_user.id,
    )

    assert len(created) == 3, f"Expected 3 items, got {len(created)}."

    for item in created:
        assert item.tenant_id == test_tenant.id, (
            f"Item {item.id}: tenant_id {item.tenant_id!r} != "
            f"expected {test_tenant.id!r}."
        )
        assert item.workspace_id == test_workspace.id, (
            f"Item {item.id}: workspace_id {item.workspace_id!r} != "
            f"expected {test_workspace.id!r}."
        )
        assert item.created_by == test_user.id, (
            f"Item {item.id}: created_by {item.created_by!r} != "
            f"expected {test_user.id!r}."
        )

    # Verify persisted data is retrievable under the correct tenant scope
    items_in_db, total = await repo.list_filtered(
        tenant_id=test_tenant.id,
        workspace_id=test_workspace.id,
        meeting_id=sample_meeting.id,
    )
    # sample_meeting already has 0 or more items from other fixtures;
    # all returned items must belong to our tenant and workspace.
    for item in items_in_db:
        assert item.tenant_id == test_tenant.id
        assert item.workspace_id == test_workspace.id
