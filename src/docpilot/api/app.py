"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from docpilot import __version__
from docpilot.api.errors import install_error_handlers
from docpilot.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from docpilot.api.routes import auth, documents, health, query
from docpilot.config import EventBusBackend, Settings, get_settings
from docpilot.container import Container, build_container
from docpilot.observability.logging import configure_logging, get_logger
from docpilot.observability.tracing import configure_tracing

ContainerFactory = Callable[[Settings], Awaitable[Container]]

_log = get_logger(__name__)
_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def create_app(
    settings: Settings | None = None,
    container_factory: ContainerFactory = build_container,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.log_json)
        configure_tracing(settings, service_suffix="api")
        container = await container_factory(settings)
        app.state.container = container
        worker = None
        # The in-memory bus has no other consumer, so it always runs the worker in-process.
        if settings.run_worker_in_process or settings.event_bus is EventBusBackend.MEMORY:
            from docpilot.worker import IngestionWorker  # noqa: PLC0415

            if container.consumer_factory is not None:
                worker = IngestionWorker(container)
                worker.start()
        _log.info("api_started", env=settings.app_env.value, version=__version__)
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await container.aclose()
            _log.info("api_stopped")

    app = FastAPI(
        title="DocPilot API",
        version=__version__,
        description=(
            "Multi-tenant document Q&A with hybrid retrieval and streaming, cited answers. "
            "Authenticate with `Authorization: Bearer <JWT or API key>` or `X-API-Key`."
        ),
        lifespan=lifespan,
    )
    install_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "Retry-After", "X-RateLimit-Remaining"],
        )
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + _MULTIPART_OVERHEAD_BYTES
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    ui = Path(settings.ui_dist_path)
    if (ui / "index.html").is_file():
        # Same-origin UI: no CORS, and the API routes above always take precedence.
        app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
    return app
