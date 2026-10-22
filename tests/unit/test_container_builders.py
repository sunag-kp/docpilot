"""Provider selection in the composition root: keyless by default, hosted only with keys."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from docpilot.config import EmbeddingProvider, LLMProvider, RerankerKind, Settings
from docpilot.container import build_embedder, build_llm, build_reranker
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.embeddings.openai import OpenAIEmbedder


def make(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "name"),
    [
        ({}, "extractive"),
        ({"llm_provider": LLMProvider.ECHO}, "echo"),
        ({"llm_provider": LLMProvider.OLLAMA}, "ollama:qwen2.5:1.5b"),
        ({"llm_provider": LLMProvider.OLLAMA, "llm_model": "llama3.2:3b"}, "ollama:llama3.2:3b"),
        ({"openai_api_key": SecretStr("sk-x")}, "openai:gpt-4o-mini"),
        ({"anthropic_api_key": SecretStr("sk-ant-x")}, "anthropic:anthropic-model"),
    ],
)
def test_llm_selection(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object], name: str
) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert build_llm(make(**overrides)).name == name


def test_embedder_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    assert isinstance(
        build_embedder(make(embedding_provider=EmbeddingProvider.HASHING)), HashingEmbedder
    )
    hosted = build_embedder(
        make(embedding_provider=EmbeddingProvider.OPENAI, openai_api_key=SecretStr("k"))
    )
    assert isinstance(hosted, OpenAIEmbedder)


def test_reranker_is_off_by_default() -> None:
    assert build_reranker(make()) is None
    assert make(reranker=RerankerKind.CROSS_ENCODER).reranker is RerankerKind.CROSS_ENCODER
