"""FastAPI application entry-point for the Synthesis backend."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import check_database, create_tables

# ---------------------------------------------------------------------------
# Router imports
# ---------------------------------------------------------------------------

# Core routers (original)
from app.routers import auth, dashboard, items, meetings

# Phase-1 add-on routers
from app.api.bi_feed import router as bi_router
from app.api.upload import router as upload_router
from app.api.platforms import router as platforms_router
from app.api.integrations import router as integrations_router

# New routers wired in during final integration
from app.api.workspace import router as workspace_router
from app.api.notifications import router as notifications_router
from app.api.admin.costs import router as admin_costs_router

from app.api.projects import router as projects_router
from app.api.compliance import router as compliance_router
from app.api.mfa import router as mfa_router
from app.api.item_comments import router as item_comments_router
from app.api.chat import router as chat_router
from app.api.knowledge import router as knowledge_router

# ---------------------------------------------------------------------------
# Middleware imports
# ---------------------------------------------------------------------------

from app.middleware.security import SecurityHeadersMiddleware, RequestIDMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.observability.logging import RequestContextMiddleware

# ---------------------------------------------------------------------------
# Trailing-slash normaliser (pure ASGI — no HTTP redirect)
# ---------------------------------------------------------------------------

class _AddTrailingSlash:
    """Internally rewrite /api/<collection> to /api/<collection>/ without a redirect.

    With redirect_slashes=False, FastAPI never issues a 307.  Instead this
    middleware rewrites the ASGI scope path so the router always sees the
    canonical trailing-slash form.  Only acts on two-segment API paths
    (/api/<word>) that don't already end with a slash; deeper paths (resource
    IDs, action sub-routes) are left unchanged.
    """

    __slots__ = ("app",)

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            # Match /api/<collection> only — exactly 3 non-empty parts when split by "/"
            # e.g. ["", "api", "meetings"] but NOT ["", "api", "meetings", "uuid"]
            if path and not path.endswith("/"):
                parts = path.split("/")
                # parts == ["", "api", "<word>"] for a collection path
                if len(parts) == 3 and parts[0] == "" and parts[1] == "api" and parts[2]:
                    scope = dict(scope)
                    scope["path"] = path + "/"
                    raw = scope.get("raw_path", path.encode())
                    scope["raw_path"] = raw + b"/"
        await self.app(scope, receive, send)

# ---------------------------------------------------------------------------
# Observability imports
# ---------------------------------------------------------------------------

from app.observability.tracing import setup_tracing
from app.observability.metrics import setup_metrics

# ---------------------------------------------------------------------------
# Structlog configuration
# ---------------------------------------------------------------------------


def _configure_logging(log_level: str) -> None:
    """Wire structlog to emit JSON in production, pretty-print in development."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    settings = get_settings()
    if settings.environment == "development":
        renderer: Any = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level.upper())


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _seed_admin_user(logger: Any) -> None:
    """Create or refresh the environment-configured bootstrap administrator."""
    import uuid
    import bcrypt as _bcrypt
    from sqlalchemy import text
    from app.database import engine

    settings = get_settings()
    email = settings.seed_admin_email.strip().lower()
    password_hash = _bcrypt.hashpw(
        settings.seed_admin_password.encode(), _bcrypt.gensalt()
    ).decode()

    async with engine.begin() as conn:
        existing = await conn.execute(
            text("SELECT id, tenant_id FROM users WHERE lower(email) = :email LIMIT 1"),
            {"email": email},
        )
        user_row = existing.mappings().first()

        if user_row is not None:
            uid = user_row["id"]
            tid = user_row["tenant_id"]
            await conn.execute(
                text(
                    "UPDATE users SET display_name = :display_name, "
                    "hashed_password = :password_hash, role = 'Admin', "
                    "is_active = true, updated_at = now() WHERE id = :user_id"
                ),
                {
                    "display_name": settings.seed_admin_display_name,
                    "password_hash": password_hash,
                    "user_id": uid,
                },
            )
        else:
            tenant_result = await conn.execute(
                text("SELECT id FROM tenants WHERE is_active = true ORDER BY created_at LIMIT 1")
            )
            existing_tenant_id = tenant_result.scalar_one_or_none()
            tid = existing_tenant_id or uuid.uuid4()
            if existing_tenant_id is None:
                await conn.execute(
                    text(
                        "INSERT INTO tenants "
                        "(id, name, plan, is_active, created_at, updated_at) "
                        "VALUES (:id, 'My Organization', 'free', true, now(), now())"
                    ),
                    {"id": tid},
                )

            uid = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, tenant_id, email, display_name, hashed_password, role, "
                    "is_active, created_at, updated_at) VALUES "
                    "(:id, :tenant_id, :email, :display_name, :password_hash, "
                    "'Admin', true, now(), now())"
                ),
                {
                    "id": uid,
                    "tenant_id": tid,
                    "email": email,
                    "display_name": settings.seed_admin_display_name,
                    "password_hash": password_hash,
                },
            )

        workspace_result = await conn.execute(
            text(
                "SELECT id FROM workspaces WHERE tenant_id = :tenant_id "
                "AND is_active = true ORDER BY created_at LIMIT 1"
            ),
            {"tenant_id": tid},
        )
        wid = workspace_result.scalar_one_or_none()
        if wid is None:
            wid = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO workspaces "
                    "(id, tenant_id, name, is_active, created_at, updated_at) "
                    "VALUES (:id, :tenant_id, 'Default Workspace', true, now(), now())"
                ),
                {"id": wid, "tenant_id": tid},
            )

        membership_result = await conn.execute(
            text(
                "SELECT id FROM workspace_members "
                "WHERE workspace_id = :workspace_id AND user_id = :user_id LIMIT 1"
            ),
            {"workspace_id": wid, "user_id": uid},
        )
        membership_id = membership_result.scalar_one_or_none()
        if membership_id is None:
            await conn.execute(
                text(
                    "INSERT INTO workspace_members "
                    "(id, workspace_id, user_id, role, joined_at, created_at, updated_at) "
                    "VALUES (:id, :workspace_id, :user_id, 'Admin', now(), now(), now())"
                ),
                {"id": uuid.uuid4(), "workspace_id": wid, "user_id": uid},
            )
        else:
            await conn.execute(
                text(
                    "UPDATE workspace_members SET role = 'Admin', updated_at = now() "
                    "WHERE id = :membership_id"
                ),
                {"membership_id": membership_id},
            )

    logger.info("startup.seed_admin_done", email=email)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    settings = get_settings()
    _configure_logging(settings.log_level)

    logger = structlog.get_logger(__name__)
    logger.info(
        "startup",
        environment=settings.environment,
        log_level=settings.log_level,
    )

    # Initialise OpenTelemetry tracing
    setup_tracing(app, settings)
    logger.info("startup.tracing_ready")

    # Initialise OpenTelemetry metrics
    setup_metrics()
    logger.info("startup.metrics_ready")

    try:
        await asyncio.wait_for(create_tables(), timeout=20.0)
        logger.info("startup.db_ready")
    except asyncio.TimeoutError:
        logger.warning("startup.db_timeout - schema check timed out, server starting anyway")
        if settings.environment != "development":
            raise
    except Exception as exc:
        logger.error("startup.db_failed", error=str(exc))
        logger.warning("startup.db_skipped - server will start but DB calls will fail until connection is fixed")
        if settings.environment != "development":
            raise

    if settings.seed_admin:
        try:
            await _seed_admin_user(logger)
        except Exception as exc:
            logger.warning("startup.seed_admin_failed", error=str(exc))

    yield

    # M10: Close Redis connections held by the rate-limit middleware on graceful shutdown
    from app.middleware.rate_limit import close_all_redis_clients
    await close_all_redis_clients()
    logger.info("shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

settings = get_settings()

app = FastAPI(
    title="Synthesis API",
    version=settings.app_version,  # L2: version from env var, not hardcoded
    description=(
        "Extract, manage and track action items from meeting transcripts "
        "using Claude AI."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    redirect_slashes=False,
)

# ---------------------------------------------------------------------------
# Middleware
# (Starlette applies middleware in reverse registration order — last added
#  runs outermost. Register from innermost to outermost.)
# ---------------------------------------------------------------------------

# Innermost: normalise /api/<collection> → /api/<collection>/ before routing.
# Must be added first so it runs closest to the router (last-added = outermost).
app.add_middleware(_AddTrailingSlash)

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

# Unique request ID stamped on every request/response
app.add_middleware(RequestIDMiddleware)

# Bind per-request context vars to structlog
app.add_middleware(RequestContextMiddleware)

# Sliding-window rate limiter + spend-cap enforcer
# M8: spend_cap_usd wired from settings (None = disabled by default)
app.add_middleware(
    RateLimitMiddleware,
    redis_url=settings.redis_url,
    spend_cap_usd=settings.spend_cap_usd,
)

# CORS must be outermost so browser-visible errors still include CORS headers.
# Starlette applies middleware in reverse registration order, so register it last.
# allow_origin_regex covers *.up.railway.app so Railway deploy URLs keep working.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:5173"],
    allow_origin_regex=r"https://.*\.up\.railway\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

API_PREFIX = settings.api_prefix  # L3: prefix from env var (default "/api")

# Original core routers
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(meetings.router, prefix=API_PREFIX)
app.include_router(items.router, prefix=API_PREFIX)
app.include_router(dashboard.router, prefix=API_PREFIX)

# Phase-1 add-on routers
app.include_router(upload_router, prefix=API_PREFIX)
app.include_router(platforms_router, prefix=API_PREFIX)
app.include_router(integrations_router, prefix=API_PREFIX)
app.include_router(bi_router, prefix=API_PREFIX)

# New routers (final integration)
app.include_router(workspace_router, prefix=API_PREFIX)
app.include_router(notifications_router, prefix=API_PREFIX)
app.include_router(admin_costs_router, prefix=API_PREFIX)
app.include_router(projects_router, prefix=API_PREFIX)

app.include_router(compliance_router, prefix=API_PREFIX)
app.include_router(mfa_router, prefix=API_PREFIX)
app.include_router(item_comments_router, prefix=API_PREFIX)
app.include_router(chat_router, prefix=API_PREFIX)
app.include_router(knowledge_router, prefix=API_PREFIX)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"], summary="Health check")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/health/db", tags=["ops"], summary="Database health check")
async def database_health_check() -> dict[str, str]:
    await check_database()
    return {"status": "ok", "database": "ready"}


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Not authenticated"},
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "Forbidden"},
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": "Resource not found"},
    )


@app.exception_handler(422)
async def validation_handler(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI already provides detailed validation errors — pass them through
    # but keep the shape consistent.
    detail = getattr(exc, "errors", lambda: str(exc))()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": detail},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = structlog.get_logger(__name__)
    logger.exception("unhandled_error", path=request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred"},
    )


# Catch-all for non-HTTP exceptions (e.g. database errors, assertion failures).
# Without this, unhandled exceptions propagate past CORSMiddleware to
# ServerErrorMiddleware, which sends a 500 with no CORS headers — the browser
# blocks it and Axios reports it as "no response from API".
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = structlog.get_logger(__name__)
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        exc_type=type(exc).__name__,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred"},
    )
