"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from docpilot import __version__
from docpilot.api.middleware import RequestContextMiddleware
from docpilot.api.routes import health
from docpilot.config import Settings, get_settings
from docpilot.container import Container, build_container
from docpilot.observability.logging import configure_logging, get_logger

ContainerFactory = Callable[[Settings], Awaitable[Container]]

_log = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    container_factory: ContainerFactory = build_container,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.log_json)
        container = await container_factory(settings)
        app.state.container = container
        _log.info("api_started", env=settings.app_env.value, version=__version__)
        try:
            yield
        finally:
            await container.aclose()
            _log.info("api_stopped")

    app = FastAPI(
        title="DocPilot API",
        version=__version__,
        description="Multi-tenant document Q&A with hybrid retrieval and streaming, cited answers.",
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "Retry-After"],
        )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app
