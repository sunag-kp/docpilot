"""Ask questions: JSON or streamed over Server-Sent Events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from docpilot.api import sse
from docpilot.api.deps import AnsweringDep, ContainerDep, rate_limited
from docpilot.api.schemas import CitationResponse, QueryRequestBody, QueryResponse
from docpilot.domain.errors import DomainError
from docpilot.domain.models import Principal
from docpilot.observability.logging import get_logger
from docpilot.services.answering import (
    AnswerCompleted,
    AnswerDelta,
    AnswerStarted,
    QueryRequest,
)

router = APIRouter(prefix="/v1", tags=["query"])
_log = get_logger(__name__)

_SSE_EXAMPLE = """event: meta
data: {"query_id":"…","cached":false,"model":"extractive","citations":[{"number":1,…}]}

event: token
data: {"text":"Employees accrue 1.5 days "}

event: done
data: {"answer":"…","cited_numbers":[1],"refused":false,"grounded":true,"latency_ms":41.2}
"""


def _to_request(body: QueryRequestBody, default_top_k: int) -> QueryRequest:
    return QueryRequest(
        question=body.question,
        top_k=body.top_k or default_top_k,
        mode=body.mode,
        document_ids=tuple(body.document_ids),
        rerank=body.rerank,
    )


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequestBody,
    answering: AnsweringDep,
    container: ContainerDep,
    principal: Annotated[Principal, Depends(rate_limited())],
) -> QueryResponse:
    """Answer a question from the tenant's documents (non-streaming)."""
    request = _to_request(body, container.settings.retrieval_top_k)
    started: AnswerStarted | None = None
    async for event in answering.answer(principal.tenant_id, request):
        if isinstance(event, AnswerStarted):
            started = event
        elif isinstance(event, AnswerCompleted):
            assert started is not None
            return QueryResponse(
                query_id=event.query_id,
                answer=event.answer,
                citations=[CitationResponse.from_domain(c) for c in started.citations],
                cited_numbers=event.cited_numbers,
                refused=event.refused,
                grounded=event.grounded,
                cached=event.cached,
                model=event.model,
                latency_ms=event.latency_ms,
                timings_ms=event.timings_ms,
            )
    raise RuntimeError("answer stream ended without a completion event")  # pragma: no cover


@router.post(
    "/query/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"example": _SSE_EXAMPLE}}}},
)
async def query_stream(
    body: QueryRequestBody,
    answering: AnsweringDep,
    container: ContainerDep,
    response: Response,
    principal: Annotated[Principal, Depends(rate_limited())],
) -> StreamingResponse:
    """Stream the answer as Server-Sent Events: `meta` → `token`* → `done` (or `error`)."""
    request = _to_request(body, container.settings.retrieval_top_k)

    async def events() -> AsyncIterator[bytes]:
        try:
            async for event in answering.answer(principal.tenant_id, request):
                if isinstance(event, AnswerStarted):
                    yield sse.encode(
                        "meta",
                        {
                            "query_id": event.query_id,
                            "cached": event.cached,
                            "model": event.model,
                            "citations": [
                                CitationResponse.from_domain(c).model_dump(mode="json")
                                for c in event.citations
                            ],
                        },
                    )
                elif isinstance(event, AnswerDelta):
                    yield sse.encode("token", {"text": event.text})
                elif isinstance(event, AnswerCompleted):
                    yield sse.encode(
                        "done",
                        {
                            "query_id": event.query_id,
                            "answer": event.answer,
                            "cited_numbers": event.cited_numbers,
                            "refused": event.refused,
                            "grounded": event.grounded,
                            "cached": event.cached,
                            "model": event.model,
                            "latency_ms": event.latency_ms,
                            "timings_ms": event.timings_ms,
                        },
                    )
        except DomainError as exc:
            yield sse.encode("error", {"type": exc.code, "detail": exc.message})
        except Exception:
            _log.exception("query_stream_failed")
            yield sse.encode("error", {"type": "internal_error", "detail": "internal error"})

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # tell nginx-style proxies not to buffer the stream
        **{k: v for k, v in response.headers.items() if k.lower().startswith("x-ratelimit")},
    }
    return StreamingResponse(
        sse.with_keepalive(events()), media_type="text/event-stream", headers=headers
    )
