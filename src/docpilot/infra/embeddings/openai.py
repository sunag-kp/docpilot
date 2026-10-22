"""Optional hosted embeddings. Only constructed when OPENAI_API_KEY is configured."""

from __future__ import annotations

from collections.abc import Sequence

import httpx2
import openai

from docpilot.domain.errors import PermanentIngestionError, TransientIngestionError


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        dim: int,
        base_url: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=2, http_client=http_client
        )
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                model=self._model, input=list(texts), dimensions=self._dim
            )
        except (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            raise TransientIngestionError(f"embedding provider unavailable: {exc}") from exc
        except openai.APIStatusError as exc:
            raise PermanentIngestionError(f"embedding request rejected: {exc}") from exc
        return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]
