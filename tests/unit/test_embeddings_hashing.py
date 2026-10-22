from __future__ import annotations

import math

from docpilot.infra.embeddings.hashing import HashingEmbedder


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_hashing_embedder_is_deterministic_normalised_and_lexical() -> None:
    embedder = HashingEmbedder(128)
    a1, a2, b, c = await embedder.embed_documents(
        [
            "vacation days accrue monthly",
            "vacation days accrue monthly",
            "monthly vacation accrual",
            "kitchen cleaning rota",
        ]
    )
    assert a1 == a2
    assert embedder.dim == len(a1) == 128
    assert math.isclose(math.sqrt(dot(a1, a1)), 1.0)
    assert dot(a1, b) > dot(a1, c)


async def test_empty_text_gets_a_unit_vector() -> None:
    vector = await HashingEmbedder(16).embed_query("the and of")  # only stopwords
    assert math.isclose(math.sqrt(dot(vector, vector)), 1.0)
