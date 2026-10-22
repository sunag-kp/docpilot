"""HTTP contract for /v1/query and /v1/query/stream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from docpilot.domain.answers import GenerationRequest
from docpilot.domain.errors import GenerationUnavailableError
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.services.ports import LLM
from tests.unit.api_helpers import MD, signup

HANDBOOK = MD + b"\n## Remote work\nStaff may work remotely three days per week.\n"


async def indexed(client: httpx.AsyncClient, bus: InMemoryEventBus) -> dict[str, str]:
    headers = await signup(client)
    response = await client.post(
        "/v1/documents", files={"file": ("h.md", HANDBOOK)}, headers=headers
    )
    assert response.status_code == 202
    await bus.drain()
    return headers


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_json_query_returns_cited_answer(
    client: httpx.AsyncClient, bus: InMemoryEventBus
) -> None:
    headers = await indexed(client, bus)
    response = await client.post(
        "/v1/query", json={"question": "How many vacation days per month?"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "1.5 vacation days per month [1]" in body["answer"]
    assert body["cited_numbers"] == [1]
    assert body["citations"][0]["section"] == "Handbook > Vacation"
    assert body["grounded"] is True
    assert body["model"] == "extractive"
    assert body["cached"] is False
    again = await client.post(
        "/v1/query", json={"question": "how many vacation days per month"}, headers=headers
    )
    assert again.json()["cached"] is True


async def test_stream_emits_meta_tokens_done(
    client: httpx.AsyncClient, bus: InMemoryEventBus
) -> None:
    headers = await indexed(client, bus)
    response = await client.post(
        "/v1/query/stream",
        json={"question": "remote work days per week", "top_k": 2},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "x-ratelimit-remaining" in response.headers
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert set(names[1:-1]) == {"token"}
    meta, done = events[0][1], events[-1][1]
    assert len(meta["citations"]) <= 2  # type: ignore[arg-type]
    assert "".join(str(data["text"]) for name, data in events if name == "token") == done["answer"]
    assert done["grounded"] is True


async def test_off_topic_question_is_refused(
    client: httpx.AsyncClient, bus: InMemoryEventBus
) -> None:
    headers = await indexed(client, bus)
    body = (
        await client.post(
            "/v1/query", json={"question": "Who won the 1998 World Cup?"}, headers=headers
        )
    ).json()
    assert body["refused"] is True
    assert body["citations"] == []


async def test_query_validation_and_auth(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/query", json={"question": "x"})).status_code == 401
    headers = await signup(client)
    for payload in (
        {"question": ""},
        {"question": "x", "top_k": 0},
        {"question": "x", "mode": "magic"},
    ):
        assert (await client.post("/v1/query", json=payload, headers=headers)).status_code == 422


class BrokenLLM:
    @property
    def name(self) -> str:
        return "broken"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        yield "partial "
        raise GenerationUnavailableError("model server is down")


@pytest.mark.parametrize("llm", [BrokenLLM()])
async def test_generation_failure_mid_stream_becomes_an_error_event(
    client: httpx.AsyncClient,
    bus: InMemoryEventBus,
    llm: LLM,
) -> None:
    headers = await indexed(client, bus)
    response = await client.post(
        "/v1/query/stream", json={"question": "vacation days per month"}, headers=headers
    )
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["meta", "token", "error"]
    assert events[-1][1] == {"type": "generation_unavailable", "detail": "model server is down"}

    failed = await client.post(
        "/v1/query", json={"question": "vacation days per month?"}, headers=headers
    )
    assert failed.status_code == 503
