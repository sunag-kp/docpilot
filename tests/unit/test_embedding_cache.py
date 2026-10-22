from __future__ import annotations

import math
from collections.abc import Sequence

from docpilot.infra.embeddings.cache import CachingEmbedder
from docpilot.infra.embeddings.hashing import HashingEmbedder


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__(16)
        self.batches: list[list[str]] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return await super().embed_documents(texts)


async def test_hits_skip_the_model_and_preserve_order_and_values() -> None:
    inner = CountingEmbedder()
    cache = CachingEmbedder(inner, max_entries=10)
    first = await cache.embed_documents(["a b", "c d", "a b"])
    assert inner.batches == [["a b", "c d"]]  # duplicates in one batch embedded once
    second = await cache.embed_documents(["c d", "e f", "a b"])
    assert inner.batches[-1] == ["e f"]
    assert second[0] == first[1]
    assert second[2] == first[0]
    assert all(
        math.isclose(a, b, rel_tol=1e-6)
        for a, b in zip(first[0], await inner.embed_query("a b"), strict=True)
    )
    assert cache.dim == 16


async def test_least_recently_used_entries_are_evicted() -> None:
    inner = CountingEmbedder()
    cache = CachingEmbedder(inner, max_entries=2)
    await cache.embed_documents(["one", "two"])
    await cache.embed_query("one")  # refresh "one"
    await cache.embed_query("three")  # evicts "two"
    assert len(cache) == 2
    await cache.embed_query("one")
    assert inner.batches[-1] == ["three"]
    await cache.embed_query("two")
    assert inner.batches[-1] == ["two"]
