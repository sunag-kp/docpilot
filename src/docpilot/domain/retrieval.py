"""Retrieval value objects and rank fusion. Pure functions, no I/O."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A candidate passage plus the evidence for why it was retrieved."""

    chunk_id: UUID
    document_id: UUID
    filename: str
    index: int
    text: str
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    vector_similarity: float | None = None  # cosine similarity in [-1, 1]
    keyword_rank: float | None = None  # ts_rank_cd, normalised to [0, 1)
    fused_score: float = 0.0
    rerank_score: float | None = None

    @property
    def score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[ScoredChunk]
    vector_hits: int = 0
    keyword_hits: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[ScoredChunk]], k: int = 60
) -> list[ScoredChunk]:
    """Fuse ranked lists by RRF: ``score(d) = Σ 1 / (k + rank_i(d))`` (Cormack et al., 2009).

    RRF only looks at *ranks*, so it can combine a cosine similarity and a full-text rank
    without calibrating two incomparable score scales. ``k`` damps the advantage of the
    very top ranks; 60 is the value from the original paper and works well in practice.
    Evidence from every list is merged so callers can see both scores for a chunk.
    """
    merged: dict[UUID, ScoredChunk] = {}
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            seen = merged.get(chunk.chunk_id)
            merged[chunk.chunk_id] = (
                chunk
                if seen is None
                else replace(
                    seen,
                    vector_similarity=(
                        seen.vector_similarity
                        if seen.vector_similarity is not None
                        else chunk.vector_similarity
                    ),
                    keyword_rank=(
                        seen.keyword_rank if seen.keyword_rank is not None else chunk.keyword_rank
                    ),
                )
            )
    fused = [replace(chunk, fused_score=scores[cid]) for cid, chunk in merged.items()]
    # Ties broken deterministically by chunk id so results are reproducible.
    fused.sort(key=lambda c: (-c.fused_score, str(c.chunk_id)))
    return fused


def is_relevant(chunk: ScoredChunk, min_vector_similarity: float) -> bool:
    """Keep a chunk if either signal vouches for it.

    A keyword match is strong evidence on its own (exact terms, codes, names); otherwise the
    embedding similarity must clear a floor so off-topic questions retrieve *nothing* and
    the system refuses instead of answering from unrelated text.
    """
    if chunk.keyword_rank is not None and chunk.keyword_rank > 0:
        return True
    return chunk.vector_similarity is not None and chunk.vector_similarity >= min_vector_similarity
