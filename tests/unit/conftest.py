from __future__ import annotations

from collections.abc import Callable

import pytest

from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.security import JwtTokenCodec, ScryptPasswordHasher, SystemClock
from docpilot.infra.storage.local import InMemoryObjectStorage
from docpilot.services.auth import AuthService
from docpilot.services.documents import DocumentService
from docpilot.services.ports import UnitOfWork
from tests.fakes import FixedClock, InMemoryState, InMemoryUnitOfWork


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
