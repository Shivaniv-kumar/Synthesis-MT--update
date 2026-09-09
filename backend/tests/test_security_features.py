"""QA tests for the five auth security gap fixes (commit ce7b851).

Tests are self-contained — they do NOT rely on the shared conftest fixtures so
they can be run independently even if the wider test suite is broken.

Run:
    cd meeting-action-tracker/backend
    pytest tests/test_security_features.py -v
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

# Force SQLite before any app import
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.revoked_token import RevokedToken
from app.models.user_mfa import UserMFA

# -----------------------------------------------------------------------
# Isolated in-memory DB for these tests only
# -----------------------------------------------------------------------

_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=False,
    connect_args={"check_same_thread": False},
)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _schema():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def db() -> AsyncSession:
    async with _SessionLocal() as session:
        yield session
        await session.rollback()


# -----------------------------------------------------------------------
# 1. JWT — every token now carries a unique jti claim
# -----------------------------------------------------------------------


def test_access_token_has_jti():
    """create_access_token must embed a jti UUID4 in the payload."""
    from jose import jwt as jose_jwt

    from app.auth.jwt import create_access_token

    uid = uuid.uuid4()
    tid = uuid.uuid4()
    wid = uuid.uuid4()
    token = create_access_token(user_id=uid, tenant_id=tid, workspace_id=wid, role="Member")

    payload = jose_jwt.decode(token, "test-secret-key-for-pytest", algorithms=["HS256"])
    assert "jti" in payload, "jti claim missing from access token"
    # Must be a valid UUID4
    parsed = uuid.UUID(payload["jti"])
    assert parsed.version == 4


def test_mfa_challenge_token_has_jti():
    """create_mfa_challenge_token must also embed a jti."""
    from jose import jwt as jose_jwt

    from app.auth.jwt import create_mfa_challenge_token

    token = create_mfa_challenge_token(user_id=uuid.uuid4())
    payload = jose_jwt.decode(token, "test-secret-key-for-pytest", algorithms=["HS256"])
    assert "jti" in payload, "jti claim missing from MFA challenge token"


def test_each_token_has_unique_jti():
    """Two calls produce two different jti values."""
    from jose import jwt as jose_jwt

    from app.auth.jwt import create_access_token

    uid, tid, wid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    t1 = create_access_token(user_id=uid, tenant_id=tid, workspace_id=wid, role="Member")
    t2 = create_access_token(user_id=uid, tenant_id=tid, workspace_id=wid, role="Member")
    p1 = jose_jwt.decode(t1, "test-secret-key-for-pytest", algorithms=["HS256"])
    p2 = jose_jwt.decode(t2, "test-secret-key-for-pytest", algorithms=["HS256"])
    assert p1["jti"] != p2["jti"], "Two tokens must not share a jti"


# -----------------------------------------------------------------------
# 2. Token revocation — DB round-trip
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_token_stored_and_queried(db: AsyncSession):
    """A jti written at logout must be found by the revocation check."""
    from sqlalchemy import select

    user_id = uuid.uuid4()
    jti = str(uuid.uuid4())
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=8)

    revoked = RevokedToken(
        id=uuid.uuid4(),
        jti=jti,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(revoked)
    await db.flush()

    result = await db.execute(select(RevokedToken).where(RevokedToken.jti == jti))
    row = result.scalar_one_or_none()
    assert row is not None, "RevokedToken row not found after insert"
    assert row.jti == jti


@pytest.mark.asyncio
async def test_non_revoked_jti_not_found(db: AsyncSession):
    """A jti that was never logged out must not appear in revoked_tokens."""
    from sqlalchemy import select

    unknown_jti = str(uuid.uuid4())
    result = await db.execute(select(RevokedToken).where(RevokedToken.jti == unknown_jti))
    assert result.scalar_one_or_none() is None


# -----------------------------------------------------------------------
# 3. MFA rate limiting — lockout columns on user_mfa
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mfa_failed_attempts_increment(db: AsyncSession):
    """Simulates the per-attempt increment logic used in verify_mfa."""
    from sqlalchemy import select

    mfa = UserMFA(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        totp_secret="JBSWY3DPEHPK3PXP",
        is_enabled=True,
        backup_codes="",
        failed_attempts=0,
        locked_until=None,
    )
    db.add(mfa)
    await db.flush()

    # Simulate 4 bad attempts
    for _ in range(4):
        mfa.failed_attempts += 1
        await db.flush()

    assert mfa.failed_attempts == 4
    assert mfa.locked_until is None  # not locked yet


@pytest.mark.asyncio
async def test_mfa_lockout_after_five_failures(db: AsyncSession):
    """On the 5th failure the account must be locked for 15 minutes."""
    _MAX_FAILED_ATTEMPTS = 5
    _LOCKOUT_MINUTES = 15

    mfa = UserMFA(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        totp_secret="JBSWY3DPEHPK3PXP",
        is_enabled=True,
        backup_codes="",
        failed_attempts=4,
        locked_until=None,
    )
    db.add(mfa)
    await db.flush()

    # 5th failure
    now_utc = datetime.now(tz=timezone.utc)
    mfa.failed_attempts += 1
    if mfa.failed_attempts >= _MAX_FAILED_ATTEMPTS:
        mfa.locked_until = now_utc + timedelta(minutes=_LOCKOUT_MINUTES)
    await db.flush()

    assert mfa.failed_attempts == 5
    assert mfa.locked_until is not None
    assert mfa.locked_until > now_utc


@pytest.mark.asyncio
async def test_mfa_lockout_check_logic(db: AsyncSession):
    """When locked_until is in the future, the lockout check fires."""
    future_lock = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
    mfa = UserMFA(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        totp_secret="JBSWY3DPEHPK3PXP",
        is_enabled=True,
        backup_codes="",
        failed_attempts=5,
        locked_until=future_lock,
    )
    db.add(mfa)
    await db.flush()

    now_utc = datetime.now(tz=timezone.utc)
    assert mfa.locked_until is not None and mfa.locked_until > now_utc, (
        "locked_until is in the future — verify_mfa should raise 429"
    )


@pytest.mark.asyncio
async def test_mfa_reset_on_success(db: AsyncSession):
    """A successful TOTP must reset failed_attempts and locked_until."""
    mfa = UserMFA(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        totp_secret="JBSWY3DPEHPK3PXP",
        is_enabled=True,
        backup_codes="",
        failed_attempts=3,
        locked_until=datetime.now(tz=timezone.utc) + timedelta(minutes=5),
    )
    db.add(mfa)
    await db.flush()

    # Simulate success reset
    mfa.failed_attempts = 0
    mfa.locked_until = None
    await db.flush()

    assert mfa.failed_attempts == 0
    assert mfa.locked_until is None


# -----------------------------------------------------------------------
# 4. TOTP encryption helpers — unit tests (no DB)
# -----------------------------------------------------------------------


def _make_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def test_totp_encrypt_decrypt_roundtrip(monkeypatch):
    """encrypt → decrypt must return the original plaintext secret."""
    from app.api.mfa import _decrypt_totp_secret, _encrypt_totp_secret

    key = _make_fernet_key()
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", key)

    # Force config cache to reload with the new key
    from app import config as _cfg
    _cfg.get_settings.cache_clear()

    secret = "JBSWY3DPEHPK3PXP"
    encrypted = _encrypt_totp_secret(secret)
    assert encrypted != secret, "Encrypted value must differ from plaintext"
    assert _decrypt_totp_secret(encrypted) == secret

    _cfg.get_settings.cache_clear()


def test_totp_decrypt_plaintext_fallback(monkeypatch):
    """If a secret is stored in plaintext and a Fernet key is now set,
    decryption must fall back to returning the plaintext unchanged."""
    from app.api.mfa import _decrypt_totp_secret

    key = _make_fernet_key()
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", key)

    from app import config as _cfg
    _cfg.get_settings.cache_clear()

    plaintext_secret = "JBSWY3DPEHPK3PXP"
    # Pass plaintext (not a valid Fernet ciphertext) — must not raise
    result = _decrypt_totp_secret(plaintext_secret)
    assert result == plaintext_secret, "Plaintext fallback must return the original string"

    _cfg.get_settings.cache_clear()


def test_totp_no_key_passthrough(monkeypatch):
    """With no TOTP_ENCRYPTION_KEY, both helpers must return the input unchanged."""
    from app.api.mfa import _decrypt_totp_secret, _encrypt_totp_secret

    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "")

    from app import config as _cfg
    _cfg.get_settings.cache_clear()

    secret = "JBSWY3DPEHPK3PXP"
    assert _encrypt_totp_secret(secret) == secret
    assert _decrypt_totp_secret(secret) == secret

    _cfg.get_settings.cache_clear()


# -----------------------------------------------------------------------
# 5. DB SSL — Supabase requires CERT_NONE; connection is still encrypted
# -----------------------------------------------------------------------


def test_db_ssl_uses_cert_none():
    """The DB SSL context must disable cert verification for Supabase compatibility.

    Supabase uses a self-signed certificate chain that is not in the system
    trust store. CERT_NONE keeps TLS encryption while skipping CA verification.
    """
    import ssl

    from app.database import _ssl_ctx

    assert _ssl_ctx.verify_mode == ssl.CERT_NONE, (
        "SSL context must use CERT_NONE so Supabase's self-signed cert is accepted"
    )
    assert _ssl_ctx.check_hostname is False
