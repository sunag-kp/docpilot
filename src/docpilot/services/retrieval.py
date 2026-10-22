"""Hybrid retrieval: full-text + vector search fused with RRF, then optional re-ranking."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from docpilot.domain.retrieval import (
    RetrievalResult,
    ScoredChunk,
    SearchMode,
    is_relevant,
    reciprocal_rank_fusion,
)
from docpilot.observability.metrics import QUERY_STAGE_SECONDS
from docpilot.observability.tracing import timed_span
from docpilot.services.ports import ChunkSearch, Embedder, Reranker


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    candidates: int = 30
    rrf_k: int = 60
    min_vector_similarity: float = 0.15


class HybridRetriever:
    """Why hybrid: embeddings catch paraphrases ("time off" ~ "vacation") while full-text
    catches exact tokens embeddings blur (error codes, product names, numbers). Each
    retrieves ``candidates`` chunks; RRF merges the two rankings; a cross-encoder may then
    re-score the merged short-list, which is too slow to run over the whole corpus.
    """

    def __init__(
        self,
        search: ChunkSearch,
        embedder: Embedder,
        config: RetrievalConfig | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._search = search
        self._embedder = embedder
        self._config = config or RetrievalConfig()
        self._reranker = reranker

    async def retrieve(
        self,
        tenant_id: UUID,
        question: str,
        *,
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        document_ids: Sequence[UUID] | None = None,
        rerank: bool = True,
    ) -> RetrievalResult:
        timings: dict[str, float] = {}
        cfg = self._config
        vector_hits: list[ScoredChunk] = []
        keyword_hits: list[ScoredChunk] = []

        async def run_vector() -> list[ScoredChunk]:
            with timed_span("retrieval.embed_query", QUERY_STAGE_SECONDS, "embed_query"):
                started = time.perf_counter()
                embedding = await self._embedder.embed_query(question)
                timings["embed_query"] = _ms(started)
            with timed_span("retrieval.vector_search", QUERY_STAGE_SECONDS, "vector_search"):
                started = time.perf_counter()
                hits = await self._search.vector_search(
                    tenant_id, embedding, cfg.candidates, document_ids
                )
                timings["vector_search"] = _ms(started)
            return hits

        async def run_keyword() -> list[ScoredChunk]:
            with timed_span("retrieval.keyword_search", QUERY_STAGE_SECONDS, "keyword_search"):
                started = time.perf_counter()
                hits = await self._search.keyword_search(
                    tenant_id, question, cfg.candidates, document_ids
                )
                timings["keyword_search"] = _ms(started)
            return hits

        if mode is SearchMode.HYBRID:
            vector_hits, keyword_hits = await asyncio.gather(run_vector(), run_keyword())
        elif mode is SearchMode.VECTOR:
            vector_hits = await run_vector()
        else:
            keyword_hits = await run_keyword()

        rankings = [r for r in (vector_hits, keyword_hits) if r]
        fused = reciprocal_rank_fusion(rankings, k=cfg.rrf_k)
        relevant = [c for c in fused if is_relevant(c, cfg.min_vector_similarity)]

        if rerank and self._reranker is not None and relevant:
            with timed_span("retrieval.rerank", QUERY_STAGE_SECONDS, "rerank"):
                started = time.perf_counter()
                relevant = await self._reranker.rerank(question, relevant[: max(top_k * 3, 10)])
                timings["rerank"] = _ms(started)

        return RetrievalResult(
            chunks=relevant[:top_k],
            vector_hits=len(vector_hits),
            keyword_hits=len(keyword_hits),
            timings_ms=timings,
        )


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
