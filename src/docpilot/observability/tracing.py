"""OpenTelemetry tracing setup and helpers.

Application code only uses the vendor-neutral OpenTelemetry *API*; when tracing is
disabled the API is a no-op, so spans cost nothing. ``configure_tracing`` installs the SDK
with an OTLP/HTTP exporter (Jaeger in docker-compose).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from prometheus_client import Histogram

from docpilot.config import Settings

tracer = trace.get_tracer("docpilot")
_configured = False


def configure_tracing(settings: Settings, *, service_suffix: str) -> None:
    global _configured  # noqa: PLW0603 - process-wide SDK install, exactly once
    if not settings.otel_enabled or _configured:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    resource = Resource.create(
        {
            "service.name": f"{settings.service_name}-{service_suffix}",
            "deployment.environment": settings.app_env.value,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        )
    )
    trace.set_tracer_provider(provider)
    _configured = True


@contextmanager
def timed_span(name: str, histogram: Histogram, label: str, **attributes: object) -> Iterator[None]:
    """Open a span *and* observe the stage duration in a Prometheus histogram."""
    started = time.perf_counter()
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if isinstance(value, str | bool | int | float):
                span.set_attribute(f"docpilot.{key}", value)
        try:
            yield
        finally:
            histogram.labels(label).observe(time.perf_counter() - started)
