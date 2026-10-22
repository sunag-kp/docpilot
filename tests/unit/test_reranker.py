from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from docpilot.domain.retrieval import ScoredChunk
from docpilot.infra.rerank import CrossEncoderReranker


class FakeCrossEncoder:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> Iterable[float]:
        self.inputs.append((query, documents))
        return [float("kitchen" in d) * 5 - i for i, d in enumerate(documents)]


async def test_cross_encoder_reorders_by_model_score_and_sees_sections() -> None:
    model = FakeCrossEncoder()
    reranker = CrossEncoderReranker("fake", model=model)
    chunks = [
        ScoredChunk(uuid4(), uuid4(), "a.md", i, text, section=section, fused_score=1 / (i + 1))
        for i, (text, section) in enumerate(
            [("Vacation days", "Leave"), ("Cleaned weekly", "kitchen")]
        )
    ]
    ranked = await reranker.rerank("when is the kitchen cleaned", chunks)
    assert [c.section for c in ranked] == ["kitchen", "Leave"]
    assert ranked[0].score == ranked[0].rerank_score == 4.0
    assert model.inputs[0][1] == ["Leave\nVacation days", "kitchen\nCleaned weekly"]
    assert await reranker.rerank("q", []) == []
