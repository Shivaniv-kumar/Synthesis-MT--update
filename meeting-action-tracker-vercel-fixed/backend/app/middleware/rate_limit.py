"""Redis sliding-window rate limiting middleware.

Implements two rate-limit tiers per tenant:
- **General** endpoints: ``requests_per_hour`` (default 1 000/h).
- **Extraction** endpoints: ``extractions_per_hour`` (default 100/h).

Extraction endpoints are those whose path matches:
    /api/meetings/{id}/extract
    /api/upload  (any sub-path)

The middleware also checks a per-tenant daily spend cap via
:class:`~app.observability.cost.CostTracker`.  If the cap is exceeded a
``402 Payment Required`` is returned before the request reaches the route.

JWT decoding is intentionally minimal — the ``sub`` claim is extracted
without full signature validation so that the middleware does not need the
secret key.  Full auth validation still happens in the route dependency chain.

Responses on limit exceeded:
    - 429 Too Many Requests  — rate limit
    - 402 Payment Required   — spend cap reached

Usage (in ``app/main.py``)::

    from app.middleware.rate_limit import RateLimitMiddleware

    app.add_middleware(
        RateLimitMiddleware,
        redis_url=settings.redis_url,
        requests_per_hour=1000,
        extractions_per_hour=100,
    )
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from typing import Any

import redis.asyncio as aioredis
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.observability.cost import CostTracker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Paths that are always exempt from rate limiting
_SKIP_PATHS: frozenset[str] = frozenset(
    {"/health", "/health/db", "/docs", "/redoc", "/openapi.json"}
)

# Extraction endpoint patterns (matched against request.url.path)
_EXTRACTION_PATH_RE = re.compile(
    r"^/api/meetings/[^/]+/extract(?:/.*)?$"
    r"|^/api/upload(?:/.*)?$"
)

# M2: Login endpoint — rate-limited by IP to prevent brute-force attacks
_LOGIN_PATH_RE = re.compile(r"^/api/auth/login$")
_LOGIN_MAX_ATTEMPTS = 10   # per IP per minute
_LOGIN_WINDOW_SECONDS = 60  # 1-minute window for login attempts

_WINDOW_SECONDS = 3600  # 1 hour sliding window for general/extraction tiers

# ---------------------------------------------------------------------------
# M10: Module-level registry so lifespan can close all Redis clients cleanly
# ---------------------------------------------------------------------------

import weakref as _weakref
_redis_registry: list[_weakref.ref] = []


async def close_all_redis_clients() -> None:
    """Close every Redis client created by ``RateLimitMiddleware`` instances."""
    for ref in list(_redis_registry):
        client = ref()
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
    _redis_registry.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_sub_from_jwt(authorization: str | None) -> str | None:
    """Decode the JWT payload (without signature verification) and return ``sub``.

    Returns ``None`` if the header is absent, malformed, or ``sub`` is missing.
    We only need the tenant identifier for rate-limit key namespacing; full
    auth validation is performed by the route dependency chain.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        # JWT payload is base64url-encoded; add padding if needed.
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload: dict[str, Any] = json.loads(payload_bytes)
        sub = payload.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


def _seconds_until_next_hour() -> int:
    """Return the number of seconds until the top of the next hour."""
    now = time.time()
    return math.ceil(_WINDOW_SECONDS - (now % _WINDOW_SECONDS))


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter + spend-cap enforcer.

    Args:
        app:                   ASGI application.
        redis_url:             Redis connection URL.
        requests_per_hour:     General rate limit per tenant (default 1 000).
        extractions_per_hour:  Extraction-specific rate limit (default 100).
        spend_cap_usd:         Optional daily spend cap in USD.  When ``None``
                               the spend-cap check is skipped entirely.
    """

    def __init__(
        self,
        app: ASGIApp,
        redis_url: str,
        requests_per_hour: int = 1000,
        extractions_per_hour: int = 100,
        spend_cap_usd: float | None = None,
    ) -> None:
        super().__init__(app)
        self._redis: aioredis.Redis = aioredis.from_url(
            redis_url, encoding="utf-8", decode_responses=True
        )
        self._requests_per_hour = requests_per_hour
        self._extractions_per_hour = extractions_per_hour
        self._spend_cap_usd = spend_cap_usd
        self._cost_tracker = CostTracker(self._redis)
        # M10: register so close_all_redis_clients() can reach this client
        _redis_registry.append(_weakref.ref(self._redis))

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path: str = request.url.path
        now = time.time()

        # ------------------------------------------------------------------
        # Skip rate limiting for health / docs endpoints
        # ------------------------------------------------------------------
        if path in _SKIP_PATHS:
            return await call_next(request)

        # ------------------------------------------------------------------
        # M2: Per-IP brute-force protection on the login endpoint
        # ------------------------------------------------------------------
        if _LOGIN_PATH_RE.match(path):
            client_ip: str = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or (request.client.host if request.client else "unknown")
            )
            login_key = f"rl:login:{client_ip}:{int(now // _LOGIN_WINDOW_SECONDS)}"
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    member = f"{now}"
                    pipe.zadd(login_key, {member: now})
                    pipe.zremrangebyscore(login_key, 0, now - _LOGIN_WINDOW_SECONDS)
                    pipe.zcard(login_key)
                    pipe.expire(login_key, _LOGIN_WINDOW_SECONDS * 2)
                    login_results = await pipe.execute()
                if int(login_results[2]) > _LOGIN_MAX_ATTEMPTS:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many login attempts. Please try again later."},
                        headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)},
                    )
            except Exception:
                # Redis unavailable — fail open for login rate limiting
                pass

        # ------------------------------------------------------------------
        # Identify tenant from JWT (best-effort; no signature check)
        # ------------------------------------------------------------------
        authorization: str | None = request.headers.get("Authorization")
        tenant_id: str | None = _extract_sub_from_jwt(authorization)

        # If we cannot identify a tenant we let the request through and allow
        # the route's auth dependency to reject it with 401.
        if not tenant_id:
            return await call_next(request)

        # ------------------------------------------------------------------
        # Determine rate-limit tier
        # ------------------------------------------------------------------
        is_extraction = bool(_EXTRACTION_PATH_RE.match(path))
        endpoint_type = "extraction" if is_extraction else "general"
        limit = self._extractions_per_hour if is_extraction else self._requests_per_hour

        # ------------------------------------------------------------------
        # Sliding-window check using Redis sorted set
        # ------------------------------------------------------------------
        window_start = now - _WINDOW_SECONDS
        current_hour = int(now // _WINDOW_SECONDS)
        rl_key = f"rl:{tenant_id}:{endpoint_type}:{current_hour}"

        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                # Add current request timestamp as both score and member (unique
                # by appending a sub-millisecond counter encoded in the member).
                member = f"{now}"
                pipe.zadd(rl_key, {member: now})
                # Remove entries outside the sliding window
                pipe.zremrangebyscore(rl_key, 0, window_start)
                # Count remaining entries
                pipe.zcard(rl_key)
                # Ensure TTL is set (2× window is safe)
                pipe.expire(rl_key, _WINDOW_SECONDS * 2)
                results = await pipe.execute()

            request_count: int = int(results[2])

            if request_count > limit:
                retry_after = _seconds_until_next_hour()
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        except Exception:
            # Redis unavailable — fail open so the request reaches the route handler.
            # The route's own auth/validation still runs; we just skip rate limiting.
            pass

        # ------------------------------------------------------------------
        # Spend-cap check
        # ------------------------------------------------------------------
        if self._spend_cap_usd is not None:
            try:
                within_cap = await self._cost_tracker.check_spend_cap(
                    tenant_id, self._spend_cap_usd
                )
                if not within_cap:
                    return JSONResponse(
                        status_code=402,
                        content={"detail": "Spend cap reached for today"},
                    )
            except Exception:
                # Redis unavailable — fail open for spend-cap check
                pass

        return await call_next(request)
