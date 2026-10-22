"""Provider adapters against mocked HTTP: request shape, streaming parse, error mapping."""

from __future__ import annotations

import json
from uuid import uuid4

import anthropic
import httpx
import httpx2
import pytest

from docpilot.domain.answers import Citation, GenerationRequest
from docpilot.domain.errors import GenerationUnavailableError
from docpilot.infra.llm.hosted import AnthropicLLM, OpenAILLM
from docpilot.infra.llm.ollama import OllamaLLM

REQUEST = GenerationRequest(
    question="How many vacation days?",
    sources=[Citation(1, uuid4(), uuid4(), "h.md", "Employees get 20 days.")],
    max_tokens=300,
)


def sse(*events: tuple[str | None, dict[str, object] | str]) -> bytes:
    out = []
    for name, data in events:
        body = data if isinstance(data, str) else json.dumps(data)
        out.append((f"event: {name}\n" if name else "") + f"data: {body}\n\n")
    return "".join(out).encode()


async def collect(llm: object) -> str:
    return "".join([piece async for piece in llm.stream(REQUEST)])  # type: ignore[attr-defined]


# --- Ollama ----------------------------------------------------------------------------


async def test_ollama_streams_ndjson_and_sends_prompt() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        lines = [
            {"message": {"content": "Employees get "}, "done": False},
            {"message": {"content": "20 days [1]."}, "done": False},
            {"message": {"content": ""}, "done": True},
        ]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    llm = OllamaLLM("qwen2.5:1.5b", transport=httpx.MockTransport(handle))
    assert await collect(llm) == "Employees get 20 days [1]."
    assert llm.name == "ollama:qwen2.5:1.5b"
    assert seen["model"] == "qwen2.5:1.5b"
    messages = seen["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert '<source id="1"' in messages[1]["content"]
    assert seen["options"] == {"temperature": 0.0, "num_predict": 300}
    await llm.aclose()


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "model not found"})),
        httpx.MockTransport(lambda r: httpx.Response(200, content=b'{"error":"out of memory"}')),
    ],
)
async def test_ollama_errors_map_to_unavailable(transport: httpx.MockTransport) -> None:
    with pytest.raises(GenerationUnavailableError):
        await collect(OllamaLLM("m", transport=transport))


async def test_ollama_unreachable_maps_to_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(GenerationUnavailableError, match="unreachable"):
        await collect(OllamaLLM("m", transport=httpx.MockTransport(refuse)))


# --- OpenAI ----------------------------------------------------------------------------


async def test_openai_streams_chat_completion_deltas() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.update(json.loads(request.content))
        chunk = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m"}
        body = sse(
            (
                None,
                {
                    **chunk,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "20 "},
                            "finish_reason": None,
                        }
                    ],
                },
            ),
            (
                None,
                {
                    **chunk,
                    "choices": [
                        {"index": 0, "delta": {"content": "days [1]."}, "finish_reason": "stop"}
                    ],
                },
            ),
            (None, "[DONE]"),
        )
        return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    llm = OpenAILLM("sk-test", "gpt-test", http_client=client)
    assert await collect(llm) == "20 days [1]."
    assert seen["stream"] is True
    assert seen["max_completion_tokens"] == 300
    assert llm.name == "openai:gpt-test"


# --- Anthropic -------------------------------------------------------------------------


def anthropic_stream(request: httpx2.Request, seen: dict[str, object]) -> httpx2.Response:
    seen["body"] = json.loads(request.content)
    seen["headers"] = dict(request.headers)
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "anthropic-test-model",
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 1},
    }
    body = sse(
        ("message_start", {"type": "message_start", "message": message}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Employees get "},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "20 days [1]."},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 9},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    return httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})


async def test_anthropic_streams_text_with_effort_fallbacks_and_no_sampling_params() -> None:
    seen: dict[str, object] = {}
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: anthropic_stream(r, seen)))
    llm = AnthropicLLM("sk-ant-test", "anthropic-test-model", http_client=client)
    assert await collect(llm) == "Employees get 20 days [1]."
    assert llm.name == "anthropic:anthropic-test-model"

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "anthropic-test-model"
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert body["max_tokens"] == 8000  # thinking tokens count toward max_tokens
    assert "temperature" not in body  # rejected by the Messages API
    assert body["system"].startswith("You are DocPilot")
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]


async def test_anthropic_overload_maps_to_unavailable() -> None:
    def overloaded(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(overloaded))
    llm = AnthropicLLM("sk-ant-test", "anthropic-test-model", http_client=client)
    llm._client = llm._client.with_options(max_retries=0)
    with pytest.raises(GenerationUnavailableError):
        await collect(llm)


async def test_anthropic_client_errors_are_not_masked_as_unavailable() -> None:
    def bad_request(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(bad_request))
    llm = AnthropicLLM("sk-ant-test", "anthropic-test-model", http_client=client)
    with pytest.raises(anthropic.BadRequestError):
        await collect(llm)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_openai_transient_errors_map_to_unavailable(status: int) -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(status, json={"error": {"message": "x"}})
        )
    )
    llm = OpenAILLM("sk-test", "gpt-test", http_client=client)
    llm._client = llm._client.with_options(max_retries=0)
    with pytest.raises(GenerationUnavailableError):
        await collect(llm)
