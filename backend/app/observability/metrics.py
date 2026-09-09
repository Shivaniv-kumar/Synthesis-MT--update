"""OpenTelemetry custom metrics for Meeting Action Tracker.

Provides a MeterProvider backed by an OTLP exporter and an
``ExtractionMetrics`` singleton that encapsulates all application-level
instruments (histograms, counters, gauges).

Usage::

    from app.observability.metrics import setup_metrics, get_extraction_metrics

    # Call once at startup:
    setup_metrics()

    # In service code:
    m = get_extraction_metrics()
    m.extraction_latency.record(latency_ms, {"model": model_name, "tenant_id": tid})
    m.extraction_tokens_input.add(input_tokens, {"model": model_name})
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource


def setup_metrics() -> None:
    """Configure a global MeterProvider backed by OTLP.

    Reads the OTLP endpoint from the ``OTLP_ENDPOINT`` environment variable
    (default: ``http://localhost:4317``).  Call this once during application
    startup, before any call to :func:`get_extraction_metrics`.
    """
    otlp_endpoint: str = os.environ.get("OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource.create(
        {
            "service.name": "meeting-action-tracker",
            "service.version": "0.1.0",
        }
    )

    exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=60_000)

    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


# ---------------------------------------------------------------------------
# ExtractionMetrics singleton
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_instance: Optional["ExtractionMetrics"] = None


class ExtractionMetrics:
    """All custom OTel instruments used by the extraction pipeline.

    Instruments are created lazily on first access and shared across the
    process lifetime via the module-level singleton returned by
    :func:`get_extraction_metrics`.

    Attribute labels used consistently across instruments:
        - ``model``       — LLM model identifier
        - ``tenant_id``   — opaque workspace / tenant UUID string
        - ``status``      — "success" | "error"
    """

    def __init__(self) -> None:
        meter = metrics.get_meter("meeting_action_tracker", version="0.1.0")

        # -----------------------------------------------------------------
        # Extraction pipeline instruments
        # -----------------------------------------------------------------

        self.extraction_latency = meter.create_histogram(
            name="extraction.latency",
            unit="ms",
            description="Time to extract action items from a transcript",
        )

        self.extraction_tokens_input = meter.create_counter(
            name="extraction.tokens.input",
            unit="tokens",
            description="Total input tokens consumed by extraction calls",
        )

        self.extraction_tokens_output = meter.create_counter(
            name="extraction.tokens.output",
            unit="tokens",
            description="Total output tokens produced by extraction calls",
        )

        self.extraction_items = meter.create_histogram(
            name="extraction.items",
            description="Number of action items extracted per run",
        )

        self.extraction_confidence = meter.create_histogram(
            name="extraction.confidence",
            description="Confidence score (0–1) of extracted action items",
        )

        # Fraction of items that are flagged for human review — updated
        # as an observable gauge so it reflects the current state rather
        # than cumulative counts.
        self._review_rate_value: float = 0.0

        def _observe_review_rate(options: metrics.CallbackOptions):  # type: ignore[type-arg]
            yield metrics.Observation(self._review_rate_value)

        self.extraction_review_rate = meter.create_observable_gauge(
            name="extraction.review_rate",
            callbacks=[_observe_review_rate],
            description="Fraction (0–1) of extracted items needing human review",
        )

        # -----------------------------------------------------------------
        # API / platform instruments
        # -----------------------------------------------------------------

        self.api_request_duration = meter.create_histogram(
            name="api.request.duration",
            unit="ms",
            description="End-to-end HTTP request duration",
        )

        self.notification_sent = meter.create_counter(
            name="notification.sent",
            description="Total notifications dispatched (email, Slack, webhook …)",
        )

        self.tenant_cost_usd = meter.create_counter(
            name="tenant.cost",
            unit="USD",
            description="Cumulative LLM spend per tenant in US dollars",
        )

    def update_review_rate(self, rate: float) -> None:
        """Update the observable gauge backing value.

        Args:
            rate: Value in [0, 1] representing the current fraction of items
                  flagged for review within the recent window.
        """
        self._review_rate_value = max(0.0, min(1.0, rate))


def get_extraction_metrics() -> ExtractionMetrics:
    """Return the process-wide :class:`ExtractionMetrics` singleton.

    Thread-safe.  Creates the singleton on first call, so :func:`setup_metrics`
    must have been called before this function is first invoked in order for
    the instruments to be wired to the correct MeterProvider.
    """
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ExtractionMetrics()
    return _instance
