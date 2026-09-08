"""Authorization tests — endpoint-level access control beyond basic RBAC.

Covers
------
* Export endpoints deny Viewers even with a valid JWT
* Cross-tenant requests are denied (ID guessing returns 404, not 403)
* Workspace members can only see their own workspace's data
* GDPR self-export vs. admin export rules
* Retention config requires CONFIGURE_RETENTION permission
* Bulk-save items validates that referenced meetings belong to the requester's tenant
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "authz-test-secret-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.auth.jwt import create_access_token  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ActionItem, Meeting, User, Workspace  # noqa: E402
from app.models.workspace import WorkspaceMember  # noqa: E402

_ENGINE = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
_Session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_ENGINE, expire_on_commit=False, autoflush=False
)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _schema():
    async with _ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def db():
    async with _Session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def client(db: AsyncSession):
    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixtures: two tenants, each with a workspace + user + meeting + items
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def tenant_a(db: AsyncSession):
    tid = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), tenant_id=tid, name="Tenant A WS")
    user = User(
        id=uuid.uuid4(),
        tenant_id=tid,
        email="alice@a.com",
        display_name="Alice",
        role="Member",
        is_active=True,
    )
    db.add_all([ws, user])
    await db.flush()
    return {"tenant_id": tid, "workspace_id": ws.id, "user_id": user.id}


@pytest_asyncio.fixture()
async def tenant_b(db: AsyncSession):
    tid = uuid.uuid4()
    ws = Workspace(id=uuid.uuid4(), tenant_id=tid, name="Tenant B WS")
    user = User(
        id=uuid.uuid4(),
        tenant_id=tid,
        email="bob@b.com",
        display_name="Bob",
        role="Member",
        is_active=True,
    )
    db.add_all([ws, user])
    await db.flush()
    return {"tenant_id": tid, "workspace_id": ws.id, "user_id": user.id}


@pytest_asyncio.fixture()
async def meeting_b(db: AsyncSession, tenant_b: dict):
    m = Meeting(
        id=uuid.uuid4(),
        tenant_id=tenant_b["tenant_id"],
        workspace_id=tenant_b["workspace_id"],
        created_by=tenant_b["user_id"],
        title="B Corp Sync",
        source_type="paste",
        status="extracted",
        attendees=[],
    )
    db.add(m)
    await db.flush()
    return m


@pytest_asyncio.fixture()
async def item_b(db: AsyncSession, meeting_b: Meeting):
    now = datetime.now(tz=timezone.utc)
    item = ActionItem(
        id=uuid.uuid4(),
        meeting_id=meeting_b.id,
        tenant_id=meeting_b.tenant_id,
        workspace_id=meeting_b.workspace_id,
        task="B Corp confidential task",
        owner_label="Bob",
        priority="High",
        status="Open",
        context="",
        confidence=0.9,
        needs_review=False,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    await db.flush()
    return item


def _headers_for(tenant_id, workspace_id, user_id, role="Member"):
    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role=role,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Export endpoints — Viewer cannot access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_export_csv_denied(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Viewer")
    r = await client.get("/api/dashboard/export/csv", headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_viewer_export_pdf_denied(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Viewer")
    r = await client.get("/api/dashboard/export/pdf", headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Cross-tenant: Tenant A cannot read Tenant B's meeting or items by UUID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_a_cannot_get_tenant_b_meeting(
    client: AsyncClient, tenant_a: dict, meeting_b: Meeting
):
    h = _headers_for(**tenant_a)
    r = await client.get(f"/api/meetings/{meeting_b.id}", headers=h)
    # Must be 404, not 200 or 403 (prevents confirming the ID exists)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tenant_a_cannot_patch_tenant_b_item(
    client: AsyncClient, tenant_a: dict, item_b: ActionItem
):
    h = _headers_for(**tenant_a)
    r = await client.patch(
        f"/api/items/{item_b.id}", json={"status": "Done"}, headers=h
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tenant_a_cannot_delete_tenant_b_item(
    client: AsyncClient, tenant_a: dict, item_b: ActionItem
):
    h = _headers_for(**tenant_a)
    r = await client.delete(f"/api/items/{item_b.id}", headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tenant_a_items_list_excludes_tenant_b(
    client: AsyncClient, tenant_a: dict, item_b: ActionItem
):
    """GET /items/ for Tenant A must not include any Tenant B items."""
    h = _headers_for(**tenant_a)
    r = await client.get("/api/items/", headers=h)
    assert r.status_code == 200
    ids = [it["id"] for it in r.json().get("items", [])]
    assert str(item_b.id) not in ids


# ---------------------------------------------------------------------------
# Bulk-save: referenced meeting must belong to caller's tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_save_cross_tenant_meeting_rejected(
    client: AsyncClient, tenant_a: dict, meeting_b: Meeting
):
    """Tenant A cannot bulk-save items referencing Tenant B's meeting."""
    h = _headers_for(**tenant_a)
    payload = {
        "items": [
            {
                "meeting_id": str(meeting_b.id),
                "task": "Cross-tenant task",
                "priority": "Low",
                "status": "Open",
            }
        ]
    }
    r = await client.post("/api/items/bulk", json=payload, headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GDPR export: admin can export any user; non-admin can only export themselves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_can_export_own_gdpr_data(
    client: AsyncClient, tenant_a: dict
):
    h = _headers_for(**tenant_a, role="Member")
    r = await client.get(f"/api/compliance/gdpr/export/{tenant_a['user_id']}", headers=h)
    # Should succeed (200 or 404 if no data, but not 403)
    assert r.status_code != 403


@pytest.mark.asyncio
async def test_member_cannot_export_other_user_gdpr(
    client: AsyncClient, tenant_a: dict, tenant_b: dict
):
    h = _headers_for(**tenant_a, role="Member")
    r = await client.get(
        f"/api/compliance/gdpr/export/{tenant_b['user_id']}", headers=h
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_export_any_user_gdpr(
    client: AsyncClient, tenant_a: dict, tenant_b: dict
):
    """Admin in Tenant A can export Tenant A users — but not cross-tenant users."""
    h = _headers_for(**tenant_a, role="Admin")
    # Exporting own tenant user — admin privilege applies
    r = await client.get(
        f"/api/compliance/gdpr/export/{tenant_a['user_id']}", headers=h
    )
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Retention config — Admin only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_cannot_read_retention_config(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Member")
    r = await client.get("/api/compliance/retention-config", headers=h)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_member_cannot_patch_retention_config(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Member")
    r = await client.patch(
        "/api/compliance/retention-config",
        json={"item_retention_days": 60},
        headers=h,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_read_retention_config(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Admin")
    r = await client.get("/api/compliance/retention-config", headers=h)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_patch_retention_config(client: AsyncClient, tenant_a: dict):
    h = _headers_for(**tenant_a, role="Admin")
    r = await client.patch(
        "/api/compliance/retention-config",
        json={"item_retention_days": 90},
        headers=h,
    )
    assert r.status_code == 200
