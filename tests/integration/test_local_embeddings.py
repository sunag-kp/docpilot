"""The default zero-cost embedder: a real ONNX model running locally on CPU."""

from __future__ import annotations

import math

import pytest

from docpilot.config import Settings
from docpilot.infra.embeddings.local import FastEmbedEmbedder
from docpilot.infra.embeddings.model_download import ensure_model


@pytest.fixture(scope="module")
def embedder() -> FastEmbedEmbedder:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    path = ensure_model(settings.embedding_model, settings.embedding_cache_dir)
    return FastEmbedEmbedder(settings.embedding_model, path, dim=384, batch_size=8)


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_vectors_are_unit_length_with_configured_dimension(
    embedder: FastEmbedEmbedder,
) -> None:
    [vector] = await embedder.embed_documents(["hello world"])
    assert len(vector) == embedder.dim == 384
    assert math.isclose(math.sqrt(dot(vector, vector)), 1.0, rel_tol=1e-3)


async def test_paraphrases_score_higher_than_unrelated_text(embedder: FastEmbedEmbedder) -> None:
    query = await embedder.embed_query("How much vacation do staff earn?")
    pto, rate_limit = await embedder.embed_documents(
        [
            "Employees accrue 1.5 days of paid time off per month.",
            "The API rejects clients that exceed 100 requests per minute.",
        ]
    )
    # No word overlap with the query, yet the PTO sentence wins: this is semantic search.
    assert dot(query, pto) > dot(query, rate_limit) + 0.2


def test_dimension_mismatch_fails_fast() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    path = ensure_model(settings.embedding_model, settings.embedding_cache_dir)
    with pytest.raises(ValueError, match="EMBEDDING_DIM"):
        FastEmbedEmbedder(settings.embedding_model, path, dim=768)
