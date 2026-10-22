from __future__ import annotations

import random
from collections.abc import Callable
from uuid import uuid4

import pytest

from docpilot.domain.chunking import Chunker, ChunkingConfig
from docpilot.domain.events import DocumentUploaded
from docpilot.domain.models import DocumentKind, DocumentStatus, Principal
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.parsing import DefaultDocumentParser
from docpilot.infra.storage.local import InMemoryObjectStorage
from docpilot.services.documents import DocumentService
from docpilot.services.ingestion import (
    IngestionOutcome,
    IngestionPipeline,
    RetryPolicy,
    StaleDocumentSweeper,
)
from docpilot.services.ports import Embedder, UnitOfWork
from tests.fakes import FixedClock, FlakyEmbedder, InMemoryState

ALICE = Principal(tenant_id=uuid4(), subject_id=uuid4(), kind="user")
HANDBOOK = b"""# Handbook

## Vacation
Employees accrue 1.5 days of paid vacation per month. Requests need manager approval.

## Remote work
Staff may work remotely three days per week.
"""


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_pipeline(
    uow_factory: Callable[[], UnitOfWork],
    storage: InMemoryObjectStorage,
    bus: InMemoryEventBus,
    clock: FixedClock,
    embedder: Embedder,
    sleeps: Sleeps,
    changed: list[object] | None = None,
) -> IngestionPipeline:
    async def hook(tenant_id: object) -> None:
        if changed is not None:
            changed.append(tenant_id)

    return IngestionPipeline(
        uow_factory,
        storage,
        DefaultDocumentParser(),
        Chunker(ChunkingConfig(max_tokens=64, min_tokens=8)),
        embedder,
        bus,
        clock,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=4),
        embed_batch_size=1,
        sleep=sleeps,
        on_corpus_changed=hook,
    )


async def upload(
    service: DocumentService, data: bytes = HANDBOOK, name: str = "h.md"
) -> DocumentUploaded:
    result = await service.upload(ALICE, name, data)
    return DocumentUploaded(
        document_id=result.document.id,
        tenant_id=ALICE.tenant_id,
        storage_key=result.document.storage_key,
        kind=result.document.kind,
        filename=result.document.filename,
    )


async def test_happy_path_parses_chunks_embeds_and_marks_ready(
    document_service, uow_factory, storage, bus, clock, embedder, state: InMemoryState
) -> None:  # type: ignore[no-untyped-def]
    changed: list[object] = []
    pipeline = make_pipeline(uow_factory, storage, bus, clock, embedder, Sleeps(), changed)
    event = await upload(document_service)

    assert await pipeline.handle(event) is IngestionOutcome.READY

    doc = state.documents[event.document_id]
    assert (doc.status, doc.attempts, doc.chunk_count) == (DocumentStatus.READY, 1, 2)
    chunks = state.chunks[event.document_id]
    assert [c.section for c, _ in chunks] == ["Handbook > Vacation", "Handbook > Remote work"]
    assert all(len(vector) == 64 for _, vector in chunks)
    assert changed == [ALICE.tenant_id]


async def test_duplicate_delivery_is_a_no_op(
    document_service, uow_factory, storage, bus, clock, embedder, state: InMemoryState
) -> None:  # type: ignore[no-untyped-def]
    pipeline = make_pipeline(uow_factory, storage, bus, clock, embedder, Sleeps())
    event = await upload(document_service)
    assert await pipeline.handle(event) is IngestionOutcome.READY
    first_chunks = [c.id for c, _ in state.chunks[event.document_id]]

    assert await pipeline.handle(event) is IngestionOutcome.SKIPPED  # at-least-once redelivery
    assert [c.id for c, _ in state.chunks[event.document_id]] == first_chunks
    assert state.documents[event.document_id].attempts == 1


async def test_event_for_unknown_document_is_skipped(
    uow_factory, storage, bus, clock, embedder
) -> None:  # type: ignore[no-untyped-def]
    pipeline = make_pipeline(uow_factory, storage, bus, clock, embedder, Sleeps())
    ghost = DocumentUploaded(uuid4(), uuid4(), "k", DocumentKind.TEXT, "x.txt")
    assert await pipeline.handle(ghost) is IngestionOutcome.SKIPPED


async def test_transient_failures_back_off_then_succeed(
    document_service, uow_factory, storage, bus, clock, state: InMemoryState
) -> None:  # type: ignore[no-untyped-def]
    sleeps = Sleeps()
    flaky = FlakyEmbedder(HashingEmbedder(64), failures=2)
    pipeline = make_pipeline(uow_factory, storage, bus, clock, flaky, sleeps)
    event = await upload(document_service)

    assert await pipeline.handle(event) is IngestionOutcome.READY
    doc = state.documents[event.document_id]
    assert (doc.status, doc.attempts, doc.error) == (DocumentStatus.READY, 3, None)
    assert len(sleeps.calls) == 2
    assert 0 <= sleeps.calls[0] <= 0.5
    assert 0 <= sleeps.calls[1] <= 1.0
    assert bus.dead_letters == []


async def test_exhausted_retries_dead_letter_and_fail(
    document_service, uow_factory, storage, bus, clock, state: InMemoryState
) -> None:  # type: ignore[no-untyped-def]
    flaky = FlakyEmbedder(HashingEmbedder(64), failures=99)
    pipeline = make_pipeline(uow_factory, storage, bus, clock, flaky, Sleeps())
    event = await upload(document_service)

    assert await pipeline.handle(event) is IngestionOutcome.FAILED
    doc = state.documents[event.document_id]
    assert (doc.status, doc.attempts) == (DocumentStatus.FAILED, 3)
    assert "TransientIngestionError" in (doc.error or "")
    [letter] = bus.dead_letters
    assert (letter.event.document_id, letter.attempts) == (event.document_id, 3)


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("bad.pdf", b"%PDF-1.4 garbage", "corrupt"),
        ("headings.md", b"# Only headings\n## Nothing else\n", "no extractable text"),
    ],
)
async def test_permanent_errors_dead_letter_without_retry(
    document_service,
    uow_factory,
    storage,
    bus,
    clock,
    embedder,
    state: InMemoryState,
    name: str,
    data: bytes,
    message: str,
) -> None:  # type: ignore[no-untyped-def]
    sleeps = Sleeps()
    pipeline = make_pipeline(uow_factory, storage, bus, clock, embedder, sleeps)
    event = await upload(document_service, data, name)

    assert await pipeline.handle(event) is IngestionOutcome.FAILED
    assert sleeps.calls == []
    assert state.documents[event.document_id].attempts == 1
    assert message in (state.documents[event.document_id].error or "")
    assert bus.dead_letters[0].error_type == "PermanentIngestionError"


async def test_expired_lease_can_be_taken_over(
    document_service, uow_factory, storage, bus, clock, embedder, state: InMemoryState
) -> None:  # type: ignore[no-untyped-def]
    pipeline = make_pipeline(uow_factory, storage, bus, clock, embedder, Sleeps())
    event = await upload(document_service)
    async with uow_factory() as uow:  # a worker claims it, then "crashes"
        assert await uow.documents.claim(event.document_id, lease_seconds=60) is not None
        await uow.commit()

    assert await pipeline.handle(event) is IngestionOutcome.SKIPPED  # lease still held
    clock.advance(61)
    assert await pipeline.handle(event) is IngestionOutcome.READY
    assert state.documents[event.document_id].attempts == 2


async def test_sweeper_republishes_stuck_documents(
    document_service, uow_factory, bus, clock
) -> None:  # type: ignore[no-untyped-def]
    event = await upload(document_service)
    sweeper = StaleDocumentSweeper(uow_factory, bus, clock, stale_after_seconds=600)
    assert await sweeper.sweep_once() == 0  # fresh uploads are left alone

    clock.advance(601)
    assert await sweeper.sweep_once() == 1
    assert bus.published[-1].document_id == event.document_id
    assert await sweeper.sweep_once() == 0  # touched: not re-sent on the next tick


def test_retry_delay_is_bounded_exponential_with_jitter() -> None:
    policy = RetryPolicy(max_attempts=10, base_delay_seconds=1, max_delay_seconds=8)
    rng = random.Random(7)
    for attempt, ceiling in [(1, 1), (2, 2), (3, 4), (4, 8), (9, 8)]:
        delays = [policy.delay(attempt, rng) for _ in range(200)]
        assert all(0 <= d <= ceiling for d in delays)
        assert max(delays) > ceiling * 0.8  # jitter actually spreads across the window
