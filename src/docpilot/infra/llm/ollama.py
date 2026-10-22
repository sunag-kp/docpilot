"""Local open-weights models served by Ollama (https://ollama.com) — no key, no cost."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from docpilot.domain.answers import GenerationRequest
from docpilot.domain.errors import GenerationUnavailableError


class OllamaLLM:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    @property
    def name(self) -> str:
        return f"ollama:{self._model}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "stream": True,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.render_user_message()},
            ],
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
        }
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")[:300]
                    raise GenerationUnavailableError(
                        f"ollama returned {response.status_code}: {body}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise GenerationUnavailableError(f"ollama error: {chunk['error']}")
                    text = chunk.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if chunk.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise GenerationUnavailableError(f"ollama unreachable: {exc}") from exc
