"""Security-related Starlette middleware.

Provides two middleware classes that can be added to a FastAPI/Starlette app:

- ``SecurityHeadersMiddleware`` — injects standard security headers on every
  response (HSTS, CSP, X-Frame-Options, etc.).
- ``RequestIDMiddleware`` — stamps every response with a unique ``X-Request-ID``
  header so distributed traces can be correlated.

Usage (in app/main.py)::

    from app.middleware.security import RequestIDMiddleware, SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive HTTP security headers to every response.

    Headers set:
      - ``Strict-Transport-Security`` — enforce HTTPS for 1 year including
        subdomains.
      - ``X-Content-Type-Options`` — prevent MIME-type sniffing.
      - ``X-Frame-Options`` — block clickjacking via iframe embedding.
      - ``Content-Security-Policy`` — restrict resource origins to 'self'.
      - ``Referrer-Policy`` — send the full URL only to same-origin requests.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response: Response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique UUID4 request identifier to every response.

    If the incoming request already carries an ``X-Request-ID`` header (e.g.
    set by an upstream load balancer) that value is preserved; otherwise a new
    UUID4 is generated.  The resolved ID is echoed back as ``X-Request-ID`` on
    the response so callers can correlate logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Honour an upstream-supplied request ID; fall back to a fresh UUID.
        request_id: str = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
