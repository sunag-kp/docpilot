"""In-process event bus for tests and single-process mode (EVENT_BUS=memory)."""

from __future__ import annotations

import asyncio

from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.observability.logging import get_logger
from docpilot.services.ports import EventHandler

_log = get_logger(__name__)


class InMemoryEventBus:
    def __init__(self) -> None:
        self.published: list[DocumentUploaded] = []
        self.dead_letters: list[DeadLetter] = []
        self._queue: asyncio.Queue[DocumentUploaded] = asyncio.Queue()
        self._stopping = asyncio.Event()

    async def publish_document_uploaded(self, event: DocumentUploaded) -> None:
        self.published.append(event)
        self._queue.put_nowait(event)

    async def publish_dead_letter(self, letter: DeadLetter) -> None:
        self.dead_letters.append(letter)

    async def run(self, handler: EventHandler) -> None:
        while not self._stopping.is_set():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            try:
                await handler(event)
            except Exception:
                _log.exception("memory_bus_handler_failed", document_id=str(event.document_id))
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        self._stopping.set()
