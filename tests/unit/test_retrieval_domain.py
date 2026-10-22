from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from docpilot.domain.retrieval import ScoredChunk, is_relevant, reciprocal_rank_fusion


def chunk(cid: UUID | None = None, **kw: Any) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=cid or uuid4(), document_id=uuid4(), filename="f.md", index=0, text="t", **kw
    )


def test_rrf_scores_follow_the_formula_and_merge_evidence() -> None:
    a, b, c = uuid4(), uuid4(), uuid4()
    vector = [chunk(a, vector_similarity=0.9), chunk(b, vector_similarity=0.8)]
    keyword = [chunk(b, keyword_rank=0.5), chunk(c, keyword_rank=0.4)]
    fused = reciprocal_rank_fusion([vector, keyword], k=60)

    assert fused[0].chunk_id == b  # ranked in both lists beats rank 1 in one list
    assert fused[0].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].vector_similarity == 0.8
    assert fused[0].keyword_rank == 0.5  # evidence from both lists is kept
    assert {f.chunk_id for f in fused} == {a, b, c}


def test_rrf_ties_are_broken_by_evidence_not_by_random_ids() -> None:
    a, b = uuid4(), uuid4()
    vector = [chunk(a, vector_similarity=0.9), chunk(b, vector_similarity=0.5)]
    keyword = [chunk(b, keyword_rank=0.8), chunk(a, keyword_rank=0.4)]
    one = reciprocal_rank_fusion([vector, keyword])
    two = reciprocal_rank_fusion([keyword, vector])
    assert one[0].fused_score == one[1].fused_score  # a genuine tie
    assert [c.chunk_id for c in one] == [c.chunk_id for c in two] == [a, b]


def test_rrf_handles_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"keyword_rank": 0.01}, True),
        ({"vector_similarity": 0.5}, True),
        ({"vector_similarity": 0.1}, False),
        ({}, False),
        ({"keyword_rank": 0.0, "vector_similarity": 0.05}, False),
    ],
)
def test_relevance_gate(kw: dict[str, float], expected: bool) -> None:
    assert is_relevant(chunk(**kw), min_vector_similarity=0.15) is expected


def test_score_prefers_rerank_score() -> None:
    assert chunk(fused_score=0.1, rerank_score=3.0).score == 3.0
    assert chunk(fused_score=0.1).score == 0.1
