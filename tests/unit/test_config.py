from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from docpilot.config import EmbeddingProvider, Environment, LLMProvider, Settings


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults must be asserted against a clean environment, not the CI job's env vars."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_are_safe_for_local_dev() -> None:
    settings = make()
    assert settings.app_env is Environment.LOCAL
    assert settings.embedding_provider is EmbeddingProvider.FASTEMBED
    assert settings.llm_provider is LLMProvider.AUTO
    assert settings.resolved_llm_provider is LLMProvider.EXTRACTIVE  # no key needed
    assert settings.max_upload_bytes == 20 * 1024 * 1024


@pytest.mark.parametrize(
    "raw",
    ["postgres://u:p@host/db", "postgresql://u:p@host/db", "postgresql+asyncpg://u:p@host/db"],
)
def test_database_url_is_normalised_to_asyncpg(raw: str) -> None:
    assert make(database_url=raw).database_url == "postgresql+asyncpg://u:p@host/db"


def test_cors_origins_accepts_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    assert make().cors_origins == ["http://a.test", "http://b.test"]


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make(app_env=Environment.PRODUCTION)


def test_production_accepts_strong_jwt_secret() -> None:
    settings = make(app_env=Environment.PRODUCTION, jwt_secret=SecretStr("x" * 40))
    assert settings.app_env is Environment.PRODUCTION


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"embedding_provider": EmbeddingProvider.OPENAI}, "OPENAI_API_KEY"),
        ({"llm_provider": LLMProvider.OPENAI}, "OPENAI_API_KEY"),
        ({"llm_provider": LLMProvider.ANTHROPIC}, "ANTHROPIC_API_KEY"),
        ({"chunk_min_tokens": 400, "chunk_max_tokens": 300}, "CHUNK_MIN_TOKENS"),
    ],
)
def test_inconsistent_settings_fail_fast(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        make(**overrides)


def test_secrets_are_not_leaked_in_repr() -> None:
    settings = make(openai_api_key=SecretStr("sk-very-secret"))
    assert "sk-very-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, LLMProvider.EXTRACTIVE),
        ({"openai_api_key": SecretStr("sk-x")}, LLMProvider.OPENAI),
        ({"anthropic_api_key": SecretStr("sk-ant-x"), "llm_model": "m"}, LLMProvider.ANTHROPIC),
        (
            {
                "openai_api_key": SecretStr("a"),
                "anthropic_api_key": SecretStr("b"),
                "llm_model": "m",
            },
            LLMProvider.ANTHROPIC,
        ),
        ({"llm_provider": LLMProvider.OLLAMA}, LLMProvider.OLLAMA),
    ],
)
def test_auto_llm_provider_only_uses_hosted_models_when_a_key_exists(
    overrides: dict[str, object], expected: LLMProvider
) -> None:
    assert make(**overrides).resolved_llm_provider is expected


def test_env_example_runs_without_any_keys() -> None:
    settings = Settings(_env_file=".env.example")  # type: ignore[call-arg]
    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.resolved_llm_provider is LLMProvider.EXTRACTIVE
    assert settings.embedding_provider is EmbeddingProvider.FASTEMBED
