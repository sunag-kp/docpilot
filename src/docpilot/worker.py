"""Ingestion worker process: ``docpilot-worker`` / ``python -m docpilot.worker``.

Runs the event consumer (parse → chunk → embed → store) and the stale-document sweeper,
exposes Prometheus metrics on ``WORKER_METRICS_PORT`` and shuts down gracefully on SIGTERM:
the in-flight document finishes and its offset is committed before the process exits.
"""

from __future__ import annotations

import asyncio
import signal

from prometheus_client import start_http_server

from docpilot.config import Settings, get_settings
from docpilot.container import Container, build_container
from docpilot.domain.events import DocumentUploaded
from docpilot.observability.logging import configure_logging, get_logger
from docpilot.observability.tracing import configure_tracing

_log = get_logger(__name__)


class IngestionWorker:
    """Owns the consumer and sweeper tasks for one process."""

    def __init__(self, container: Container, *, sweep_interval_seconds: float = 30.0) -> None:
        assert container.pipeline is not None
        assert container.sweeper is not None
        assert container.consumer_factory is not None
        self._pipeline = container.pipeline
        self._sweeper = container.sweeper
        self._consumer = container.consumer_factory()
        self._sweep_interval = sweep_interval_seconds
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def _handle(self, event: DocumentUploaded) -> None:
        await self._pipeline.handle(event)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._consumer.run(self._handle), name="ingest-consumer"),
            asyncio.create_task(
                self._sweeper.run(self._sweep_interval, self._stop), name="stale-sweeper"
            ),
        ]
        _log.info("ingestion_worker_started")

    async def stop(self) -> None:
        self._stop.set()
        await self._consumer.stop()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                _log.error("worker_task_failed", error=repr(result))
        _log.info("ingestion_worker_stopped")

    async def wait(self) -> None:
        await self._stop.wait()


async def serve(settings: Settings) -> None:
    configure_logging(settings.log_level, json=settings.log_json)
    configure_tracing(settings, service_suffix="worker")
    if settings.metrics_enabled and settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    container = await build_container(settings)
    worker = IngestionWorker(container)
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_requested.set)
    worker.start()
    try:
        await stop_requested.wait()
        _log.info("shutdown_requested")
    finally:
        await worker.stop()
        await container.aclose()


def run() -> None:
    asyncio.run(serve(get_settings()))


if __name__ == "__main__":
    run()
