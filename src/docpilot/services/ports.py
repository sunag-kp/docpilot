"""Ports: the interfaces the application layer depends on.

Services only ever see these Protocols. Concrete adapters live in ``docpilot.infra`` and are
wired together in ``docpilot.container``; unit tests substitute in-memory fakes. The
import-linter contract in pyproject.toml enforces that services never import infra.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.domain.models import (
    ApiKey,
    Chunk,
    Document,
    DocumentKind,
    DocumentStatus,
    EmbeddedChunk,
    ParsedDocument,
    Tenant,
    User,
)

# --- persistence -----------------------------------------------------------------------


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...
    async def get(self, tenant_id: UUID) -> Tenant | None: ...


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...
    async def get_by_email(self, email: str) -> User | None: ...


class ApiKeyRepository(Protocol):
    async def add(self, key: ApiKey, key_hash: str) -> None: ...
    async def get_active_by_hash(self, key_hash: str) -> ApiKey | None: ...
    async def list_for_tenant(self, tenant_id: UUID) -> list[ApiKey]: ...
    async def revoke(self, tenant_id: UUID, key_id: UUID, at: datetime) -> bool: ...


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> Document:
        """Insert; if (tenant, sha256) already exists return the existing row instead."""
        ...

    async def get(self, tenant_id: UUID, document_id: UUID) -> Document | None: ...
    async def get_by_sha256(self, tenant_id: UUID, sha256: str) -> Document | None: ...
    async def list_for_tenant(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]: ...
    async def delete(self, tenant_id: UUID, document_id: UUID) -> Document | None: ...

    async def claim(self, document_id: UUID, lease_seconds: int) -> Document | None:
        """Atomically move queued (or lease-expired processing) -> processing.

        Returns ``None`` when another worker holds the lease or the document is already
        terminal — this is what makes at-least-once delivery safe to process twice.
        """
        ...

    async def release_for_retry(self, document_id: UUID, error: str) -> None: ...
    async def mark_ready(self, document_id: UUID, page_count: int | None, chunks: int) -> None: ...
    async def mark_failed(self, document_id: UUID, error: str) -> None: ...
    async def requeue(self, tenant_id: UUID, document_id: UUID) -> Document | None: ...
    async def touch_stale(self, older_than: datetime, limit: int) -> list[Document]:
        """Lock and bump ``updated_at`` of documents stuck since ``older_than``.

        Stuck = queued but never picked up, or processing with an expired lease.
        """
        ...


class ChunkRepository(Protocol):
    async def replace_for_document(
        self, document: Document, chunks: Sequence[EmbeddedChunk]
    ) -> None: ...
    async def list_for_document(self, document_id: UUID) -> list[Chunk]: ...


class UnitOfWork(Protocol):
    """One database transaction spanning any number of repositories."""

    # Read-only properties (not attributes) so implementations may narrow the types.
    @property
    def tenants(self) -> TenantRepository: ...
    @property
    def users(self) -> UserRepository: ...
    @property
    def api_keys(self) -> ApiKeyRepository: ...
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def chunks(self) -> ChunkRepository: ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]


# --- blobs, events, models -------------------------------------------------------------


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class EventPublisher(Protocol):
    async def publish_document_uploaded(self, event: DocumentUploaded) -> None: ...
    async def publish_dead_letter(self, letter: DeadLetter) -> None: ...


EventHandler = Callable[[DocumentUploaded], Awaitable[None]]


class EventConsumer(Protocol):
    async def run(self, handler: EventHandler) -> None:
        """Consume until ``stop()``; commit an event only after ``handler`` returns."""
        ...

    async def stop(self) -> None: ...


class DocumentParser(Protocol):
    def parse(self, data: bytes, kind: DocumentKind) -> ParsedDocument:
        """Raise ``PermanentIngestionError`` for corrupt or empty input."""
        ...


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


# --- security --------------------------------------------------------------------------


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, encoded: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject_id: UUID
    tenant_id: UUID
    expires_at: datetime


class TokenCodec(Protocol):
    def issue(self, subject_id: UUID, tenant_id: UUID) -> tuple[str, datetime]: ...
    def decode(self, token: str) -> TokenClaims:
        """Raise ``AuthenticationError`` if invalid or expired."""
        ...


# --- rate limiting ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class RateLimiter(Protocol):
    async def acquire(self, key: str, cost: int = 1) -> RateLimitDecision: ...


# --- misc ------------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> datetime: ...


Sleeper = Callable[[float], Awaitable[None]]
TokenStream = AsyncIterator[str]
