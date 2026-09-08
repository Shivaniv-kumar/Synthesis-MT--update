"""JWT creation and decoding utilities.

All tokens are signed HS256 using settings.SECRET_KEY.
Token claims:
    sub   — str(user_id)
    tid   — str(tenant_id)
    wid   — str(workspace_id)
    role  — user role string
    exp   — expiry (UTC epoch)
    jti   — unique token ID (UUID4) used for revocation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings

logger = structlog.get_logger(__name__)

_ALGORITHM = "HS256"


def _settings():
    return get_settings()


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    role: str,
    expires_delta: timedelta = timedelta(hours=8),
) -> str:
    """Return a signed JWT containing identity + tenant claims."""
    settings = _settings()
    now = datetime.now(tz=timezone.utc)
    expire = now + expires_delta

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "wid": str(workspace_id),
        "role": role,
        "iat": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def create_mfa_challenge_token(user_id: uuid.UUID, expires_delta: timedelta = timedelta(minutes=5)) -> str:
    """Return a short-lived JWT that only authorises MFA verification.

    The token carries ``mfa_challenge=True`` and omits ``wid``/``role`` so
    it cannot satisfy any protected endpoint's RBAC dependency.
    """
    settings = _settings()
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "mfa_challenge": True,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT.

    Raises:
        HTTPException(401) if the token is missing, malformed, or expired.
    """
    settings = _settings()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.secret_key, algorithms=[_ALGORITHM]
        )
    except JWTError as exc:
        logger.warning("jwt.decode_failed", error=str(exc))
        raise credentials_exception from exc

    # Validate required claims are present
    if not payload.get("sub") or not payload.get("tid") or not payload.get("wid"):
        logger.warning("jwt.missing_claims", payload_keys=list(payload.keys()))
        raise credentials_exception

    return payload
