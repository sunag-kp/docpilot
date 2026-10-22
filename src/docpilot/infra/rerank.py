"""Optional cross-encoder re-ranking with a local fastembed model (RERANKER=cross_encoder).

A cross-encoder reads (question, passage) *together*, so it judges relevance far better
than comparing two independently computed embeddings — but it costs one model pass per
pair, which is why it only re-scores the fused short-list, never the corpus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from docpilot.domain.retrieval import ScoredChunk


class CrossEncoderReranker:
    def __init__(self, model_name: str, *, cache_dir: str | None = None, model: Any = None) -> None:
        if model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415

            model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
        self._model = model
        self._lock = asyncio.Semaphore(1)

    async def rerank(self, query: str, chunks: Sequence[ScoredChunk]) -> list[ScoredChunk]:
        if not chunks:
            return []
        texts = [f"{c.section}\n{c.text}" if c.section else c.text for c in chunks]
        async with self._lock:
            scores = await asyncio.to_thread(lambda: list(self._model.rerank(query, texts)))
        rescored = [replace(c, rerank_score=float(s)) for c, s in zip(chunks, scores, strict=True)]
        return sorted(rescored, key=lambda c: -(c.rerank_score or 0.0))
