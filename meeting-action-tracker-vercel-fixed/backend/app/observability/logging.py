"""Structured logging helpers for Meeting Action Tracker.

Provides:

- ``configure_structlog()`` — call once at startup to wire structlog.
- ``SanitizingProcessor``    — redacts sensitive keys from every log record.
- ``RequestContextMiddleware`` — binds request_id / method / path to structlog
  contextvars for the lifetime of each HTTP request.

Sensitive keys (those whose name contains any of the strings in
``SanitizingProcessor.SENSITIVE_KEYS``) are replaced with ``"[REDACTED]"``
before the log event reaches any renderer.

Usage::

    # In lifespan / app factory:
    from app.observability.logging import configure_structlog, RequestContextMiddleware

    configure_structlog(environment="production", log_level="INFO")
    app.add_middleware(RequestContextMiddleware)
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# ---------------------------------------------------------------------------
# Sanitising processor
# ---------------------------------------------------------------------------


class SanitizingProcessor:
    """structlog processor that redacts values for keys matching sensitive names.

    A key is considered sensitive if its lowercase form contains *any* substring
    listed in :attr:`SENSITIVE_KEYS`.  Redaction replaces the value with the
    literal string ``"[REDACTED]"`` in-place inside ``event_dict`` so that
    downstream processors and renderers never see the raw value.
    """

    SENSITIVE_KEYS: frozenset[str] = frozenset(
        {
            "transcript",
            "content",
            "token",
            "key",
            "password",
            "secret",
            "api_key",
            "email",
        }
    )

    def __call__(
        self,
        logger: Any,
        method: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        for key in list(event_dict.keys()):
            lower_key = key.lower()
            if any(sensitive in lower_key for sensitive in self.SENSITIVE_KEYS):
                event_dict[key] = "[REDACTED]"
        return event_dict


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------


def configure_structlog(environment: str, log_level: str) -> None:
    """Configure structlog for the given *environment* and *log_level*.

    - Production / staging → JSON renderer.
    - Development          → colourised ``ConsoleRenderer``.

    The :class:`SanitizingProcessor` is always included so that sensitive data
    can never leak into any log output regardless of the environment.

    Args:
        environment: One of ``"development"``, ``"staging"``, ``"production"``.
        log_level:   Python logging level string (e.g. ``"INFO"``).
    """
    sanitizer = SanitizingProcessor()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        sanitizer,
    ]

    if environment == "development":
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
# Request context middleware
# ---------------------------------------------------------------------------


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind per-request context variables to structlog for every log record.

    Bound values:
        - ``request_id`` — value of the ``X-Request-ID`` header, or a fresh
          UUID4 if the header is absent.
        - ``method``     — HTTP method (GET, POST, …).
        - ``path``       — URL path (never the full URL to avoid leaking
          query-string secrets).

    Context is cleared after the response is sent so that values from one
    request never bleed into a subsequent request handled by the same thread
    or task.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # Generate or inherit a request ID for correlation across services.
        request_id: str = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        return response
