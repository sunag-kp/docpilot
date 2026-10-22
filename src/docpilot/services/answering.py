"""Question answering: cache → hybrid retrieval → grounded generation, as a stream of events.

The service yields typed events (started → deltas → completed); the API layer only turns
them into Server-Sent Events. Keeping transport out of here means the same flow serves the
streaming endpoint, the JSON endpoint and the evaluation harness.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from docpilot.domain.answers import (
    REFUSAL,
    Citation,
    GenerationRequest,
    analyse_answer,
)
from docpilot.domain.retrieval import SearchMode
from docpilot.domain.text import normalize_question
from docpilot.observability.logging import get_logger
from docpilot.observability.metrics import (
    CACHE_LOOKUPS,
    LLM_TOKENS,
    QUERIES,
    QUERY_STAGE_SECONDS,
    QUERY_TIME_TO_FIRST_TOKEN,
)
from docpilot.observability.tracing import tracer
from docpilot.services.ports import LLM, AnswerCache, CachedAnswer
from docpilot.services.retrieval import HybridRetriever

_log = get_logger(__name__)
_CACHE_SCHEMA = "v1"  # bump to invalidate every cached answer after a prompt change


@dataclass(frozen=True, slots=True)
class QueryRequest:
    question: str
    top_k: int = 5
    mode: SearchMode = SearchMode.HYBRID
    document_ids: tuple[UUID, ...] = ()
    rerank: bool = True


@dataclass(frozen=True, slots=True)
class AnswerStarted:
    query_id: UUID
    citations: list[Citation]
    cached: bool
    model: str


@dataclass(frozen=True, slots=True)
class AnswerDelta:
    text: str


@dataclass(frozen=True, slots=True)
class AnswerCompleted:
    query_id: UUID
    answer: str
    cited_numbers: list[int]
    refused: bool
    grounded: bool
    cached: bool
    model: str
    latency_ms: float
    timings_ms: dict[str, float] = field(default_factory=dict)


AnswerEvent = AnswerStarted | AnswerDelta | AnswerCompleted


def cache_keys(tenant_id: UUID, corpus_version: int, request: QueryRequest) -> list[str]:
    """Exact key first, then a normalised one ("What's X?" == "what's x").

    The tenant's corpus version is part of the key, so uploading or deleting a document
    makes every cached answer for that tenant unreachable — no stale answers, no scans.
    """
    scope = "|".join(
        [
            str(request.top_k),
            request.mode.value,
            ",".join(sorted(str(d) for d in request.document_ids)),
            str(request.rerank),
        ]
    )
    prefix = f"qa:{_CACHE_SCHEMA}:{tenant_id}:{corpus_version}"
    exact = hashlib.sha256(f"{request.question.strip()}|{scope}".encode()).hexdigest()
    normalized = hashlib.sha256(
        f"{normalize_question(request.question)}|{scope}".encode()
    ).hexdigest()
    return [f"{prefix}:exact:{exact}", f"{prefix}:norm:{normalized}"]


class QuestionAnswering:
    def __init__(
        self,
        retriever: HybridRetriever,
        llm: LLM,
        cache: AnswerCache | None,
        *,
        max_tokens: int = 700,
        temperature: float = 0.0,
    ) -> None:
        self._retriever = retriever
        self._llm = llm
        self._cache = cache
        self._max_tokens = max_tokens
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._llm.name

    async def answer(self, tenant_id: UUID, request: QueryRequest) -> AsyncIterator[AnswerEvent]:
        started = time.perf_counter()
        query_id = uuid4()
        with tracer.start_as_current_span("query.answer") as span:
            span.set_attribute("docpilot.query_id", str(query_id))
            keys: list[str] = []
            if self._cache is not None:
                version = await self._cache.corpus_version(tenant_id)
                keys = cache_keys(tenant_id, version, request)
                cached = await self._cache.get(keys)
                CACHE_LOOKUPS.labels("hit" if cached else "miss").inc()
                if cached is not None:
                    span.set_attribute("docpilot.cache_hit", True)
                    async for event in self._replay(query_id, cached, started):
                        yield event
                    return

            retrieval = await self._retriever.retrieve(
                tenant_id,
                request.question,
                top_k=request.top_k,
                mode=request.mode,
                document_ids=request.document_ids or None,
                rerank=request.rerank,
            )
            citations = [Citation.from_chunk(i, c) for i, c in enumerate(retrieval.chunks, 1)]
            timings = dict(retrieval.timings_ms)
            yield AnswerStarted(query_id, citations, cached=False, model=self._llm.name)

            if not citations:
                # Nothing relevant was retrieved: refuse without spending an LLM call.
                answer = REFUSAL
                QUERY_TIME_TO_FIRST_TOKEN.observe(time.perf_counter() - started)
                yield AnswerDelta(answer)
            else:
                parts: list[str] = []
                generation_started = time.perf_counter()
                request_ = GenerationRequest(
                    question=request.question,
                    sources=citations,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
                async for delta in self._llm.stream(request_):
                    if not parts:
                        QUERY_TIME_TO_FIRST_TOKEN.observe(time.perf_counter() - started)
                        timings["time_to_first_token"] = _ms(started)
                    parts.append(delta)
                    LLM_TOKENS.labels(self._llm.name).inc()
                    yield AnswerDelta(delta)
                answer = "".join(parts).strip() or REFUSAL
                QUERY_STAGE_SECONDS.labels("generate").observe(
                    time.perf_counter() - generation_started
                )
                timings["generate"] = _ms(generation_started)

            report = analyse_answer(answer, len(citations))
            outcome = "refused" if report.refused else "answered"
            QUERIES.labels(outcome, "false").inc()
            span.set_attribute("docpilot.refused", report.refused)
            if self._cache is not None:
                await self._cache.set(
                    keys,
                    CachedAnswer(
                        answer=answer,
                        citations=citations,
                        refused=report.refused,
                        model=self._llm.name,
                    ),
                )
            yield AnswerCompleted(
                query_id=query_id,
                answer=answer,
                cited_numbers=report.cited_numbers,
                refused=report.refused,
                grounded=report.grounded,
                cached=False,
                model=self._llm.name,
                latency_ms=_ms(started),
                timings_ms=timings,
            )

    async def _replay(
        self, query_id: UUID, cached: CachedAnswer, started: float
    ) -> AsyncIterator[AnswerEvent]:
        yield AnswerStarted(query_id, cached.citations, cached=True, model=cached.model)
        QUERY_TIME_TO_FIRST_TOKEN.observe(time.perf_counter() - started)
        for chunk in _word_chunks(cached.answer):
            yield AnswerDelta(chunk)
        report = analyse_answer(cached.answer, len(cached.citations))
        QUERIES.labels("refused" if report.refused else "answered", "true").inc()
        yield AnswerCompleted(
            query_id=query_id,
            answer=cached.answer,
            cited_numbers=report.cited_numbers,
            refused=report.refused,
            grounded=report.grounded,
            cached=True,
            model=cached.model,
            latency_ms=_ms(started),
        )


def _word_chunks(text: str, size: int = 8) -> Sequence[str]:
    words = text.split(" ")
    return [
        " ".join(words[i : i + size]) + (" " if i + size < len(words) else "")
        for i in range(0, len(words), size)
    ]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
