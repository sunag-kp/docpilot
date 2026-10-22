"""Ingestion worker use case: parse → chunk → embed → store, exactly-once in effect.

Delivery from the event bus is *at-least-once*, so the pipeline is made idempotent:

* **Lease-based claim.** A worker must atomically move the document from ``queued`` to
  ``processing`` (or take over an expired lease) before doing any work. A duplicate event
  for a document that is ready, failed, or leased by another worker is acknowledged and
  skipped.
* **Atomic replace.** Chunks are written with delete-then-insert in the same transaction
  that marks the document ``ready``, so a retry never leaves duplicate or partial chunks.
* **Bounded retries.** Transient failures back off exponentially with full jitter; the
  attempt counter lives in Postgres so the bound holds across crashes and redeliveries.
  Permanent failures (corrupt file) and exhausted retries go to the dead-letter topic.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from docpilot.domain.chunking import Chunker
from docpilot.domain.errors import PermanentIngestionError
from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.domain.models import ChunkDraft, Document, EmbeddedChunk
from docpilot.observability.logging import get_logger
from docpilot.observability.metrics import (
    INGEST_CHUNKS,
    INGEST_DEAD_LETTERS,
    INGEST_END_TO_END_SECONDS,
    INGEST_OUTCOMES,
    INGEST_REPUBLISHED,
    INGEST_RETRIES,
    INGEST_STAGE_SECONDS,
)
from docpilot.observability.tracing import timed_span, tracer
from docpilot.services.ports import (
    Clock,
    DocumentParser,
    Embedder,
    EventPublisher,
    ObjectStorage,
    Sleeper,
    UnitOfWorkFactory,
)

_log = get_logger(__name__)

CorpusChangedHook = Callable[[UUID], Awaitable[None]]


class IngestionOutcome(StrEnum):
    READY = "ready"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 20.0

    def delay(self, attempt: int, rng: random.Random | None = None) -> float:
        """Exponential backoff with full jitter (spreads retries; avoids thundering herds)."""
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))
        return (rng or random).uniform(0, ceiling)


class IngestionPipeline:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: ObjectStorage,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: Embedder,
        publisher: EventPublisher,
        clock: Clock,
        *,
        retry_policy: RetryPolicy | None = None,
        lease_seconds: int = 300,
        embed_batch_size: int = 32,
        sleep: Sleeper = asyncio.sleep,
        on_corpus_changed: CorpusChangedHook | None = None,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._publisher = publisher
        self._clock = clock
        self._retry = retry_policy or RetryPolicy()
        self._lease_seconds = lease_seconds
        self._batch_size = embed_batch_size
        self._sleep = sleep
        self._on_corpus_changed = on_corpus_changed

    async def handle(self, event: DocumentUploaded) -> IngestionOutcome:
        with tracer.start_as_current_span("ingest.document") as span:
            span.set_attribute("docpilot.document_id", str(event.document_id))
            outcome = await self._handle(event)
            span.set_attribute("docpilot.outcome", outcome.value)
        INGEST_OUTCOMES.labels(outcome.value).inc()
        return outcome

    async def _handle(self, event: DocumentUploaded) -> IngestionOutcome:
        bound = _log.bind(document_id=str(event.document_id), tenant_id=str(event.tenant_id))
        while True:
            async with self._uow() as uow:
                document = await uow.documents.claim(
                    event.document_id, lease_seconds=self._lease_seconds
                )
                await uow.commit()
            if document is None:
                bound.info("ingest_skipped_not_claimable")
                return IngestionOutcome.SKIPPED

            try:
                chunk_count = await self._process(document)
            except Exception as exc:
                retryable = getattr(exc, "retryable", True)
                error = f"{type(exc).__name__}: {exc}"
                if not retryable or document.attempts >= self._retry.max_attempts:
                    await self._dead_letter(event, document, exc, error)
                    bound.warning(
                        "ingest_failed",
                        attempts=document.attempts,
                        error=error,
                        retryable=retryable,
                    )
                    return IngestionOutcome.FAILED
                async with self._uow() as uow:
                    await uow.documents.release_for_retry(document.id, error)
                    await uow.commit()
                delay = self._retry.delay(document.attempts)
                INGEST_RETRIES.inc()
                bound.warning(
                    "ingest_retrying",
                    attempt=document.attempts,
                    delay_s=round(delay, 2),
                    error=error,
                )
                await self._sleep(delay)
                continue

            INGEST_CHUNKS.inc(chunk_count)
            INGEST_END_TO_END_SECONDS.observe(
                max(0.0, (self._clock.now() - event.occurred_at).total_seconds())
            )
            if self._on_corpus_changed is not None:
                await self._on_corpus_changed(document.tenant_id)
            bound.info("ingest_ready", chunks=chunk_count, attempts=document.attempts)
            return IngestionOutcome.READY

    async def _process(self, document: Document) -> int:
        with timed_span("ingest.fetch", INGEST_STAGE_SECONDS, "fetch"):
            data = await self._storage.get(document.storage_key)
        with timed_span("ingest.parse", INGEST_STAGE_SECONDS, "parse", kind=document.kind.value):
            # Parsing is CPU-bound; keep the event loop responsive for other partitions.
            parsed = await asyncio.to_thread(self._parser.parse, data, document.kind)
        with timed_span("ingest.chunk", INGEST_STAGE_SECONDS, "chunk"):
            drafts = self._chunker.chunk(parsed)
        if not drafts:
            raise PermanentIngestionError("document contains no extractable text")
        with timed_span("ingest.embed", INGEST_STAGE_SECONDS, "embed", chunks=len(drafts)):
            embedded = await self._embed(drafts)
        with timed_span("ingest.store", INGEST_STAGE_SECONDS, "store"):
            async with self._uow() as uow:
                await uow.chunks.replace_for_document(document, embedded)
                await uow.documents.mark_ready(document.id, parsed.page_count, len(embedded))
                await uow.commit()
        return len(embedded)

    async def _embed(self, drafts: list[ChunkDraft]) -> list[EmbeddedChunk]:
        embedded: list[EmbeddedChunk] = []
        for start in range(0, len(drafts), self._batch_size):
            batch = drafts[start : start + self._batch_size]
            vectors = await self._embedder.embed_documents([d.embedding_text for d in batch])
            embedded.extend(
                EmbeddedChunk(draft=d, embedding=v) for d, v in zip(batch, vectors, strict=True)
            )
        return embedded

    async def _dead_letter(
        self, event: DocumentUploaded, document: Document, exc: Exception, error: str
    ) -> None:
        async with self._uow() as uow:
            await uow.documents.mark_failed(document.id, error)
            await uow.commit()
        await self._publisher.publish_dead_letter(
            DeadLetter(
                event=event,
                error_type=type(exc).__name__,
                error_message=str(exc),
                attempts=document.attempts,
            )
        )
        INGEST_DEAD_LETTERS.inc()


class StaleDocumentSweeper:
    """Re-publishes documents that were never processed.

    Covers two gaps: the API committed the row but crashed (or the broker was down) before
    publishing, and a worker died mid-document so its lease expired. Re-publishing is safe
    because the pipeline's claim makes duplicates no-ops — a lightweight alternative to a
    full transactional outbox.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        publisher: EventPublisher,
        clock: Clock,
        *,
        stale_after_seconds: int,
        batch_size: int = 100,
    ) -> None:
        self._uow = uow_factory
        self._publisher = publisher
        self._clock = clock
        self._stale_after = timedelta(seconds=stale_after_seconds)
        self._batch_size = batch_size

    async def sweep_once(self) -> int:
        async with self._uow() as uow:
            stale = await uow.documents.touch_stale(
                older_than=self._clock.now() - self._stale_after, limit=self._batch_size
            )
            await uow.commit()
        for document in stale:
            await self._publisher.publish_document_uploaded(
                DocumentUploaded(
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    storage_key=document.storage_key,
                    kind=document.kind,
                    filename=document.filename,
                )
            )
        if stale:
            INGEST_REPUBLISHED.inc(len(stale))
            _log.info("stale_documents_republished", count=len(stale))
        return len(stale)

    async def run(self, interval_seconds: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.sweep_once()
            except Exception:
                _log.exception("sweeper_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
