"""pgvector ANN + Postgres full-text search against real data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from docpilot.domain.models import (
    ChunkDraft,
    Document,
    DocumentKind,
    DocumentStatus,
    EmbeddedChunk,
    Tenant,
)
from docpilot.domain.retrieval import SearchMode
from docpilot.infra.db.search import PgChunkSearch
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.services.ports import UnitOfWork
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig

PASSAGES = [
    ("Leave", "Full-time employees accrue 1.5 days of paid vacation per month of service."),
    ("Errors", "Error code E-4012 means the license key has expired and must be renewed."),
    (
        "Security",
        "Passwords must be rotated every 90 days; multi-factor authentication is mandatory.",
    ),
    ("Office", "The office kitchen is cleaned every Friday afternoon."),
]


async def seed(
    uow_factory: Callable[[], UnitOfWork], embedder: HashingEmbedder, texts: list[tuple[str, str]]
) -> tuple[UUID, list[UUID]]:
    now = datetime.now(UTC)
    tenant = Tenant(uuid4(), "search", now)
    doc_ids: list[UUID] = []
    async with uow_factory() as uow:
        await uow.tenants.add(tenant)
        for section, text in texts:
            doc = Document(
                id=uuid4(),
                tenant_id=tenant.id,
                filename=f"{section.lower()}.md",
                kind=DocumentKind.MARKDOWN,
                size_bytes=1,
                sha256=uuid4().hex,
                storage_key="k",
                status=DocumentStatus.READY,
                created_at=now,
                updated_at=now,
            )
            await uow.documents.add(doc)
            draft = ChunkDraft(0, text, 10, section)
            [vector] = await embedder.embed_documents([draft.embedding_text])
            await uow.chunks.replace_for_document(doc, [EmbeddedChunk(draft, vector)])
            doc_ids.append(doc.id)
        await uow.commit()
    return tenant.id, doc_ids


@pytest.fixture
async def corpus(uow_factory: Callable[[], UnitOfWork]) -> tuple[UUID, list[UUID]]:
    return await seed(uow_factory, HashingEmbedder(384), PASSAGES)


@pytest.fixture
def search(engine: AsyncEngine) -> PgChunkSearch:
    return PgChunkSearch(engine, 384)


async def test_vector_search_orders_by_cosine_similarity(
    search: PgChunkSearch, corpus: tuple[UUID, list[UUID]]
) -> None:
    tenant, _ = corpus
    query = await HashingEmbedder(384).embed_query("vacation days accrue per month")
    hits = await search.vector_search(tenant, query, limit=4)
    assert hits[0].section == "Leave"
    assert hits[0].filename == "leave.md"
    sims = [h.vector_similarity or 0 for h in hits]
    assert sims == sorted(sims, reverse=True)
    assert -1 <= sims[-1] <= sims[0] <= 1


async def test_keyword_search_ors_terms_and_ranks_by_coverage(
    search: PgChunkSearch, corpus: tuple[UUID, list[UUID]]
) -> None:
    tenant, _ = corpus
    hits = await search.keyword_search(
        tenant, "What does error E-4012 mean for my license?", limit=5
    )
    assert hits[0].section == "Errors"
    assert 0 < (hits[0].keyword_rank or 0) < 1
    # Stemming: "rotating passwords" matches "Passwords must be rotated".
    assert (await search.keyword_search(tenant, "rotating passwords", 5))[0].section == "Security"
    # Only stop words -> no query -> no matches (and no SQL error).
    assert await search.keyword_search(tenant, "what is the", 5) == []


async def test_searches_are_tenant_isolated_and_document_filterable(
    search: PgChunkSearch, corpus: tuple[UUID, list[UUID]], uow_factory: Callable[[], UnitOfWork]
) -> None:
    tenant, doc_ids = corpus
    other, _ = await seed(uow_factory, HashingEmbedder(384), [("Leave", "Vacation is 40 days.")])
    query = await HashingEmbedder(384).embed_query("vacation")
    mine = await search.vector_search(tenant, query, limit=10)
    assert all(h.document_id in doc_ids for h in mine)
    theirs = await search.keyword_search(other, "vacation", limit=10)
    assert [h.text for h in theirs] == ["Vacation is 40 days."]

    only = [doc_ids[3]]
    assert {h.document_id for h in await search.vector_search(tenant, query, 10, only)} == set(only)
    assert await search.keyword_search(tenant, "vacation", 10, only) == []


async def test_hybrid_retriever_over_postgres(
    search: PgChunkSearch, corpus: tuple[UUID, list[UUID]]
) -> None:
    tenant, _ = corpus
    retriever = HybridRetriever(
        search, HashingEmbedder(384), RetrievalConfig(min_vector_similarity=0.2)
    )
    hybrid = await retriever.retrieve(tenant, "license key E-4012 expired", top_k=2)
    assert hybrid.chunks[0].section == "Errors"
    assert hybrid.vector_hits > 0
    assert hybrid.keyword_hits > 0
    keyword_only = await retriever.retrieve(tenant, "kitchen", top_k=2, mode=SearchMode.KEYWORD)
    assert [c.section for c in keyword_only.chunks] == ["Office"]
