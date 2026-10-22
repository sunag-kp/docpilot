"""Deterministic feature-hashing embedder.

Not semantic — it captures lexical overlap only — but it needs no model download, runs in
microseconds and is fully reproducible, which makes it the right choice for unit tests and
air-gapped environments. The default production path is the local ONNX model (fastembed).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from itertools import pairwise

from docpilot.domain.text import content_terms


class HashingEmbedder:
    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        terms = content_terms(text)
        features = terms + [f"{a}_{b}" for a, b in pairwise(terms)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0  # signed hashing cancels collision bias
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0  # a defined, unit-length vector for empty input
            return vector
        return [v / norm for v in vector]
