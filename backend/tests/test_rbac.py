"""RBAC tests — verify role-based access control on every protected endpoint.

Rules under test
----------------
* Admin    — all permissions
* Member   — can create/edit meetings and items; can view dashboard and export;
             cannot manage workspace/members/retention
* Viewer   — can view items and dashboard; cannot create, edit, delete, or export
* Unauthenticated — always 401

Tests use JWTs minted directly (no DB required for auth enforcement) to keep
each case focused on the RBAC layer, not the service layer.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "rbac-test-secret-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.auth.jwt import create_access_token  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

_ENGINE = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
_SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
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
async def _db():
    async with _SessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def client(_db: AsyncSession):
    async def _override():
        yield _db

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

_TENANT = uuid.uuid4()
_WORKSPACE = uuid.uuid4()


def _token(role: str) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=_TENANT,
        workspace_id=_WORKSPACE,
        role=role,
    )


def _headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role)}"}


# ---------------------------------------------------------------------------
# Helper: assert status
# ---------------------------------------------------------------------------


def _expect(response, expected_status: int) -> None:
    assert response.status_code == expected_status, (
        f"Expected {expected_status}, got {response.status_code}: {response.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Unauthenticated — every protected endpoint returns 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauth_list_items(client: AsyncClient):
    r = await client.get("/api/items/")
    _expect(r, 401)


@pytest.mark.asyncio
async def test_unauth_list_meetings(client: AsyncClient):
    r = await client.get("/api/meetings/")
    _expect(r, 401)


@pytest.mark.asyncio
async def test_unauth_dashboard_summary(client: AsyncClient):
    r = await client.get("/api/dashboard/summary")
    _expect(r, 401)


@pytest.mark.asyncio
async def test_unauth_export_csv(client: AsyncClient):
    r = await client.get("/api/dashboard/export/csv")
    _expect(r, 401)


@pytest.mark.asyncio
async def test_unauth_export_pdf(client: AsyncClient):
    r = await client.get("/api/dashboard/export/pdf")
    _expect(r, 401)


# ---------------------------------------------------------------------------
# Viewer — can view items/meetings/dashboard; CANNOT create, edit, export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_can_list_items(client: AsyncClient):
    r = await client.get("/api/items/", headers=_headers("Viewer"))
    # 200 even if empty — permission is granted
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_viewer_can_list_meetings(client: AsyncClient):
    r = await client.get("/api/meetings/", headers=_headers("Viewer"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_viewer_can_view_dashboard(client: AsyncClient):
    r = await client.get("/api/dashboard/summary", headers=_headers("Viewer"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_viewer_cannot_export_csv(client: AsyncClient):
    r = await client.get("/api/dashboard/export/csv", headers=_headers("Viewer"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_export_pdf(client: AsyncClient):
    r = await client.get("/api/dashboard/export/pdf", headers=_headers("Viewer"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_create_meeting(client: AsyncClient):
    payload = {"title": "Test Meeting", "source_type": "paste"}
    r = await client.post("/api/meetings/", json=payload, headers=_headers("Viewer"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_patch_item(client: AsyncClient):
    item_id = uuid.uuid4()
    r = await client.patch(
        f"/api/items/{item_id}", json={"status": "Done"}, headers=_headers("Viewer")
    )
    # 403 from RBAC before any DB lookup
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_delete_item(client: AsyncClient):
    item_id = uuid.uuid4()
    r = await client.delete(f"/api/items/{item_id}", headers=_headers("Viewer"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_manage_workspace_members(client: AsyncClient):
    ws_id = uuid.uuid4()
    r = await client.get(f"/api/workspaces/{ws_id}/members", headers=_headers("Viewer"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_viewer_cannot_configure_retention(client: AsyncClient):
    r = await client.get("/api/compliance/retention-config", headers=_headers("Viewer"))
    _expect(r, 403)


# ---------------------------------------------------------------------------
# Member — can create/edit/view; CANNOT manage workspace, configure retention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_member_can_list_items(client: AsyncClient):
    r = await client.get("/api/items/", headers=_headers("Member"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_member_can_view_dashboard(client: AsyncClient):
    r = await client.get("/api/dashboard/summary", headers=_headers("Member"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_member_can_export_csv(client: AsyncClient):
    # Member has EXPORT_DATA — should reach the handler (200, not 403)
    r = await client.get("/api/dashboard/export/csv", headers=_headers("Member"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_member_can_export_pdf(client: AsyncClient):
    r = await client.get("/api/dashboard/export/pdf", headers=_headers("Member"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_member_cannot_manage_workspace_members(client: AsyncClient):
    ws_id = uuid.uuid4()
    r = await client.get(f"/api/workspaces/{ws_id}/members", headers=_headers("Member"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_member_cannot_configure_retention(client: AsyncClient):
    r = await client.get("/api/compliance/retention-config", headers=_headers("Member"))
    _expect(r, 403)


@pytest.mark.asyncio
async def test_member_cannot_view_audit_log(client: AsyncClient):
    r = await client.get("/api/compliance/audit-log", headers=_headers("Member"))
    _expect(r, 403)


# ---------------------------------------------------------------------------
# Admin — all permissions including admin-only routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_can_view_audit_log(client: AsyncClient):
    r = await client.get("/api/compliance/audit-log", headers=_headers("Admin"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_configure_retention(client: AsyncClient):
    r = await client.get("/api/compliance/retention-config", headers=_headers("Admin"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_export_csv(client: AsyncClient):
    r = await client.get("/api/dashboard/export/csv", headers=_headers("Admin"))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Role escalation — tampered role claim is rejected by signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tampered_role_in_jwt_rejected(client: AsyncClient):
    """A JWT with a manually-set role='Admin' but wrong signature is rejected."""
    import base64
    import json

    # Build a Viewer token then tamper the payload
    original = _token("Viewer")
    header_b64, payload_b64, sig = original.split(".")

    # Decode and alter role
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    claims["role"] = "Admin"
    tampered_payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )

    tampered_token = f"{header_b64}.{tampered_payload}.{sig}"
    headers = {"Authorization": f"Bearer {tampered_token}"}

    r = await client.get("/api/compliance/audit-log", headers=headers)
    # Invalid signature → 401, not 200
    _expect(r, 401)
