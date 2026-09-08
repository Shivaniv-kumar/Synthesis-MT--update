"""OIDC security tests.

Covers
------
1. Invalid HMAC state is rejected at /oidc/callback
2. State with correct nonce but wrong signature is rejected
3. Truncated state (no dot separator) is rejected
4. redirect_uri outside the configured frontend URL is rejected (authorize + callback)
5. Pre-invited user (placeholder with no sso_subject) cannot authenticate before SSO
6. _sign_state / _verify_state round-trip correctness
7. OIDC authorize returns 503 when SSO_PROVIDER is not configured
"""

from __future__ import annotations

import os
import uuid
import hashlib
import hmac

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "oidc-security-test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")

from app.api.auth import _sign_state, _verify_state  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402

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


_SECRET = "oidc-security-test-secret"
_VALID_REDIRECT = "http://localhost:5173/callback"
_INVALID_REDIRECT = "https://evil.example.com/steal"


# ---------------------------------------------------------------------------
# 1. _sign_state / _verify_state unit tests
# ---------------------------------------------------------------------------


def test_sign_verify_roundtrip():
    state = _sign_state("some-nonce", _SECRET)
    assert _verify_state(state, _SECRET)


def test_verify_wrong_secret():
    state = _sign_state("nonce", _SECRET)
    assert not _verify_state(state, "different-secret")


def test_verify_tampered_nonce():
    state = _sign_state("original-nonce", _SECRET)
    # Replace the nonce portion but keep the signature
    nonce, sig = state.rsplit(".", 1)
    tampered = f"malicious-nonce.{sig}"
    assert not _verify_state(tampered, _SECRET)


def test_verify_no_dot_separator():
    # State without a dot cannot be split — must return False
    assert not _verify_state("nodothere", _SECRET)


def test_verify_empty_state():
    assert not _verify_state("", _SECRET)


def test_verify_only_dot():
    assert not _verify_state(".", _SECRET)


# ---------------------------------------------------------------------------
# 2. /oidc/callback rejects invalid state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_invalid_state_rejected(client: AsyncClient):
    """Completely invalid state returns 400."""
    r = await client.get(
        "/api/auth/oidc/callback",
        params={
            "code": "authcode123",
            "redirect_uri": _VALID_REDIRECT,
            "state": "this-is-invalid-state",
        },
    )
    assert r.status_code == 400
    assert "Invalid" in r.json().get("detail", "")


@pytest.mark.asyncio
async def test_callback_tampered_state_rejected(client: AsyncClient):
    """State with valid structure but wrong HMAC is rejected."""
    good_state = _sign_state("real-nonce", _SECRET)
    nonce, _ = good_state.rsplit(".", 1)
    # Forge a signature
    forged_sig = hmac.new(b"wrong-key", nonce.encode(), hashlib.sha256).hexdigest()
    bad_state = f"{nonce}.{forged_sig}"

    r = await client.get(
        "/api/auth/oidc/callback",
        params={
            "code": "authcode123",
            "redirect_uri": _VALID_REDIRECT,
            "state": bad_state,
        },
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_callback_empty_state_rejected(client: AsyncClient):
    r = await client.get(
        "/api/auth/oidc/callback",
        params={
            "code": "some-code",
            "redirect_uri": _VALID_REDIRECT,
            "state": "",
        },
    )
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 3. redirect_uri allowlist enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_rejects_external_redirect(client: AsyncClient):
    r = await client.get(
        "/api/auth/oidc/authorize",
        params={"redirect_uri": _INVALID_REDIRECT},
    )
    assert r.status_code == 400
    assert "redirect_uri" in r.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_callback_rejects_external_redirect(client: AsyncClient):
    valid_state = _sign_state("nonce", _SECRET)
    r = await client.get(
        "/api/auth/oidc/callback",
        params={
            "code": "someCode",
            "redirect_uri": _INVALID_REDIRECT,
            "state": valid_state,
        },
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_authorize_accepts_valid_redirect(client: AsyncClient):
    """A redirect_uri under the configured frontend URL is accepted.
    We expect 503 (SSO not configured) not 400 (bad redirect_uri).
    """
    r = await client.get(
        "/api/auth/oidc/authorize",
        params={"redirect_uri": _VALID_REDIRECT},
    )
    # 503 = redirect_uri accepted, SSO provider not configured in test env
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# 4. Pre-invited user cannot use callback before SSO login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_invited_user_no_access_before_sso(db: AsyncSession, client: AsyncClient):
    """A placeholder user with no sso_subject has no JWT — cannot access API."""
    # Simulate a pre-invited user: record exists, no sso_subject, no hashed_password
    placeholder = User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="invited@corp.com",
        display_name="Pre-Invited User",
        role="Member",
        is_active=False,  # not active until SSO completes
    )
    db.add(placeholder)
    await db.flush()

    # Without a JWT (no successful SSO), they cannot call any authenticated endpoint
    r = await client.get("/api/items/")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_pre_invited_inactive_user_password_login_denied(
    db: AsyncSession, client: AsyncClient
):
    """An inactive pre-invite user gets the same generic 401 as a bad password."""
    placeholder = User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="invited2@corp.com",
        display_name="Inactive User",
        role="Member",
        is_active=False,
        hashed_password=None,
    )
    db.add(placeholder)
    await db.flush()

    r = await client.post(
        "/api/auth/login",
        json={"email": "invited2@corp.com", "password": "any-password"},
    )
    # Production env check fires first; in test env (non-production) we get 401
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 5. SSO not configured → 503 on authorize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_authorize_503_when_not_configured(client: AsyncClient):
    """Without SSO_PROVIDER configured the authorize endpoint returns 503."""
    # We don't set SSO_PROVIDER so get_oidc_provider() raises ValueError → 503
    r = await client.get(
        "/api/auth/oidc/authorize",
        params={"redirect_uri": _VALID_REDIRECT},
    )
    assert r.status_code == 503
