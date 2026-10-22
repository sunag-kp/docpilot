from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from docpilot.domain.answers import REFUSAL, GenerationRequest
from docpilot.domain.retrieval import SearchMode
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.llm.extractive import ExtractiveLLM
from docpilot.services.answering import (
    AnswerCompleted,
    AnswerDelta,
    AnswerEvent,
    AnswerStarted,
    QueryRequest,
    QuestionAnswering,
    cache_keys,
)
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig
from tests.fakes import InMemoryAnswerCache, InMemoryChunkSearch

TENANT = uuid4()


class SpyLLM:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []
        self.inner = ExtractiveLLM()

    @property
    def name(self) -> str:
        return "spy"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        async for piece in self.inner.stream(request):
            yield piece


@pytest.fixture
async def setup() -> tuple[QuestionAnswering, SpyLLM, InMemoryAnswerCache]:
    search = InMemoryChunkSearch(HashingEmbedder(256))
    await search.add(TENANT, "Employees accrue 1.5 vacation days per month.", section="Vacation")
    await search.add(TENANT, "Remote work is allowed three days per week.", section="Remote")
    llm, cache = SpyLLM(), InMemoryAnswerCache()
    retriever = HybridRetriever(
        search, HashingEmbedder(256), RetrievalConfig(min_vector_similarity=0.2)
    )
    return QuestionAnswering(retriever, llm, cache), llm, cache


async def collect(qa: QuestionAnswering, question: str, tenant: UUID = TENANT) -> list[AnswerEvent]:
    return [e async for e in qa.answer(tenant, QueryRequest(question=question, top_k=3))]


async def test_answer_streams_started_deltas_completed(setup) -> None:  # type: ignore[no-untyped-def]
    qa, llm, _ = setup
    events = await collect(qa, "How many vacation days per month?")
    started, *deltas, completed = events
    assert isinstance(started, AnswerStarted)
    assert isinstance(completed, AnswerCompleted)
    assert all(isinstance(d, AnswerDelta) for d in deltas)
    assert started.citations[0].section == "Vacation"
    assert "".join(d.text for d in deltas) == completed.answer  # type: ignore[union-attr]
    assert completed.cited_numbers == [1]
    assert completed.grounded
    assert not completed.cached
    assert "time_to_first_token" in completed.timings_ms
    assert len(llm.requests) == 1


async def test_no_relevant_context_refuses_without_calling_the_llm(setup) -> None:  # type: ignore[no-untyped-def]
    qa, llm, _ = setup
    events = await collect(qa, "What is the capital of France?")
    completed = events[-1]
    assert isinstance(completed, AnswerCompleted)
    assert completed.answer == REFUSAL
    assert completed.refused
    assert llm.requests == []


async def test_repeat_and_normalised_questions_hit_the_cache(setup) -> None:  # type: ignore[no-untyped-def]
    qa, llm, _ = setup
    first = (await collect(qa, "How many vacation days per month?"))[-1]
    exact = (await collect(qa, "How many vacation days per month?"))[-1]
    normalised = (await collect(qa, "  how many VACATION days per month "))[-1]
    assert isinstance(exact, AnswerCompleted)
    assert isinstance(normalised, AnswerCompleted)
    assert exact.cached
    assert normalised.cached
    assert normalised.answer == first.answer  # type: ignore[union-attr]
    assert len(llm.requests) == 1


async def test_corpus_change_invalidates_cached_answers(setup) -> None:  # type: ignore[no-untyped-def]
    qa, llm, cache = setup
    await collect(qa, "remote work days")
    await cache.bump_corpus_version(TENANT)  # a document was added or deleted
    again = (await collect(qa, "remote work days"))[-1]
    assert isinstance(again, AnswerCompleted)
    assert not again.cached
    assert len(llm.requests) == 2


def test_cache_keys_scope_by_tenant_version_and_options() -> None:
    base = QueryRequest(question="Q?", top_k=5)
    keys = cache_keys(TENANT, 3, base)
    assert keys[0].startswith(f"qa:v1:{TENANT}:3:exact:")
    assert keys[1].startswith(f"qa:v1:{TENANT}:3:norm:")
    assert cache_keys(TENANT, 3, QueryRequest(question="q", top_k=5))[1] == keys[1]
    assert cache_keys(TENANT, 3, QueryRequest(question="q", top_k=5))[0] != keys[0]
    for variant in (
        QueryRequest(question="Q?", top_k=6),
        QueryRequest(question="Q?", top_k=5, mode=SearchMode.VECTOR),
        QueryRequest(question="Q?", top_k=5, document_ids=(uuid4(),)),
    ):
        assert cache_keys(TENANT, 3, variant)[1] != keys[1]
    assert cache_keys(uuid4(), 3, base) != keys
    assert cache_keys(TENANT, 4, base) != keys
