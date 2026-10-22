"""Deterministic mock LLM for tests: cites the first source, or refuses without sources."""

from __future__ import annotations

from collections.abc import AsyncIterator

from docpilot.domain.answers import REFUSAL, GenerationRequest


class EchoLLM:
    @property
    def name(self) -> str:
        return "echo"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        if not request.sources:
            yield REFUSAL
            return
        for piece in ("You asked: ", request.question, " ", f"[{request.sources[0].number}]"):
            yield piece
