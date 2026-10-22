"""In-memory test doubles for the service ports.

They implement the same Protocols as the SQL/Redis/Kafka adapters, so service and API
tests run in milliseconds with no infrastructure. The integration suite proves the real
adapters behave the same way.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from docpilot.domain.errors import ConflictError, TransientIngestionError
from docpilot.domain.models import (
    ApiKey,
    Chunk,
    Document,
    DocumentStatus,
    EmbeddedChunk,
    Tenant,
    User,
)
from docpilot.domain.retrieval import ScoredChunk
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.services.ports import CachedAnswer, RateLimitDecision


class FixedClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime.now(UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass
class InMemoryState:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    users: dict[UUID, User] = field(default_factory=dict)
    api_keys: dict[UUID, tuple[ApiKey, str]] = field(default_factory=dict)
    documents: dict[UUID, Document] = field(default_factory=dict)
    leases: dict[UUID, datetime] = field(default_factory=dict)
    chunks: dict[UUID, list[tuple[Chunk, list[float]]]] = field(default_factory=dict)


class _Tenants:
    def __init__(self, state: InMemoryState) -> None:
        self.s = state

    async def add(self, tenant: Tenant) -> None:
        self.s.tenants[tenant.id] = tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self.s.tenants.get(tenant_id)


class _Users:
    def __init__(self, state: InMemoryState) -> None:
        self.s = state

    async def add(self, user: User) -> None:
        if any(u.email.lower() == user.email.lower() for u in self.s.users.values()):
            raise ConflictError("an account with this email already exists")
        self.s.users[user.id] = user

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.s.users.values() if u.email.lower() == email.lower()), None)


class _ApiKeys:
    def __init__(self, state: InMemoryState) -> None:
        self.s = state

    async def add(self, key: ApiKey, key_hash: str) -> None:
        self.s.api_keys[key.id] = (key, key_hash)

    async def get_active_by_hash(self, key_hash: str) -> ApiKey | None:
        return next(
            (k for k, h in self.s.api_keys.values() if h == key_hash and k.revoked_at is None),
            None,
        )

    async def list_for_tenant(self, tenant_id: UUID) -> list[ApiKey]:
        keys = [k for k, _ in self.s.api_keys.values() if k.tenant_id == tenant_id]
        return sorted(keys, key=lambda k: k.created_at, reverse=True)

    async def revoke(self, tenant_id: UUID, key_id: UUID, at: datetime) -> bool:
        entry = self.s.api_keys.get(key_id)
        if entry is None or entry[0].tenant_id != tenant_id or entry[0].revoked_at is not None:
            return False
        self.s.api_keys[key_id] = (replace(entry[0], revoked_at=at), entry[1])
        return True


class _Documents:
    def __init__(self, state: InMemoryState, clock: FixedClock) -> None:
        self.s = state
        self.clock = clock

    def _save(self, document: Document, **changes: object) -> Document:
        updated = replace(document, updated_at=self.clock.now(), **changes)  # type: ignore[arg-type]
        self.s.documents[document.id] = updated
        return updated

    async def add(self, document: Document) -> Document:
        existing = await self.get_by_sha256(document.tenant_id, document.sha256)
        if existing is not None:
            return existing
        self.s.documents[document.id] = document
        return document

    async def get(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        document = self.s.documents.get(document_id)
        return document if document and document.tenant_id == tenant_id else None

    async def get_by_sha256(self, tenant_id: UUID, sha256: str) -> Document | None:
        return next(
            (
                d
                for d in self.s.documents.values()
                if d.tenant_id == tenant_id and d.sha256 == sha256
            ),
            None,
        )

    async def list_for_tenant(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]:
        docs = [
            d
            for d in self.s.documents.values()
            if d.tenant_id == tenant_id and (status is None or d.status == status)
        ]
        docs.sort(key=lambda d: d.created_at, reverse=True)
        return docs[offset : offset + limit], len(docs)

    async def delete(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        document = await self.get(tenant_id, document_id)
        if document is not None:
            del self.s.documents[document_id]
            self.s.chunks.pop(document_id, None)
        return document

    async def claim(self, document_id: UUID, lease_seconds: int) -> Document | None:
        document = self.s.documents.get(document_id)
        if document is None:
            return None
        lease = self.s.leases.get(document_id)
        expired = lease is not None and lease < self.clock.now()
        if document.status is DocumentStatus.QUEUED or (
            document.status is DocumentStatus.PROCESSING and expired
        ):
            self.s.leases[document_id] = self.clock.now() + timedelta(seconds=lease_seconds)
            return self._save(
                document, status=DocumentStatus.PROCESSING, attempts=document.attempts + 1
            )
        return None

    async def release_for_retry(self, document_id: UUID, error: str) -> None:
        self.s.leases.pop(document_id, None)
        self._save(self.s.documents[document_id], status=DocumentStatus.QUEUED, error=error)

    async def mark_ready(self, document_id: UUID, page_count: int | None, chunks: int) -> None:
        self.s.leases.pop(document_id, None)
        self._save(
            self.s.documents[document_id],
            status=DocumentStatus.READY,
            page_count=page_count,
            chunk_count=chunks,
            error=None,
            processed_at=self.clock.now(),
        )

    async def mark_failed(self, document_id: UUID, error: str) -> None:
        self.s.leases.pop(document_id, None)
        self._save(self.s.documents[document_id], status=DocumentStatus.FAILED, error=error)

    async def requeue(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        document = await self.get(tenant_id, document_id)
        if document is None or document.status is not DocumentStatus.FAILED:
            return None
        return self._save(document, status=DocumentStatus.QUEUED, attempts=0, error=None)

    async def touch_stale(self, older_than: datetime, limit: int) -> list[Document]:
        stale = [
            d
            for d in self.s.documents.values()
            if (d.status is DocumentStatus.QUEUED and d.updated_at < older_than)
            or (
                d.status is DocumentStatus.PROCESSING
                and self.s.leases.get(d.id, self.clock.now()) < self.clock.now()
            )
        ][:limit]
        return [self._save(d) for d in stale]


class _Chunks:
    def __init__(self, state: InMemoryState) -> None:
        self.s = state

    async def replace_for_document(
        self, document: Document, chunks: Sequence[EmbeddedChunk]
    ) -> None:
        self.s.chunks[document.id] = [
            (
                Chunk(
                    id=uuid4(),
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    index=c.draft.index,
                    text=c.draft.text,
                    token_count=c.draft.token_count,
                    section=c.draft.section,
                    page_start=c.draft.page_start,
                    page_end=c.draft.page_end,
                ),
                c.embedding,
            )
            for c in chunks
        ]

    async def list_for_document(self, document_id: UUID) -> list[Chunk]:
        return [chunk for chunk, _ in self.s.chunks.get(document_id, [])]


class InMemoryUnitOfWork:
    """Copy-on-enter, swap-on-commit: uncommitted changes are discarded like a rollback."""

    def __init__(self, state: InMemoryState, clock: FixedClock) -> None:
        self._committed = state
        self._clock = clock

    async def __aenter__(self) -> Self:
        self._working = copy.deepcopy(self._committed)
        self.tenants = _Tenants(self._working)
        self.users = _Users(self._working)
        self.api_keys = _ApiKeys(self._working)
        self.documents = _Documents(self._working, self._clock)
        self.chunks = _Chunks(self._working)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        for name in vars(self._committed):
            setattr(self._committed, name, getattr(self._working, name))


class FakeRateLimiter:
    def __init__(self, capacity: int = 1000) -> None:
        self.capacity = capacity
        self.spent: dict[str, int] = {}

    async def acquire(self, key: str, cost: int = 1) -> RateLimitDecision:
        spent = self.spent.get(key, 0)
        if spent + cost > self.capacity:
            return RateLimitDecision(False, self.capacity, self.capacity - spent, 2.5)
        self.spent[key] = spent + cost
        return RateLimitDecision(True, self.capacity, self.capacity - spent - cost, 0.0)


class FlakyEmbedder:
    """Wraps an embedder and fails the first ``failures`` calls with a transient error."""

    def __init__(self, inner: object, failures: int) -> None:
        self._inner = inner
        self.failures = failures
        self.calls = 0

    @property
    def dim(self) -> int:
        return self._inner.dim  # type: ignore[attr-defined, no-any-return]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientIngestionError("embedding backend timed out")
        return await self._inner.embed_documents(texts)  # type: ignore[attr-defined, no-any-return]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class InMemoryChunkSearch:
    """Brute-force vector + term-overlap search over an in-memory corpus."""

    def __init__(self, embedder: HashingEmbedder) -> None:
        self._embedder = embedder
        self.corpus: list[tuple[UUID, ScoredChunk, list[float]]] = []
        self.calls: list[str] = []

    async def add(self, tenant_id: UUID, text: str, *, section: str | None = None) -> ScoredChunk:
        chunk = ScoredChunk(
            chunk_id=uuid4(),
            document_id=uuid4(),
            filename="doc.md",
            index=len(self.corpus),
            text=text,
            section=section,
        )
        self.corpus.append((tenant_id, chunk, await self._embedder.embed_query(text)))
        return chunk

    async def vector_search(
        self,
        tenant_id: UUID,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        self.calls.append("vector")
        scored = [
            replace(
                chunk, vector_similarity=sum(a * b for a, b in zip(vec, embedding, strict=True))
            )
            for tid, chunk, vec in self.corpus
            if tid == tenant_id and (not document_ids or chunk.document_id in document_ids)
        ]
        scored.sort(key=lambda c: -(c.vector_similarity or 0))
        return scored[:limit]

    async def keyword_search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        from docpilot.domain.text import content_terms

        self.calls.append("keyword")
        terms = set(content_terms(query))
        hits = []
        for tid, chunk, _ in self.corpus:
            if tid != tenant_id or (document_ids and chunk.document_id not in document_ids):
                continue
            overlap = len(terms & set(content_terms(chunk.text)))
            if overlap:
                hits.append(replace(chunk, keyword_rank=overlap / (overlap + 1)))
        hits.sort(key=lambda c: -(c.keyword_rank or 0))
        return hits[:limit]


class InMemoryAnswerCache:
    def __init__(self) -> None:
        self.entries: dict[str, CachedAnswer] = {}
        self.versions: dict[UUID, int] = {}

    async def get(self, keys: Sequence[str]) -> CachedAnswer | None:
        return next((self.entries[k] for k in keys if k in self.entries), None)

    async def set(self, keys: Sequence[str], answer: CachedAnswer) -> None:
        for key in keys:
            self.entries[key] = answer

    async def corpus_version(self, tenant_id: UUID) -> int:
        return self.versions.get(tenant_id, 0)

    async def bump_corpus_version(self, tenant_id: UUID) -> None:
        self.versions[tenant_id] = self.versions.get(tenant_id, 0) + 1


class StateChunkSearch(InMemoryChunkSearch):
    """Searches the chunks the in-memory ingestion pipeline wrote into ``InMemoryState``."""

    def __init__(self, embedder: HashingEmbedder, state: InMemoryState) -> None:
        super().__init__(embedder)
        self._state = state

    def _sync(self) -> None:
        self.corpus = [
            (
                chunk.tenant_id,
                ScoredChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    filename=self._state.documents[chunk.document_id].filename,
                    index=chunk.index,
                    text=chunk.text,
                    section=chunk.section,
                ),
                vector,
            )
            for chunks in self._state.chunks.values()
            for chunk, vector in chunks
            if chunk.document_id in self._state.documents
        ]

    async def vector_search(
        self,
        tenant_id: UUID,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        self._sync()
        return await super().vector_search(tenant_id, embedding, limit, document_ids)

    async def keyword_search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        self._sync()
        return await super().keyword_search(tenant_id, query, limit, document_ids)
