"""Optional hosted providers. Constructed only when their API key is configured."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import anthropic
import httpx2
import openai
from anthropic.types.beta import BetaOutputConfigParam

from docpilot.domain.answers import GenerationRequest
from docpilot.domain.errors import GenerationUnavailableError

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class OpenAILLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=2,
            http_client=http_client,
        )

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                stream=True,
                max_completion_tokens=request.max_tokens,
                temperature=request.temperature,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.render_user_message()},
                ],
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise GenerationUnavailableError(f"openai unavailable: {exc}") from exc


class AnthropicLLM:
    """Anthropic Messages API via the official SDK, streamed.

    Current models think adaptively, and thinking tokens count against ``max_tokens``,
    so the budget here is larger than the answer length; ``effort`` sets
    how much the model thinks (``low`` suits extracting answers from given passages).
    Sampling parameters are not sent: the Messages API rejects ``temperature`` now.
    Server-side refusal fallbacks are on, so a safety-classifier decline is re-run on a
    fallback model inside the same call instead of returning an empty answer.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        effort: Effort = "low",
        max_tokens: int = 8000,
        timeout_seconds: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._output_config: BetaOutputConfigParam = {"effort": effort}
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=2, http_client=http_client
        )

    @property
    def name(self) -> str:
        return f"anthropic:{self._model}"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=max(self._max_tokens, request.max_tokens),
                system=request.system,
                messages=[{"role": "user", "content": request.render_user_message()}],
                output_config=self._output_config,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except anthropic.APIConnectionError as exc:
            raise GenerationUnavailableError(f"anthropic unreachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            # 429 and 5xx (incl. 529 overloaded) are transient; 4xx are configuration bugs.
            if _retryable(exc.status_code):
                raise GenerationUnavailableError(f"anthropic unavailable: {exc}") from exc
            raise


def _retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500
