"""Local CPU embeddings with fastembed (ONNX Runtime) — no API key, no GPU."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from fastembed import TextEmbedding

from docpilot.domain.errors import TransientIngestionError


class FastEmbedEmbedder:
    def __init__(
        self,
        model_name: str,
        model_path: Path,
        *,
        dim: int,
        batch_size: int = 32,
        threads: int | None = None,
        max_concurrency: int = 2,
    ) -> None:
        self._model = TextEmbedding(
            model_name=model_name, specific_model_path=str(model_path), threads=threads
        )
        self._batch_size = batch_size
        # ONNX Runtime parallelises inside one call; bounding concurrent calls avoids
        # oversubscribing CPU cores when many requests embed at once.
        self._semaphore = asyncio.Semaphore(max_concurrency)
        probe = self._embed_sync(["dimension probe"])[0]
        if len(probe) != dim:
            raise ValueError(f"{model_name} produces {len(probe)}-d vectors, EMBEDDING_DIM={dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_sync(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            vector.tolist()
            for vector in self._model.embed(list(texts), batch_size=self._batch_size)
        ]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        async with self._semaphore:
            try:
                return await asyncio.to_thread(self._embed_sync, texts)
            except Exception as exc:  # pragma: no cover - ONNX runtime failures are rare
                raise TransientIngestionError(f"embedding failed: {exc}") from exc

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]
