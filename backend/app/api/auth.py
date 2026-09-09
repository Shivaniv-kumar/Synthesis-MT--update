"""Authentication API routes.

POST /api/auth/login              — email + password -> JWT (dev/staging only)
POST /api/auth/logout             — stateless logout
GET  /api/auth/me                 — return current user profile
GET  /api/auth/oidc/authorize     — begin SSO flow (returns authorization_url)
GET  /api/auth/oidc/callback      — handle SSO callback, provision user, return JWT

Password-based login is disabled in production environments.
SSO login is available in all environments when SSO_PROVIDER is configured.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
import bcrypt as _bcrypt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context, oauth2_scheme
from app.auth.jwt import create_access_token, create_mfa_challenge_token, decode_token
from app.auth.oidc import OIDCUserInfo, get_oidc_provider, provision_user_from_oidc
from app.config import get_settings
from app.database import get_db
from app.models import AuditLog, User, WorkspaceMember
from app.models.workspace import Workspace
from app.models.password_reset_token import PasswordResetToken
from app.models.revoked_token import RevokedToken
from app.models.user_mfa import UserMFA
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_DUMMY_HASH: bytes = _bcrypt.hashpw(b"__dummy__", _bcrypt.gensalt())
_ROLE_ALIASES: dict[str, str] = {
    "admin": "Admin",
    "member": "Member",
    "viewer": "Viewer",
}


# ---------------------------------------------------------------------------
# Inline schemas for OIDC endpoints
# ---------------------------------------------------------------------------


class AuthorizeResponse(BaseModel):
    authorization_url: str
    state: str  # H14: HMAC-signed state; client must echo this back in the callback


class MFAChallengeResponse(BaseModel):
    """Returned by /login when the user has MFA enabled.

    The client must call POST /auth/mfa/verify with this token and the TOTP code.
    """
    mfa_required: bool = True
    mfa_challenge_token: str


# ---------------------------------------------------------------------------
# OIDC security helpers
# ---------------------------------------------------------------------------


def _sign_state(nonce: str, secret: str) -> str:
    """Return 'nonce.hmac' where hmac is a SHA-256 signature over nonce."""
    sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def _verify_state(state: str, secret: str) -> bool:
    """Return True iff the state carries a valid HMAC signature."""
    if "." not in state:
        return False
    nonce, received_sig = state.rsplit(".", 1)
    expected_sig = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, received_sig)


def _validate_redirect_uri(redirect_uri: str) -> None:
    """C9: Reject redirect_uris that don't start with the configured frontend URL."""
    settings = get_settings()
    allowed_prefix = settings.frontend_url.rstrip("/")
    if not redirect_uri.startswith(allowed_prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri is not in the list of allowed URIs.",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _normalise_role(role: object) -> str:
    role_text = str(role or "Member").strip()
    return _ROLE_ALIASES.get(role_text.lower(), role_text)


async def _get_login_user_row(
    email: str,
    db: AsyncSession,
) -> Mapping[str, Any] | None:
    """Fetch login fields without ORM enum hydration.

    Some early Supabase rows used legacy role values/casing. Hydrating those
    rows through the ORM can raise before we can return a clean 401/403.
    """
    result = await db.execute(
        text(
            """
            SELECT
                id,
                tenant_id,
                email,
                COALESCE(display_name, email) AS display_name,
                hashed_password,
                role,
                is_active
            FROM users
            WHERE email = :email
            LIMIT 1
            """
        ),
        {"email": email},
    )
    row = result.mappings().first()
    return row


async def _get_primary_workspace_id_for_login(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> uuid.UUID:
    """Return the user's first workspace, falling back to tenant_id."""
    try:
        result = await db.execute(
            text(
                """
                SELECT workspace_id
                FROM workspace_members
                WHERE user_id = :user_id
                ORDER BY joined_at
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        )
        workspace_id = result.scalar_one_or_none()
        return uuid.UUID(str(workspace_id)) if workspace_id else tenant_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.login_workspace_lookup_failed", error=str(exc))
        return tenant_id


def _build_token_response_from_login_row(
    user_row: Mapping[str, Any],
    workspace_id: uuid.UUID,
) -> TokenResponse:
    user_id = uuid.UUID(str(user_row["id"]))
    tenant_id = uuid.UUID(str(user_row["tenant_id"]))
    email = str(user_row["email"])
    display_name = str(user_row.get("display_name") or email)
    role = _normalise_role(user_row.get("role"))

    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role=role,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserOut(
            id=user_id,
            email=email,
            display_name=display_name,
            role=role,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ),
    )


async def _get_primary_workspace_id(
    user: User,
    db: AsyncSession,
) -> uuid.UUID:
    """Return the first workspace the user is a member of.

    Falls back to tenant_id as a sentinel if no membership row is found
    (e.g. in tests or seed data that pre-dates workspace_members).
    """
    result = await db.execute(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(WorkspaceMember.joined_at)
        .limit(1)
    )
    row = result.first()
    return row[0] if row else user.tenant_id


def _build_user_out(user: User, workspace_id: uuid.UUID) -> UserOut:
    """Map a User ORM row to the UserOut schema."""
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name or user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
    )


def _build_token_response(user: User, workspace_id: uuid.UUID) -> TokenResponse:
    """Create a signed JWT and wrap it in a TokenResponse."""
    token = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        role=user.role,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=_build_user_out(user, workspace_id),
    )


# ---------------------------------------------------------------------------
# Auth event logging helper
# ---------------------------------------------------------------------------


async def _record_auth_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    after_data: dict[str, Any] | None = None,
) -> None:
    """Write a row to audit_logs for security-relevant auth events.

    Failures here are silently swallowed — login/logout must succeed even if
    the audit write fails (e.g. temporary DB issue).
    """
    try:
        entry = AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=user_id,
            entity_type="auth",
            entity_id=user_id,
            action=action,
            after_data=after_data or {},
        )
        db.add(entry)
        await db.flush([entry])
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.audit_log_failed", action=action, error=str(exc))


# ---------------------------------------------------------------------------
# POST /auth/login  (dev / staging only)
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,  # 200 = full token; 202 = MFA challenge (MFAChallengeResponse)
    summary="Obtain a JWT access token via email + password (non-production only)",
    status_code=status.HTTP_200_OK,
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate with email + password and return a signed JWT.

    This endpoint is disabled in production environments.  Use the SSO flow
    (``/auth/oidc/authorize``) for production access.
    """
    settings = get_settings()
    if settings.environment == "production" and not settings.allow_password_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Password-based login is disabled in production. "
                "Set ALLOW_PASSWORD_LOGIN=true or use SSO via /api/auth/oidc/authorize."
            ),
        )

    # Generic error to prevent user-enumeration via timing or messaging
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_row = await _get_login_user_row(body.email, db)

    if user_row is None:
        # Run a dummy verify to mitigate timing-based user enumeration
        _bcrypt.checkpw(b"__dummy__", _DUMMY_HASH)
        raise invalid_exc

    hashed_password = user_row.get("hashed_password")
    if not hashed_password or not _verify_password(body.password, str(hashed_password)):
        raise invalid_exc

    if not bool(user_row.get("is_active")):
        # L8: return the same generic message as bad credentials to avoid leaking
        # whether the account exists but is deactivated. Log the real reason server-side.
        logger.warning("auth.login_inactive", user_id=str(user_row.get("id")))
        raise invalid_exc

    user_id = uuid.UUID(str(user_row["id"]))
    tenant_id = uuid.UUID(str(user_row["tenant_id"]))

    # Check if MFA is enrolled and active for this user
    try:
        mfa_result = await db.execute(
            select(UserMFA).where(UserMFA.user_id == user_id, UserMFA.is_enabled.is_(True))
        )
        mfa_row = mfa_result.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.login_mfa_lookup_failed", error=str(exc))
        mfa_row = None

    if mfa_row is not None:
        # Issue a short-lived MFA challenge token instead of a full JWT.
        # The client must call POST /auth/mfa/verify to complete login.
        challenge_token = create_mfa_challenge_token(user_id)
        logger.info(
            "auth.login_mfa_required",
            user_id=str(user_id),
            tenant_id=str(tenant_id),
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"mfa_required": True, "mfa_challenge_token": challenge_token},
        )

    workspace_id = await _get_primary_workspace_id_for_login(user_id, tenant_id, db)

    logger.info(
        "auth.login_success",
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        method="password",
    )
    await _record_auth_event(
        db, tenant_id=tenant_id, user_id=user_id,
        action="login", after_data={"method": "password"},
    )

    return _build_token_response_from_login_row(user_row, workspace_id)


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    summary="Logout — revoke the current JWT so it cannot be reused",
    status_code=status.HTTP_200_OK,
)
async def logout(
    current_user: User = Depends(get_current_user),
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke the bearer token so subsequent requests with it return 401.

    Inserts the token's ``jti`` into ``revoked_tokens`` and lazily purges
    already-expired revocation rows to keep the table compact.
    """
    try:
        payload = decode_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            revoked = RevokedToken(
                id=uuid.uuid4(),
                jti=jti,
                user_id=current_user.id,
                expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
            )
            db.add(revoked)
            await db.flush([revoked])
            # Lazy cleanup: remove rows whose tokens have already expired
            await db.execute(
                delete(RevokedToken).where(
                    RevokedToken.expires_at < datetime.now(tz=timezone.utc)
                )
            )
    except Exception as exc:  # noqa: BLE001
        # Logout always succeeds from the client's perspective
        logger.warning("auth.logout_revocation_failed", error=str(exc))

    logger.info("auth.logout", user_id=str(current_user.id))
    await _record_auth_event(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action="logout",
    )
    return {"detail": "Logged out successfully"}


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserOut,
    summary="Return the authenticated user's profile",
)
async def me(
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
) -> UserOut:
    """Return the profile of the currently authenticated user."""
    return _build_user_out(current_user, ctx.workspace_id)


# ---------------------------------------------------------------------------
# GET /auth/oidc/authorize
# ---------------------------------------------------------------------------


@router.get(
    "/oidc/authorize",
    response_model=AuthorizeResponse,
    summary="Begin the SSO login flow — returns the IdP authorization URL",
)
async def oidc_authorize(
    redirect_uri: str = Query(
        ...,
        description="The URI the IdP should redirect to after login",
    ),
) -> AuthorizeResponse:
    """Return the authorization URL for the configured SSO provider.

    C9: redirect_uri is validated against the configured frontend URL.
    H14: A server-generated HMAC-signed state token is returned; clients must
         echo it back in the callback so the server can verify it.

    Raises 503 if no SSO provider is configured.
    """
    # C9: Validate redirect_uri against the configured allowed prefix
    _validate_redirect_uri(redirect_uri)

    try:
        provider = get_oidc_provider()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SSO is not configured: {exc}",
        ) from exc

    # H14: Generate a short-lived HMAC-signed state to prevent CSRF
    settings = get_settings()
    nonce = secrets.token_urlsafe(32)
    signed_state = _sign_state(nonce, settings.secret_key)

    authorization_url = await provider.get_authorization_url(
        redirect_uri=redirect_uri,
        state=signed_state,
    )

    logger.info("auth.oidc_authorize_requested", redirect_uri=redirect_uri)

    return AuthorizeResponse(authorization_url=authorization_url, state=signed_state)


# ---------------------------------------------------------------------------
# GET /auth/oidc/callback
# ---------------------------------------------------------------------------


@router.get(
    "/oidc/callback",
    response_model=TokenResponse,
    summary="SSO callback — exchange authorization code for a JWT",
)
async def oidc_callback(
    code: str = Query(..., description="Authorization code returned by the IdP"),
    redirect_uri: str = Query(
        ...,
        description="Must match the redirect_uri used in the authorization request",
    ),
    state: str = Query(
        ...,
        description="HMAC-signed state returned by /oidc/authorize — required for CSRF validation",
    ),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchange the authorization code for a JWT.

    C9: redirect_uri is validated against the configured allowed prefix.
    H14: The state HMAC signature is verified before the code is exchanged.

    Steps:
    1. Validate redirect_uri and state.
    2. Pass the code to the configured OIDC provider to obtain normalised user
       info (``OIDCUserInfo``).
    3. Upsert the user via ``provision_user_from_oidc``.
    4. Issue and return a signed JWT.

    Raises 400 on invalid state/redirect_uri, 502 if the IdP exchange fails,
    503 if SSO is not configured.
    """
    # C9: Validate redirect_uri
    _validate_redirect_uri(redirect_uri)

    # H14: Verify HMAC-signed state to prevent CSRF
    settings = get_settings()
    if not _verify_state(state, settings.secret_key):
        logger.warning("auth.oidc_invalid_state", state=state[:32])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter.",
        )

    try:
        provider = get_oidc_provider()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"SSO is not configured: {exc}",
        ) from exc

    try:
        oidc_info: OIDCUserInfo = await provider.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
        )
    except Exception as exc:
        logger.warning("auth.oidc_exchange_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to exchange authorization code with the identity provider.",
        ) from exc

    user = await provision_user_from_oidc(oidc_info=oidc_info, db=db)

    workspace_id = await _get_primary_workspace_id(user, db)

    logger.info(
        "auth.oidc_login_success",
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        method="oidc",
    )
    await _record_auth_event(
        db, tenant_id=user.tenant_id, user_id=user.id,
        action="login", after_data={"method": "oidc"},
    )

    return _build_token_response(user, workspace_id)


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


async def _send_reset_email(to_email: str, raw_token: str) -> None:
    """Fire the password-reset email via the configured email transport."""
    from app.services.notification_providers import EmailProvider
    settings = get_settings()
    reset_url = f"{settings.frontend_url}/reset-password?token={raw_token}"
    html_body = (
        "<p>You requested a password reset for your <strong>Synthesis</strong> account.</p>"
        f'<p><a href="{reset_url}">Click here to reset your password</a></p>'
        "<p>This link expires in <strong>1 hour</strong>. "
        "If you did not request this, you can safely ignore this email.</p>"
    )
    text_body = (
        f"Reset your Synthesis password: {reset_url}\n\n"
        "This link expires in 1 hour. If you did not request this, ignore this email."
    )
    provider = EmailProvider()
    await provider.send_email(
        to=to_email,
        subject="Reset your Synthesis password",
        html_body=html_body,
        text_body=text_body,
    )


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    summary="Request a password reset email",
)
async def forgot_password(
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Send a password-reset link to the given email address.

    Always returns the same response regardless of whether the email is
    registered, to prevent user enumeration.

    Anti-spam: if a valid token was issued in the last 5 minutes for the same
    account, a second email is suppressed silently.
    """
    _ok = {"detail": "If that email is registered, a reset link has been sent."}

    user_row = await _get_login_user_row(body.email, db)
    if user_row is None or not bool(user_row.get("is_active")):
        # Run a dummy bcrypt check so timing is indistinguishable from success
        _bcrypt.checkpw(b"__dummy__", _DUMMY_HASH)
        return _ok

    user_id = uuid.UUID(str(user_row["id"]))
    now_utc = datetime.now(tz=timezone.utc)

    # Anti-spam: suppress if a fresh token already exists for this user
    recent = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now_utc,
            PasswordResetToken.created_at > now_utc - timedelta(minutes=5),
        )
    )
    if recent.scalar_one_or_none() is not None:
        return _ok

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    db.add(PasswordResetToken(
        id=uuid.uuid4(),
        token_hash=token_hash,
        user_id=user_id,
        expires_at=now_utc + timedelta(hours=1),
    ))
    await db.flush()

    try:
        await _send_reset_email(str(user_row["email"]), raw_token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth.reset_email_failed", user_id=str(user_id), error=str(exc))

    logger.info("auth.forgot_password_sent", user_id=str(user_id))
    return _ok


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    summary="Reset password using a one-time token from the reset email",
)
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Validate the reset token and update the user's hashed password.

    The token is single-use — ``used_at`` is stamped on consumption.
    Returns 400 for expired, already-used, or unknown tokens (generic message
    to avoid leaking token validity information).
    """
    _invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This reset link is invalid or has expired. Please request a new one.",
    )

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    now_utc = datetime.now(tz=timezone.utc)

    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now_utc,
        )
    )
    reset_row = result.scalar_one_or_none()
    if reset_row is None:
        raise _invalid

    user_result = await db.execute(select(User).where(User.id == reset_row.user_id))
    user: User | None = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _invalid

    user.hashed_password = _bcrypt.hashpw(body.new_password.encode(), _bcrypt.gensalt()).decode()
    reset_row.used_at = now_utc
    await db.flush()

    await _record_auth_event(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="password_reset",
        after_data={"method": "email_token"},
    )
    logger.info("auth.password_reset_success", user_id=str(user.id))
    return {"detail": "Your password has been reset. You can now sign in with your new password."}


# ---------------------------------------------------------------------------
# POST /auth/accept-invite  — activate account from invite link
# ---------------------------------------------------------------------------


class AcceptInviteRequest(BaseModel):
    token: str = Field(..., min_length=10)
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post(
    "/accept-invite",
    status_code=status.HTTP_200_OK,
    summary="Accept an invitation: set a password and activate the account",
)
async def accept_invite(
    body: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This invitation link is invalid or has expired. Please ask your admin to resend it.",
    )

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    now_utc = datetime.now(tz=timezone.utc)

    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.token_type == "invite",
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now_utc,
        )
    )
    invite_row = result.scalar_one_or_none()
    if invite_row is None:
        raise _invalid

    user_result = await db.execute(select(User).where(User.id == invite_row.user_id))
    user: User | None = user_result.scalar_one_or_none()
    if user is None:
        raise _invalid

    user.hashed_password = _bcrypt.hashpw(body.new_password.encode(), _bcrypt.gensalt()).decode()
    user.is_active = True
    invite_row.used_at = now_utc
    await db.flush()

    await _record_auth_event(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action="invite_accepted",
        after_data={"method": "invite_token"},
    )
    logger.info("auth.invite_accepted", user_id=str(user.id))
    return {"detail": "Your account is ready. You can now sign in."}


# ---------------------------------------------------------------------------
# GET /auth/my-workspaces — workspaces the caller is a member of
# ---------------------------------------------------------------------------


class WorkspaceInfo(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


@router.get(
    "/my-workspaces",
    response_model=list[WorkspaceInfo],
    summary="List workspaces the current user belongs to",
)
async def my_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceInfo]:
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == current_user.id,
            Workspace.is_active.is_(True),
        )
        .order_by(Workspace.name)
    )
    workspaces = result.scalars().all()
    return [WorkspaceInfo.model_validate(ws) for ws in workspaces]


# ---------------------------------------------------------------------------
# POST /auth/switch-workspace — re-issue JWT for a different workspace
# ---------------------------------------------------------------------------


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: uuid.UUID


@router.post(
    "/switch-workspace",
    response_model=TokenResponse,
    summary="Switch to a different workspace and receive a new JWT",
)
async def switch_workspace(
    body: SwitchWorkspaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    # Verify the workspace belongs to the caller's tenant
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == body.workspace_id,
            Workspace.tenant_id == current_user.tenant_id,
            Workspace.is_active.is_(True),
        )
    )
    workspace: Workspace | None = ws_result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found.",
        )

    # Verify the caller is a member of that workspace
    membership_result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == body.workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
    )
    if membership_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace.",
        )

    logger.info(
        "auth.workspace_switched",
        user_id=str(current_user.id),
        workspace_id=str(body.workspace_id),
    )
    return _build_token_response(current_user, body.workspace_id)
