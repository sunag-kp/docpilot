from __future__ import annotations

import json

import httpx2 as httpx  # the OpenAI SDK is built on the httpx2 fork
import pytest

from docpilot.domain.errors import PermanentIngestionError, TransientIngestionError
from docpilot.infra.embeddings.openai import OpenAIEmbedder


def embedder(handler: httpx.MockTransport) -> OpenAIEmbedder:
    client = httpx.AsyncClient(transport=handler)
    return OpenAIEmbedder("sk-test", "text-embedding-3-small", dim=3, http_client=client)


async def test_requests_configured_dimensions_and_orders_by_index() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        data = [
            {"object": "embedding", "index": 1, "embedding": [0.0, 1.0, 0.0]},
            {"object": "embedding", "index": 0, "embedding": [1.0, 0.0, 0.0]},
        ]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "m",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    vectors = await embedder(httpx.MockTransport(handle)).embed_documents(["a", "b"])
    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert seen["dimensions"] == 3


@pytest.mark.parametrize(
    ("status", "error"), [(429, TransientIngestionError), (400, PermanentIngestionError)]
)
async def test_provider_errors_map_to_retry_semantics(status: int, error: type[Exception]) -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(status, json={"error": {"message": "x"}})
    )
    instance = embedder(transport)
    instance._client = instance._client.with_options(max_retries=0)
    with pytest.raises(error):
        await instance.embed_query("hi")
