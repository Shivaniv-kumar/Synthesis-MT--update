"""Shared pytest fixtures for the Meeting Action Tracker test suite.

Database strategy: SQLite in-memory via aiosqlite for speed.  SQLAlchemy's
asyncio extension supports SQLite so all ORM code works unmodified.

We override the ``DATABASE_URL`` env var to point at SQLite *before* any app
module is imported so that the engine in ``app.database`` is never connected
to Postgres during tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Force SQLite in-memory BEFORE importing any app module that creates an engine
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Now safe to import app modules
from app.auth.jwt import create_access_token as _jwt_create_access_token  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ActionItem, Meeting, Tenant, User, Workspace  # noqa: E402

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Engine / session factory — SQLite in-memory, shared across the test process
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(
    _TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

_TestSession: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Session-scoped schema creation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema():
    """Create all tables once for the test session."""
    # SQLite does not support ARRAY — patch it out before metadata is reflected
    import sqlalchemy.dialects.postgresql as pg_dialect

    from sqlalchemy import String
    from sqlalchemy.types import TypeDecorator

    class _FakeArray(TypeDecorator):
        """Minimal stand-in for PostgreSQL ARRAY used in SQLite tests."""

        impl = String
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            return ",".join(value) if isinstance(value, list) else value

        def process_result_value(self, value, dialect):
            if value is None:
                return []
            return [v for v in value.split(",") if v] if value else []

    # Monkey-patch so that models using ARRAY compile against SQLite
    pg_dialect.ARRAY = _FakeArray  # type: ignore[attr-defined]

    # Re-import models to pick up patched ARRAY type
    import importlib

    import app.models as _models_mod

    importlib.reload(_models_mod)

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Per-test async session with rollback isolation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession and roll back after each test for isolation."""
    async with _TestSession() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Tenant / Workspace fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def test_tenant(async_session: AsyncSession) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), name="Test Corp")
    async_session.add(tenant)
    await async_session.flush()
    return tenant


@pytest_asyncio.fixture()
async def test_workspace(async_session: AsyncSession, test_tenant: Tenant) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), tenant_id=test_tenant.id, name="Main Workspace")
    async_session.add(ws)
    await async_session.flush()
    return ws


@pytest_asyncio.fixture()
async def other_tenant(async_session: AsyncSession) -> Tenant:
    tenant = Tenant(id=uuid.uuid4(), name="Other Corp")
    async_session.add(tenant)
    await async_session.flush()
    return tenant


@pytest_asyncio.fixture()
async def other_workspace(async_session: AsyncSession, other_tenant: Tenant) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), tenant_id=other_tenant.id, name="Other Workspace")
    async_session.add(ws)
    await async_session.flush()
    return ws


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def test_user(async_session: AsyncSession, test_tenant: Tenant) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="member@testcorp.com",
        hashed_password=_pwd.hash("password123"),
        full_name="Alice Member",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture()
async def test_admin(async_session: AsyncSession, test_tenant: Tenant) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email="admin@testcorp.com",
        hashed_password=_pwd.hash("adminpass"),
        full_name="Bob Admin",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    return user


@pytest_asyncio.fixture()
async def other_tenant_user(async_session: AsyncSession, other_tenant: Tenant) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="user@othercorp.com",
        hashed_password=_pwd.hash("password"),
        full_name="Charlie Other",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    return user


# ---------------------------------------------------------------------------
# Auth headers helpers
# ---------------------------------------------------------------------------


def auth_headers(user: User, workspace_id: uuid.UUID | None = None) -> dict[str, str]:
    """Return Authorization header dict for the given user.

    workspace_id defaults to user.tenant_id for tests that don't exercise
    workspace-membership enforcement (get_current_user only, not get_tenant_context).
    """
    wid = workspace_id or user.tenant_id
    token = _jwt_create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        workspace_id=wid,
        role=getattr(user, "role", "Member"),
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Meeting fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def sample_meeting(
    async_session: AsyncSession,
    test_tenant: Tenant,
    test_workspace: Workspace,
    test_user: User,
) -> Meeting:
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        workspace_id=test_workspace.id,
        created_by=test_user.id,
        title="Sprint Planning Q2",
        source_type="paste",
        occurred_at=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
        attendees=["Alice Member", "Bob Admin", "Jonathan Smith"],
        transcript_ref="Alice: Let's schedule the design review.\nBob: I'll prepare the deck.",
        status="draft",
    )
    async_session.add(meeting)
    await async_session.flush()
    return meeting


@pytest_asyncio.fixture()
async def other_meeting(
    async_session: AsyncSession,
    other_tenant: Tenant,
    other_workspace: Workspace,
    other_tenant_user: User,
) -> Meeting:
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        workspace_id=other_workspace.id,
        created_by=other_tenant_user.id,
        title="Other Corp Meeting",
        source_type="paste",
        transcript_ref="Some transcript",
        status="draft",
        attendees=[],
    )
    async_session.add(meeting)
    await async_session.flush()
    return meeting


# ---------------------------------------------------------------------------
# ActionItem fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def sample_items(
    async_session: AsyncSession,
    sample_meeting: Meeting,
) -> list[ActionItem]:
    now = datetime.now(tz=timezone.utc)
    items = [
        ActionItem(
            id=uuid.uuid4(),
            meeting_id=sample_meeting.id,
            tenant_id=sample_meeting.tenant_id,
            workspace_id=sample_meeting.workspace_id,
            task="Schedule design review",
            owner_label="Alice Member",
            priority="High",
            due_text="by next Friday",
            status="Open",
            context="Team agreed to review designs before sprint end",
            confidence=0.9,
            needs_review=False,
            created_at=now,
            updated_at=now,
        ),
        ActionItem(
            id=uuid.uuid4(),
            meeting_id=sample_meeting.id,
            tenant_id=sample_meeting.tenant_id,
            workspace_id=sample_meeting.workspace_id,
            task="Prepare presentation deck",
            owner_label="Bob Admin",
            priority="Medium",
            due_text="end of week",
            status="In progress",
            context="Bob committed to preparing the deck for stakeholders",
            confidence=0.85,
            needs_review=False,
            created_at=now,
            updated_at=now,
        ),
        ActionItem(
            id=uuid.uuid4(),
            meeting_id=sample_meeting.id,
            tenant_id=sample_meeting.tenant_id,
            workspace_id=sample_meeting.workspace_id,
            task="Send meeting notes to team",
            owner_label="Alice Member",
            priority="Low",
            due_text="today",
            status="Open",
            context="Notes should go out same day",
            confidence=0.4,  # below threshold → needs_review
            needs_review=True,
            created_at=now,
            updated_at=now,
        ),
    ]
    async_session.add_all(items)
    await async_session.flush()
    return items


# ---------------------------------------------------------------------------
# FastAPI test client with overridden DB dependency
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def test_client(async_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient backed by the in-memory SQLite session."""

    async def _override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
