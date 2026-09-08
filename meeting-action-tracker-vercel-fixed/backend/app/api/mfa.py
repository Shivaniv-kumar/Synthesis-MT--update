"""Multi-Factor Authentication (TOTP) API.

POST /auth/mfa/enroll          — generate a new TOTP secret and QR URI
POST /auth/mfa/confirm-enroll  — verify the first TOTP code and activate MFA
POST /auth/mfa/verify          — exchange an mfa_challenge token + TOTP code for a full JWT
DELETE /auth/mfa               — disable MFA (requires current TOTP code)

Design notes
------------
* The TOTP secret is generated with pyotp.random_base32() (160-bit entropy).
* MFA is tied to the password-based dev/staging login path.  SSO users rely on
  their IdP (WorkOS / Auth0) for MFA enforcement — that is configured in the
  IdP dashboard and is not our responsibility to re-enforce here.
* The mfa_challenge JWT carries no workspace/role claims so it cannot satisfy
  any RBAC dependency; it is only consumable by POST /auth/mfa/verify.
* Backup codes: 8 codes, each 8 alphanumeric characters, stored as bcrypt hashes.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.jwt import _ALGORITHM, _settings, create_access_token, create_mfa_challenge_token
from app.database import get_db
from app.models import AuditLog, User, WorkspaceMember
from app.models.user_mfa import UserMFA

try:
    import pyotp
    _PYOTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYOTP_AVAILABLE = False

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/mfa", tags=["mfa"])

_BACKUP_CODE_COUNT = 8
_BACKUP_CODE_LENGTH = 8
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15


# ---------------------------------------------------------------------------
# TOTP encryption helpers
# ---------------------------------------------------------------------------


def _get_fernet():  # type: ignore[return]
    """Return a Fernet instance if TOTP_ENCRYPTION_KEY is configured, else None."""
    try:
        from cryptography.fernet import Fernet
        key = _settings().totp_encryption_key
        if not key:
            return None
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def _encrypt_totp_secret(secret: str) -> str:
    f = _get_fernet()
    if f is None:
        return secret
    return f.encrypt(secret.encode()).decode()


def _decrypt_totp_secret(stored: str) -> str:
    """Decrypt a TOTP secret.  Falls back to plaintext for pre-encryption rows."""
    f = _get_fernet()
    if f is None:
        return stored
    try:
        return f.decrypt(stored.encode()).decode()
    except Exception:
        return stored  # stored in plaintext from before encryption was enabled


# ---------------------------------------------------------------------------
# Auth event logging
# ---------------------------------------------------------------------------


async def _record_mfa_event(
    db: AsyncSession,
    *,
    user: "User",
    action: str,
) -> None:
    try:
        entry = AuditLog(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            entity_type="auth",
            entity_id=user.id,
            action=action,
            after_data={"method": "mfa"},
        )
        db.add(entry)
        await db.flush([entry])
    except Exception as exc:  # noqa: BLE001
        logger.warning("mfa.audit_log_failed", action=action, error=str(exc))


# ---------------------------------------------------------------------------
# Other helpers
# ---------------------------------------------------------------------------


def _require_pyotp() -> None:
    if not _PYOTP_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="MFA is not available — install the 'pyotp' package.",
        )


def _verify_totp(secret: str, code: str) -> bool:
    """Return True if *code* is a valid current or previous-window TOTP value."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def _generate_backup_codes() -> tuple[list[str], list[str]]:
    """Return (plaintext_codes, bcrypt_hashes)."""
    plain = [
        secrets.token_hex(_BACKUP_CODE_LENGTH // 2).upper()
        for _ in range(_BACKUP_CODE_COUNT)
    ]
    hashed = [
        _bcrypt.hashpw(code.encode(), _bcrypt.gensalt()).decode()
        for code in plain
    ]
    return plain, hashed


def _consume_backup_code(stored_hashes: list[str], code: str) -> tuple[bool, list[str]]:
    """Try to consume a backup code; return (success, remaining_hashes)."""
    code_bytes = code.upper().encode()
    for i, h in enumerate(stored_hashes):
        if _bcrypt.checkpw(code_bytes, h.encode()):
            remaining = stored_hashes[:i] + stored_hashes[i + 1 :]
            return True, remaining
    return False, stored_hashes


async def _get_primary_workspace(user_id: uuid.UUID, db: AsyncSession) -> uuid.UUID | None:
    result = await db.execute(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.joined_at)
        .limit(1)
    )
    row = result.first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class EnrollResponse(BaseModel):
    totp_uri: str
    secret: str  # shown once during enrollment for manual entry
    backup_codes: list[str]  # shown once — user must save these


class ConfirmEnrollRequest(BaseModel):
    totp_code: str


class ConfirmEnrollResponse(BaseModel):
    message: str


class VerifyRequest(BaseModel):
    mfa_challenge_token: str
    totp_code: str


class VerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DisableMFARequest(BaseModel):
    totp_code: str


# ---------------------------------------------------------------------------
# POST /auth/mfa/enroll
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=EnrollResponse,
    summary="Generate a TOTP secret and provisioning URI for MFA enrollment",
)
async def enroll_mfa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EnrollResponse:
    """Generate a TOTP secret for the current user.

    The response contains a ``totp_uri`` (otpauth:// URI) for QR-code display and
    the raw ``secret`` for manual entry.  MFA is **not** activated until the user
    calls ``/confirm-enroll`` with a valid code, proving they have the secret.

    The returned ``backup_codes`` are shown only once.  The user must save them.
    If MFA is already enabled, the existing enrollment is replaced (the old secret
    is invalidated immediately).
    """
    _require_pyotp()

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    totp_uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="Synthesis",
    )

    plain_codes, hashed_codes = _generate_backup_codes()
    encrypted_secret = _encrypt_totp_secret(secret)

    result = await db.execute(
        select(UserMFA).where(UserMFA.user_id == current_user.id)
    )
    mfa_row: UserMFA | None = result.scalar_one_or_none()

    if mfa_row is None:
        mfa_row = UserMFA(
            id=uuid.uuid4(),
            user_id=current_user.id,
            totp_secret=encrypted_secret,
            is_enabled=False,  # not active until confirmed
            backup_codes=",".join(hashed_codes),
        )
        db.add(mfa_row)
    else:
        mfa_row.totp_secret = encrypted_secret
        mfa_row.is_enabled = False
        mfa_row.backup_codes = ",".join(hashed_codes)

    await db.flush()

    logger.info("mfa.enroll_started", user_id=str(current_user.id))
    return EnrollResponse(totp_uri=totp_uri, secret=secret, backup_codes=plain_codes)


# ---------------------------------------------------------------------------
# POST /auth/mfa/confirm-enroll
# ---------------------------------------------------------------------------


@router.post(
    "/confirm-enroll",
    response_model=ConfirmEnrollResponse,
    summary="Confirm MFA enrollment with a TOTP code from the authenticator app",
)
async def confirm_enroll_mfa(
    body: ConfirmEnrollRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfirmEnrollResponse:
    """Activate MFA after the user proves they can generate valid codes."""
    _require_pyotp()

    result = await db.execute(
        select(UserMFA).where(UserMFA.user_id == current_user.id)
    )
    mfa_row: UserMFA | None = result.scalar_one_or_none()

    if mfa_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA enrollment not started. Call /enroll first.",
        )

    if not _verify_totp(_decrypt_totp_secret(mfa_row.totp_secret), body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    mfa_row.is_enabled = True
    await db.flush()

    logger.info("mfa.enabled", user_id=str(current_user.id))
    return ConfirmEnrollResponse(message="MFA enabled successfully.")


# ---------------------------------------------------------------------------
# POST /auth/mfa/verify
# ---------------------------------------------------------------------------


@router.post(
    "/verify",
    response_model=VerifyResponse,
    summary="Exchange an MFA challenge token and TOTP code for a full JWT",
)
async def verify_mfa(
    body: VerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> VerifyResponse:
    """Complete the MFA login step.

    Accepts the short-lived mfa_challenge JWT issued by ``/auth/login`` when
    MFA is required, plus the current TOTP code (or a backup code).
    Returns a full access token on success.
    """
    _require_pyotp()

    settings = _settings()
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired MFA challenge.",
    )

    try:
        claims = jwt.decode(
            body.mfa_challenge_token,
            settings.secret_key,
            algorithms=[_ALGORITHM],
        )
    except JWTError:
        raise invalid

    if not claims.get("mfa_challenge"):
        raise invalid

    user_id = uuid.UUID(claims["sub"])

    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise invalid

    mfa_result = await db.execute(
        select(UserMFA).where(UserMFA.user_id == user_id, UserMFA.is_enabled.is_(True))
    )
    mfa_row: UserMFA | None = mfa_result.scalar_one_or_none()
    if mfa_row is None:
        raise invalid

    # Rate limiting: reject if account is locked
    now_utc = datetime.now(tz=timezone.utc)
    if mfa_row.locked_until and mfa_row.locked_until > now_utc:
        logger.warning("mfa.verify_locked", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed MFA attempts. Try again later.",
        )

    # Try TOTP first, then backup codes
    decrypted_secret = _decrypt_totp_secret(mfa_row.totp_secret)
    totp_ok = _verify_totp(decrypted_secret, body.totp_code)
    if not totp_ok:
        stored = mfa_row.backup_codes.split(",") if mfa_row.backup_codes else []
        code_ok, remaining = _consume_backup_code(stored, body.totp_code)
        if not code_ok:
            # Increment failure counter; lock if threshold reached
            mfa_row.failed_attempts = (mfa_row.failed_attempts or 0) + 1
            if mfa_row.failed_attempts >= _MAX_FAILED_ATTEMPTS:
                mfa_row.locked_until = now_utc + timedelta(minutes=_LOCKOUT_MINUTES)
                logger.warning(
                    "mfa.account_locked",
                    user_id=str(user_id),
                    attempts=mfa_row.failed_attempts,
                )
            await db.flush()
            logger.warning("mfa.verify_failed", user_id=str(user_id), attempts=mfa_row.failed_attempts)
            await _record_mfa_event(db, user=user, action="mfa_failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA code.",
            )
        mfa_row.backup_codes = ",".join(remaining)
        await db.flush()
        logger.info("mfa.backup_code_used", user_id=str(user_id), remaining=len(remaining))

    # Reset failure counter on success
    mfa_row.failed_attempts = 0
    mfa_row.locked_until = None
    await db.flush()
    await _record_mfa_event(db, user=user, action="mfa_success")

    workspace_result = await db.execute(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(WorkspaceMember.joined_at)
        .limit(1)
    )
    ws_row = workspace_result.first()
    workspace_id: uuid.UUID = ws_row[0] if ws_row else user.tenant_id

    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        role=user.role,
    )

    logger.info("mfa.verify_success", user_id=str(user_id))
    return VerifyResponse(access_token=token)


# ---------------------------------------------------------------------------
# DELETE /auth/mfa
# ---------------------------------------------------------------------------


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable MFA for the current user (requires current TOTP code)",
)
async def disable_mfa(
    body: DisableMFARequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Disable and remove the TOTP enrollment for the current user."""
    _require_pyotp()

    result = await db.execute(
        select(UserMFA).where(UserMFA.user_id == current_user.id, UserMFA.is_enabled.is_(True))
    )
    mfa_row: UserMFA | None = result.scalar_one_or_none()

    if mfa_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this account.",
        )

    if not _verify_totp(_decrypt_totp_secret(mfa_row.totp_secret), body.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code.",
        )

    await db.delete(mfa_row)
    await db.flush()

    logger.info("mfa.disabled", user_id=str(current_user.id))
