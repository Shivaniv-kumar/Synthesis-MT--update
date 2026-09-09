"""Tests for compliance, GDPR, and data retention functionality.

Coverage
--------
- test_retention_clears_old_transcript
    Creates a meeting older than the retention threshold and verifies that
    apply_retention_policy clears transcript_text.

- test_retention_deletes_old_items
    Creates action items on an old meeting and verifies they are deleted by
    the retention job.

- test_gdpr_erasure_anonymizes_user
    Calls process_erasure_request and asserts the user's PII is anonymised.

- test_gdpr_erasure_clears_item_ownership
    After erasure, action items previously owned by the user have
    owner_user_id=None and owner_label="[Deleted User]".

- test_audit_log_written_on_item_delete
    Calls write_audit_log directly and verifies the AuditLog row exists.

- test_retention_config_defaults
    Getting the config for a workspace with no row returns defaults (365/730/90).

All tests use an in-memory SQLite session for speed.  S3 calls are mocked via
``unittest.mock.patch`` so no real AWS credentials are needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# In-memory SQLite engine shared by all tests in this module
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_engine = create_async_engine(
    _TEST_DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

_Session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Patch PostgreSQL-specific types before any model import
# ---------------------------------------------------------------------------


def _patch_pg_types() -> None:
    """Replace PostgreSQL-only types with SQLite-compatible equivalents."""
    import sqlalchemy.dialects.postgresql as pg_dialect
    from sqlalchemy import String, Text
    from sqlalchemy.types import TypeDecorator

    class _FakeArray(TypeDecorator):
        impl = String
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            return ",".join(value) if isinstance(value, list) else value

        def process_result_value(self, value, dialect):
            if not value:
                return []
            return [v for v in value.split(",") if v]

    class _FakeJSONB(TypeDecorator):
        """Minimal JSONB stand-in backed by TEXT for SQLite."""
        import json as _json

        impl = Text
        cache_ok = True

        def process_bind_param(self, value, dialect):
            if value is None:
                return None
            import json
            return json.dumps(value)

        def process_result_value(self, value, dialect):
            if value is None:
                return None
            import json
            try:
                return json.loads(value)
            except Exception:
                return value

    pg_dialect.ARRAY = _FakeArray  # type: ignore[attr-defined]
    pg_dialect.JSONB = _FakeJSONB  # type: ignore[attr-defined]
    pg_dialect.UUID = String  # type: ignore[attr-defined]


_patch_pg_types()

# ---------------------------------------------------------------------------
# Now safe to import app models / services
# ---------------------------------------------------------------------------

from app.audit.logger import write_audit_log  # noqa: E402
from app.database import Base  # noqa: E402
from app.models.action_item import ActionItem  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.gdpr import DataRetentionConfig, GDPRAuditEvent  # noqa: E402
from app.models.meeting import Meeting  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.workspace import Workspace  # noqa: E402
from app.services.retention import RetentionService  # noqa: E402


# ---------------------------------------------------------------------------
# Schema creation fixture — runs once per session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema():
    """Create all ORM tables in the in-memory SQLite DB before any test runs."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Per-test session with rollback isolation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db() -> AsyncSession:
    """Yield an isolated AsyncSession that is rolled back after each test."""
    async with _Session() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_workspace_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_user_id() -> uuid.UUID:
    return uuid.uuid4()


async def _create_workspace(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
) -> Workspace:
    ws = Workspace(
        id=workspace_id or uuid.uuid4(),
        tenant_id=tenant_id,
        name="Test Workspace",
        is_active=True,
    )
    db.add(ws)
    await db.flush()
    return ws


async def _create_user(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    email: str = "user@test.com",
) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=email,
        display_name="Test User",
        hashed_password="hashed",
        is_active=True,
        role="Member",
    )
    db.add(user)
    await db.flush()
    return user


async def _create_old_meeting(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    days_old: int = 400,
    transcript_text: str | None = "This is a test transcript.",
    transcript_ref: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Meeting:
    """Create a meeting with created_at set *days_old* days in the past."""
    created_at = datetime.now(tz=timezone.utc) - timedelta(days=days_old)
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        title="Old Meeting",
        source_type="paste",
        transcript_text=transcript_text,
        transcript_ref=transcript_ref,
        status="extracted",
        created_at=created_at,
        updated_at=created_at,
        created_by=user_id,
    )
    db.add(meeting)
    await db.flush()
    return meeting


async def _create_action_item(
    db: AsyncSession,
    meeting: Meeting,
    owner_user_id: uuid.UUID | None = None,
    owner_label: str = "Test Owner",
) -> ActionItem:
    now = datetime.now(tz=timezone.utc)
    item = ActionItem(
        id=uuid.uuid4(),
        meeting_id=meeting.id,
        tenant_id=meeting.tenant_id,
        workspace_id=meeting.workspace_id,
        task="Test task",
        owner_user_id=owner_user_id,
        owner_label=owner_label,
        priority="Medium",
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


# ---------------------------------------------------------------------------
# Mock S3 storage
# ---------------------------------------------------------------------------


def _mock_storage():
    """Return a context manager that patches get_storage_service."""
    mock_svc = MagicMock()
    mock_svc.delete_file = AsyncMock(return_value=None)
    mock_svc.upload_audio = AsyncMock(return_value="fake/key")
    return patch(
        "app.services.retention.get_storage_service",
        return_value=mock_svc,
    )


# ===========================================================================
# Tests
# ===========================================================================


class TestRetentionClearsOldTranscript:
    """Retention job clears transcript_text on meetings older than threshold."""

    @pytest.mark.asyncio
    async def test_retention_clears_old_transcript(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()

        await _create_workspace(db, tenant_id, workspace_id)

        # Create a meeting 400 days old (past default 365-day transcript threshold)
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=400,
            transcript_text="Sensitive meeting content here.",
        )
        assert meeting.transcript_text is not None

        service = RetentionService()
        with _mock_storage():
            result = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        # Refresh from DB
        await db.refresh(meeting)
        assert meeting.transcript_text is None, "transcript_text should be cleared"
        assert result["transcripts_cleared"] >= 1

    @pytest.mark.asyncio
    async def test_recent_meeting_transcript_not_cleared(self, db: AsyncSession):
        """Meetings within the retention window are not touched."""
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        # Only 30 days old — well within the 365-day default
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=30,
            transcript_text="Recent transcript — should be kept.",
        )

        service = RetentionService()
        with _mock_storage():
            result = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        await db.refresh(meeting)
        assert meeting.transcript_text == "Recent transcript — should be kept."
        assert result["transcripts_cleared"] == 0


class TestRetentionDeletesOldItems:
    """Retention job deletes action items on meetings older than item_retention_days."""

    @pytest.mark.asyncio
    async def test_retention_deletes_old_items(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        # 800 days old — past default 730-day item retention threshold
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=800,
            transcript_text=None,
        )

        item = await _create_action_item(db, meeting)
        item_id = item.id

        service = RetentionService()
        with _mock_storage():
            result = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        assert result["items_deleted"] >= 1

        # Verify item no longer exists in the session
        still_there = await db.execute(
            select(ActionItem).where(ActionItem.id == item_id)
        )
        assert still_there.scalar_one_or_none() is None, "Action item should be deleted"

    @pytest.mark.asyncio
    async def test_retention_does_not_delete_items_on_recent_meetings(
        self, db: AsyncSession
    ):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        # 100 days old — within 730-day item threshold
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=100,
        )
        item = await _create_action_item(db, meeting)
        item_id = item.id

        service = RetentionService()
        with _mock_storage():
            result = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        assert result["items_deleted"] == 0
        still_there = await db.execute(
            select(ActionItem).where(ActionItem.id == item_id)
        )
        assert still_there.scalar_one_or_none() is not None


class TestGDPRErasureAnonymizesUser:
    """process_erasure_request anonymises the user's PII fields."""

    @pytest.mark.asyncio
    async def test_gdpr_erasure_anonymizes_user(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        admin = await _create_user(db, tenant_id, email="admin@corp.com")
        subject = await _create_user(db, tenant_id, email="victim@corp.com")
        subject_id = subject.id
        admin_id = admin.id

        service = RetentionService()
        await service.process_erasure_request(
            subject_user_id=subject_id,
            actor_user_id=admin_id,
            tenant_id=tenant_id,
            db=db,
        )

        # Re-fetch from DB (session was committed by process_erasure_request)
        async with _Session() as verify_session:
            result = await verify_session.execute(
                select(User).where(User.id == subject_id)
            )
            erased_user: User | None = result.scalar_one_or_none()

        assert erased_user is not None
        assert erased_user.email == "[deleted@erased.local]"
        assert erased_user.display_name == "[Deleted User]"
        assert erased_user.hashed_password is None
        assert erased_user.sso_subject is None
        assert erased_user.is_active is False

    @pytest.mark.asyncio
    async def test_gdpr_erasure_writes_audit_event(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        admin = await _create_user(db, tenant_id, email="a2@corp.com")
        subject = await _create_user(db, tenant_id, email="s2@corp.com")

        service = RetentionService()
        await service.process_erasure_request(
            subject_user_id=subject.id,
            actor_user_id=admin.id,
            tenant_id=tenant_id,
            db=db,
        )

        async with _Session() as verify_session:
            result = await verify_session.execute(
                select(GDPRAuditEvent).where(
                    GDPRAuditEvent.subject_user_id == subject.id,
                    GDPRAuditEvent.event_type == "erasure",
                )
            )
            event: GDPRAuditEvent | None = result.scalar_one_or_none()

        assert event is not None
        assert event.actor_user_id == admin.id


class TestGDPRErasureClearsItemOwnership:
    """After erasure, items owned by the subject are de-linked."""

    @pytest.mark.asyncio
    async def test_gdpr_erasure_clears_item_ownership(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        admin = await _create_user(db, tenant_id, email="admin3@corp.com")
        subject = await _create_user(db, tenant_id, email="s3@corp.com")

        # Meeting and item owned by subject
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=10,
            user_id=subject.id,
        )
        item = await _create_action_item(
            db,
            meeting=meeting,
            owner_user_id=subject.id,
            owner_label="Original Owner",
        )
        item_id = item.id

        service = RetentionService()
        await service.process_erasure_request(
            subject_user_id=subject.id,
            actor_user_id=admin.id,
            tenant_id=tenant_id,
            db=db,
        )

        async with _Session() as verify_session:
            result = await verify_session.execute(
                select(ActionItem).where(ActionItem.id == item_id)
            )
            updated_item: ActionItem | None = result.scalar_one_or_none()

        assert updated_item is not None
        assert updated_item.owner_user_id is None
        assert updated_item.owner_label == "[Deleted User]"


class TestAuditLogWrittenOnItemDelete:
    """write_audit_log inserts an AuditLog row."""

    @pytest.mark.asyncio
    async def test_audit_log_written_on_item_delete(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        entity_id = uuid.uuid4()
        actor_id = uuid.uuid4()

        await write_audit_log(
            db=db,
            actor_user_id=actor_id,
            tenant_id=tenant_id,
            entity_type="action_item",
            entity_id=entity_id,
            action="delete",
            before={"status": "Open"},
            after=None,
        )
        await db.flush()

        result = await db.execute(
            select(AuditLog).where(
                AuditLog.entity_id == entity_id,
                AuditLog.action == "delete",
            )
        )
        entry: AuditLog | None = result.scalar_one_or_none()

        assert entry is not None
        assert entry.entity_type == "action_item"
        assert entry.actor_user_id == actor_id
        assert entry.tenant_id == tenant_id

    @pytest.mark.asyncio
    async def test_audit_log_system_actor_nullable(self, db: AsyncSession):
        """actor_user_id can be None for system-triggered events."""
        tenant_id = _make_tenant_id()
        entity_id = uuid.uuid4()

        await write_audit_log(
            db=db,
            actor_user_id=None,
            tenant_id=tenant_id,
            entity_type="meeting",
            entity_id=entity_id,
            action="delete",
        )
        await db.flush()

        result = await db.execute(
            select(AuditLog).where(AuditLog.entity_id == entity_id)
        )
        entry: AuditLog | None = result.scalar_one_or_none()
        assert entry is not None
        assert entry.actor_user_id is None


class TestRetentionConfigDefaults:
    """get_or_create returns sensible defaults when no config row exists."""

    @pytest.mark.asyncio
    async def test_retention_config_defaults(self, db: AsyncSession):
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        # No DataRetentionConfig row exists for this workspace
        result = await db.execute(
            select(DataRetentionConfig).where(
                DataRetentionConfig.workspace_id == workspace_id,
            )
        )
        assert result.scalar_one_or_none() is None

        # Calling apply_retention_policy with no config should use defaults
        service = RetentionService()
        with _mock_storage():
            summary = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        # The call should succeed and return zero counts (no old data)
        assert summary["transcripts_cleared"] == 0
        assert summary["items_deleted"] == 0
        assert summary["meetings_deleted"] == 0

    @pytest.mark.asyncio
    async def test_custom_retention_config_respected(self, db: AsyncSession):
        """A DataRetentionConfig row with short thresholds is respected."""
        tenant_id = _make_tenant_id()
        workspace_id = _make_workspace_id()
        await _create_workspace(db, tenant_id, workspace_id)

        # Set very short retention: 30 days for transcripts
        config = DataRetentionConfig(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            transcript_retention_days=30,
            item_retention_days=60,
            audio_retention_days=30,
        )
        db.add(config)
        await db.flush()

        # Create a meeting 45 days old — older than 30-day transcript threshold
        meeting = await _create_old_meeting(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            days_old=45,
            transcript_text="Should be cleared by 30-day config.",
        )

        service = RetentionService()
        with _mock_storage():
            summary = await service.apply_retention_policy(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                db=db,
            )

        await db.refresh(meeting)
        assert meeting.transcript_text is None
        assert summary["transcripts_cleared"] >= 1
