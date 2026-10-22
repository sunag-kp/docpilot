from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from asgi_lifespan import LifespanManager

from docpilot.api.app import create_app
from docpilot.config import EventBusBackend, Settings
from docpilot.container import Container
from docpilot.domain.chunking import Chunker, ChunkingConfig
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.llm.extractive import ExtractiveLLM
from docpilot.infra.parsing import DefaultDocumentParser
from docpilot.infra.security import JwtTokenCodec, ScryptPasswordHasher, SystemClock
from docpilot.infra.storage.local import InMemoryObjectStorage
from docpilot.services.answering import QuestionAnswering
from docpilot.services.auth import AuthService
from docpilot.services.documents import DocumentService
from docpilot.services.ingestion import IngestionPipeline, StaleDocumentSweeper
from docpilot.services.ports import LLM, UnitOfWork
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig
from tests.fakes import (
    FakeRateLimiter,
    FixedClock,
    InMemoryAnswerCache,
    InMemoryState,
    InMemoryUnitOfWork,
    StateChunkSearch,
)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def state() -> InMemoryState:
    return InMemoryState()


@pytest.fixture
def uow_factory(state: InMemoryState, clock: FixedClock) -> Callable[[], UnitOfWork]:
    return lambda: InMemoryUnitOfWork(state, clock)


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder(64)


@pytest.fixture
def auth_service(uow_factory: Callable[[], UnitOfWork]) -> AuthService:
    return AuthService(
        uow_factory,
        ScryptPasswordHasher(n=2**10),
        JwtTokenCodec("t" * 40, issuer="docpilot", ttl_minutes=5, clock=SystemClock()),
        SystemClock(),
    )


@pytest.fixture
def document_service(
    uow_factory: Callable[[], UnitOfWork],
    storage: InMemoryObjectStorage,
    bus: InMemoryEventBus,
    clock: FixedClock,
) -> DocumentService:
    return DocumentService(uow_factory, storage, bus, clock, max_bytes=1024 * 1024)


# --- in-memory application for HTTP contract tests ------------------------------------


@pytest.fixture
def limiter() -> FakeRateLimiter:
    return FakeRateLimiter(capacity=1000)


@pytest.fixture
def llm() -> LLM:
    return ExtractiveLLM()


@pytest.fixture
async def client(
    settings: Settings, limiter: FakeRateLimiter, bus: InMemoryEventBus, llm: LLM
) -> AsyncIterator[httpx.AsyncClient]:
    settings = settings.model_copy(update={"event_bus": EventBusBackend.MEMORY, "max_upload_mb": 1})
    clock = FixedClock()
    state = InMemoryState()
    storage = InMemoryObjectStorage()
    uow: Callable[[], UnitOfWork] = lambda: InMemoryUnitOfWork(state, clock)  # noqa: E731

    async def factory(s: Settings) -> Container:
        system = SystemClock()
        return Container(
            settings=s,
            auth=AuthService(
                uow,
                ScryptPasswordHasher(n=2**10),
                JwtTokenCodec("j" * 40, issuer="docpilot", ttl_minutes=5, clock=system),
                system,
            ),
            documents=DocumentService(uow, storage, bus, clock, max_bytes=s.max_upload_bytes),
            answering=QuestionAnswering(
                HybridRetriever(
                    StateChunkSearch(HashingEmbedder(32), state),
                    HashingEmbedder(32),
                    RetrievalConfig(min_vector_similarity=0.3),
                ),
                llm,
                InMemoryAnswerCache(),
            ),
            rate_limiter=limiter,
            pipeline=IngestionPipeline(
                uow,
                storage,
                DefaultDocumentParser(),
                Chunker(ChunkingConfig(max_tokens=64, min_tokens=8)),
                HashingEmbedder(32),
                bus,
                clock,
            ),
            sweeper=StaleDocumentSweeper(uow, bus, clock, stale_after_seconds=600),
            consumer_factory=lambda: bus,
        )

    app = create_app(settings, container_factory=factory)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
