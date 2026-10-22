from __future__ import annotations

from uuid import uuid4

from docpilot.domain.answers import REFUSAL, Citation, GenerationRequest, analyse_answer
from docpilot.infra.llm.echo import EchoLLM
from docpilot.infra.llm.extractive import ExtractiveLLM

VACATION = (
    "Full-time employees accrue 1.5 days of paid vacation per month. "
    "Requests need manager approval two weeks ahead. The cafeteria opens at 8am."
)
SECURITY = "Passwords rotate every 90 days. Vacation cannot be used during a security incident."


def source(number: int, text: str, similarity: float = 0.5) -> Citation:
    return Citation(
        number=number,
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="h.md",
        text=text,
        vector_similarity=similarity,
    )


def ask(question: str, *sources: Citation, llm: ExtractiveLLM | None = None) -> str:
    request = GenerationRequest(question=question, sources=list(sources))
    return (llm or ExtractiveLLM()).compose(request)


def test_selects_overlapping_sentences_with_citations() -> None:
    answer = ask(
        "How many vacation days accrue per month?", source(1, VACATION), source(2, SECURITY)
    )
    assert "accrue 1.5 days of paid vacation per month [1]." in answer
    assert "cafeteria" not in answer
    report = analyse_answer(answer, 2)
    assert report.grounded
    assert not report.refused


def test_caps_sentences_per_source_and_overall() -> None:
    text = " ".join(f"Vacation rule number {i} applies." for i in range(10))
    llm = ExtractiveLLM(max_sentences=3, max_per_source=2)
    assert ask("vacation rule", source(1, text), llm=llm).count("[1]") == 2


def test_refuses_when_nothing_matches_and_similarity_is_low() -> None:
    assert ask("What is the capital of France?", source(1, VACATION, similarity=0.1)) == REFUSAL
    assert ask("anything") == REFUSAL


def test_paraphrase_fallback_uses_top_source_when_retrieval_is_confident() -> None:
    answer = ask("How much PTO?", source(1, VACATION, similarity=0.7))
    assert answer.startswith("Full-time employees accrue")
    assert answer.endswith("[1].")


async def test_streams_words_that_rebuild_the_answer() -> None:
    llm = ExtractiveLLM()
    request = GenerationRequest(question="vacation per month", sources=[source(1, VACATION)])
    pieces = [p async for p in llm.stream(request)]
    assert len(pieces) > 3
    assert "".join(pieces) == llm.compose(request)
    assert llm.name == "extractive"


async def test_echo_llm() -> None:
    llm = EchoLLM()
    request = GenerationRequest("Q?", [source(3, "x")])
    assert "".join([p async for p in llm.stream(request)]) == "You asked: Q? [3]"
    assert "".join([p async for p in llm.stream(GenerationRequest("Q?", []))]) == REFUSAL
