"""In-process LRU cache in front of any embedder.

Query-time work repeats a lot: the extractive answerer embeds the sentences of the top
passages, and popular passages come back for many questions. Caching by exact text turns
those into dictionary lookups. Vectors are stored as float32 arrays (~1.5 KB for 384-d,
versus ~10 KB as a list of Python floats), so 10k entries cost about 15 MB.
"""

from __future__ import annotations

from array import array
from collections import OrderedDict
from collections.abc import Sequence

from prometheus_client import Counter

from docpilot.services.ports import Embedder

EMBEDDING_CACHE = Counter(
    "docpilot_embedding_cache_lookups_total", "Embedding cache lookups", ["result"]
)


class CachingEmbedder:
    def __init__(self, inner: Embedder, *, max_entries: int = 10_000) -> None:
        self._inner = inner
        self._max_entries = max_entries
        self._entries: OrderedDict[str, array[float]] = OrderedDict()

    @property
    def dim(self) -> int:
        return self._inner.dim

    def __len__(self) -> int:
        return len(self._entries)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}
        for i, text in enumerate(texts):
            cached = self._entries.get(text)
            if cached is not None:
                self._entries.move_to_end(text)
                results[i] = cached.tolist()
            else:
                missing.setdefault(text, []).append(i)
        EMBEDDING_CACHE.labels("hit").inc(len(texts) - sum(len(v) for v in missing.values()))
        if missing:
            EMBEDDING_CACHE.labels("miss").inc(len(missing))
            vectors = await self._inner.embed_documents(list(missing))
            for (text, positions), vector in zip(missing.items(), vectors, strict=True):
                self._store(text, vector)
                for i in positions:
                    results[i] = vector
        return [r for r in results if r is not None]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    def _store(self, text: str, vector: list[float]) -> None:
        self._entries[text] = array("f", vector)
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
