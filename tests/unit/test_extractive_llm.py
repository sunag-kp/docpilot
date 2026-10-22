from __future__ import annotations

from uuid import uuid4

import pytest

from docpilot.domain.answers import REFUSAL, Citation, GenerationRequest, analyse_answer
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.llm.echo import EchoLLM
from docpilot.infra.llm.extractive import ExtractiveLLM

VACATION = (
    "Full-time employees accrue 1.5 days of paid vacation per month. "
    "Requests need manager approval two weeks ahead. The cafeteria opens at 8am."
)
SECURITY = "Passwords rotate every 90 days. Vacation cannot be used during a security incident."


def source(number: int, text: str) -> Citation:
    return Citation(
        number=number, chunk_id=uuid4(), document_id=uuid4(), filename="h.md", text=text
    )


async def ask(question: str, *sources: Citation, llm: ExtractiveLLM | None = None) -> str:
    request = GenerationRequest(question=question, sources=list(sources))
    return await (llm or ExtractiveLLM(HashingEmbedder(256), min_similarity=0.2)).compose(request)


async def test_selects_the_closest_sentences_with_citations() -> None:
    answer = await ask(
        "How many vacation days accrue per month?", source(1, VACATION), source(2, SECURITY)
    )
    assert answer.startswith("Full-time employees accrue 1.5 days of paid vacation per month [1].")
    assert "cafeteria" not in answer
    report = analyse_answer(answer, 2)
    assert report.grounded
    assert not report.refused


async def test_caps_sentences_per_source_and_overall() -> None:
    text = " ".join(f"Vacation rule number {i} applies." for i in range(10))
    llm = ExtractiveLLM(None, min_similarity=0.1, max_sentences=3, max_per_source=2, margin=1.0)
    assert (await ask("vacation rule", source(1, text), llm=llm)).count("[1]") == 2


@pytest.mark.parametrize("embedder", [None, HashingEmbedder(256)])
async def test_refuses_when_no_sentence_is_similar_enough(embedder: HashingEmbedder | None) -> None:
    llm = ExtractiveLLM(embedder, min_similarity=0.3)
    assert await ask("What is the capital of France?", source(1, VACATION), llm=llm) == REFUSAL
    assert await ask("anything", llm=llm) == REFUSAL


async def test_streams_words_that_rebuild_the_answer() -> None:
    llm = ExtractiveLLM(None, min_similarity=0.1)
    request = GenerationRequest(question="vacation per month", sources=[source(1, VACATION)])
    pieces = [p async for p in llm.stream(request)]
    assert len(pieces) >= 2  # streamed in several deltas, not one blob
    assert "".join(pieces) == await llm.compose(request)
    assert llm.name == "extractive"


async def test_echo_llm() -> None:
    llm = EchoLLM()
    request = GenerationRequest("Q?", [source(3, "x")])
    assert "".join([p async for p in llm.stream(request)]) == "You asked: Q? [3]"
    assert "".join([p async for p in llm.stream(GenerationRequest("Q?", []))]) == REFUSAL
