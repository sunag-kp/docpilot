from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from uuid import uuid4

import pytest

from docpilot.domain.retrieval import ScoredChunk, SearchMode
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig
from tests.fakes import InMemoryChunkSearch

TENANT = uuid4()


@pytest.fixture
async def search() -> InMemoryChunkSearch:
    s = InMemoryChunkSearch(HashingEmbedder(256))
    await s.add(TENANT, "Employees accrue 1.5 vacation days per month.", section="Vacation")
    await s.add(TENANT, "Error code E-4012 means the license expired.", section="Errors")
    await s.add(TENANT, "The kitchen is cleaned every Friday.", section="Office")
    await s.add(uuid4(), "Another tenant's vacation policy: 40 days.")  # must never leak
    return s


def retriever(search: InMemoryChunkSearch, **cfg: float) -> HybridRetriever:
    return HybridRetriever(search, HashingEmbedder(256), RetrievalConfig(**cfg))  # type: ignore[arg-type]


async def test_hybrid_runs_both_searches_and_fuses(search: InMemoryChunkSearch) -> None:
    result = await retriever(search).retrieve(TENANT, "vacation days per month", top_k=2)
    assert sorted(search.calls) == ["keyword", "vector"]
    assert result.chunks[0].section == "Vacation"
    assert result.chunks[0].keyword_rank is not None
    assert result.chunks[0].vector_similarity is not None
    assert {"embed_query", "vector_search", "keyword_search"} <= set(result.timings_ms)
    assert all("Another tenant" not in c.text for c in result.chunks)


@pytest.mark.parametrize(
    ("mode", "calls"), [(SearchMode.VECTOR, ["vector"]), (SearchMode.KEYWORD, ["keyword"])]
)
async def test_single_mode_ablations(
    search: InMemoryChunkSearch, mode: SearchMode, calls: list[str]
) -> None:
    result = await retriever(search).retrieve(TENANT, "E-4012 license", top_k=3, mode=mode)
    assert search.calls == calls
    assert result.chunks[0].section == "Errors"


async def test_irrelevant_question_retrieves_nothing(search: InMemoryChunkSearch) -> None:
    result = await retriever(search, min_vector_similarity=0.3).retrieve(
        TENANT, "capital of France", top_k=5
    )
    assert result.chunks == []


async def test_top_k_and_document_filter(search: InMemoryChunkSearch) -> None:
    only = search.corpus[2][1].document_id
    result = await retriever(search, min_vector_similarity=-1).retrieve(
        TENANT, "cleaned vacation", top_k=5, document_ids=[only]
    )
    assert [c.document_id for c in result.chunks] == [only]


class ReverseReranker:
    def __init__(self) -> None:
        self.seen: list[int] = []

    async def rerank(self, query: str, chunks: Sequence[ScoredChunk]) -> list[ScoredChunk]:
        self.seen.append(len(chunks))
        return [replace(c, rerank_score=float(i)) for i, c in enumerate(reversed(chunks))]


async def test_reranker_reorders_shortlist_and_can_be_skipped(search: InMemoryChunkSearch) -> None:
    reranker = ReverseReranker()
    r = HybridRetriever(
        search, HashingEmbedder(256), RetrievalConfig(min_vector_similarity=-1), reranker
    )
    reranked = await r.retrieve(TENANT, "vacation", top_k=3)
    assert reranker.seen == [3]
    assert reranked.chunks[0].rerank_score == 0.0
    assert "rerank" in reranked.timings_ms
    plain = await r.retrieve(TENANT, "vacation", top_k=3, rerank=False)
    assert reranker.seen == [3]
    assert plain.chunks[0].rerank_score is None
