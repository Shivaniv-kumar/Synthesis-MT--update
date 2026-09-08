"""OpenTelemetry tracing setup for Meeting Action Tracker.

Initialises a TracerProvider backed by an OTLP gRPC exporter, instruments
FastAPI automatically, and provides small helpers used throughout the service.

Usage::

    # In lifespan / app factory:
    from app.observability.tracing import setup_tracing
    setup_tracing(app, settings)

    # In service code:
    from app.observability.tracing import get_tracer, add_span_attrs

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my.operation") as span:
        add_span_attrs(meeting_id=str(mid), model=model_name)
        ...
"""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Keys whose names contain any of these substrings must never be recorded in
# span attributes — they may carry PII, secrets, or large text blobs.
_BLOCKED_ATTR_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "transcript",
        "content",
        "token",
        "key",
        "password",
        "secret",
    }
)


def _is_sensitive(attr_name: str) -> bool:
    """Return True if *attr_name* matches any blocked substring (case-insensitive)."""
    lower = attr_name.lower()
    return any(blocked in lower for blocked in _BLOCKED_ATTR_SUBSTRINGS)


def setup_tracing(app: Any, settings: Any) -> None:  # app: FastAPI, settings: Settings
    """Configure OpenTelemetry tracing and instrument the FastAPI application.

    Args:
        app:      The FastAPI application instance.
        settings: The application Settings (from ``app.config.get_settings()``).
                  Only used here for service metadata; OTLP endpoint is read
                  from the ``OTLP_ENDPOINT`` environment variable.
    """
    otlp_endpoint: str = os.environ.get(
        "OTLP_ENDPOINT", "http://localhost:4317"
    )

    resource = Resource.create(
        {
            "service.name": "meeting-action-tracker",
            "service.version": "0.1.0",
            "deployment.environment": getattr(settings, "environment", "development"),
        }
    )

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    processor = BatchSpanProcessor(exporter)

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)

    # Instrument FastAPI — this adds spans for every request automatically.
    FastAPIInstrumentor().instrument_app(app)


def add_span_attrs(**kwargs: Any) -> None:
    """Attach *kwargs* as attributes on the current active span.

    Any key whose name contains a sensitive substring is silently dropped so
    that transcripts, API keys, and PII never reach the tracing backend.

    Safe to call even when there is no active span (no-op in that case).
    """
    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return

    for key, value in kwargs.items():
        if _is_sensitive(key):
            continue
        # OpenTelemetry attribute values must be scalar or sequences of scalars.
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
        else:
            # Coerce to string for complex types to avoid SDK errors.
            span.set_attribute(key, str(value))


def get_tracer(name: str) -> trace.Tracer:
    """Return a named :class:`opentelemetry.trace.Tracer`.

    This is a thin convenience wrapper around ``trace.get_tracer`` so call
    sites do not need to import the OpenTelemetry SDK directly.

    Args:
        name: Typically ``__name__`` of the calling module.
    """
    return trace.get_tracer(name)
