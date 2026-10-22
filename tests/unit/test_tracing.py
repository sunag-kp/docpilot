"""Tracing wiring: enabling OTel installs an SDK provider and instruments the app."""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace

from docpilot.api.app import instrument_app
from docpilot.config import Settings
from docpilot.observability import tracing


def test_tracing_is_a_no_op_when_disabled(settings: Settings) -> None:
    app = FastAPI()
    tracing.configure_tracing(settings, service_suffix="api")
    instrument_app(app, settings)
    tracing.instrument_clients(settings)
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_enabling_tracing_installs_the_sdk_and_instruments(settings: Settings) -> None:
    enabled = settings.model_copy(update={"otel_enabled": True})
    tracing.configure_tracing(enabled, service_suffix="api")
    tracing.configure_tracing(enabled, service_suffix="api")  # idempotent
    provider = trace.get_tracer_provider()
    assert type(provider).__module__.startswith("opentelemetry.sdk")
    app = FastAPI()
    instrument_app(app, enabled)
    assert getattr(app, "_is_instrumented_by_opentelemetry", False)
    tracing.instrument_clients(enabled)
    tracing.instrument_clients(enabled)  # idempotent
