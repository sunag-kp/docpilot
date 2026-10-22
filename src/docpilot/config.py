"""Application configuration, loaded from environment variables (12-factor).

Every knob lives here so the rest of the code never reads ``os.environ`` directly.
Secrets are typed as ``SecretStr`` so they are never printed in logs or reprs.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class EventBusBackend(StrEnum):
    KAFKA = "kafka"
    MEMORY = "memory"


class StorageBackend(StrEnum):
    S3 = "s3"
    LOCAL = "local"
    MEMORY = "memory"


class EmbeddingProvider(StrEnum):
    FASTEMBED = "fastembed"  # local ONNX model on CPU (default, no key)
    HASHING = "hashing"  # dependency-free lexical fallback for tests / air-gapped runs
    OPENAI = "openai"  # optional, only when OPENAI_API_KEY is present


class LLMProvider(StrEnum):
    AUTO = "auto"  # anthropic/openai if a key is present, otherwise extractive
    EXTRACTIVE = "extractive"  # built-in local answerer, no key, no model download
    ECHO = "echo"  # deterministic mock for tests
    OLLAMA = "ollama"  # local open-weights model served by Ollama
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class RerankerKind(StrEnum):
    NONE = "none"
    CROSS_ENCODER = "cross_encoder"  # local fastembed cross-encoder


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # `OPENAI_API_KEY=` in .env means "not set", not ""
    )

    # --- runtime -------------------------------------------------------------------------
    app_env: Environment = Environment.LOCAL
    service_name: str = "docpilot"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ui_dist_path: str = "ui/dist"
    run_worker_in_process: bool = False

    # --- datastores ----------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://docpilot:docpilot@localhost:5432/docpilot"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    redis_url: str = "redis://localhost:6379/0"

    # --- event bus -----------------------------------------------------------------------
    event_bus: EventBusBackend = EventBusBackend.KAFKA
    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str | None = None
    kafka_sasl_username: str | None = None
    kafka_sasl_password: SecretStr | None = None
    kafka_topic_ingest: str = "docpilot.documents.uploaded.v1"
    kafka_topic_dlq: str = "docpilot.documents.uploaded.v1.dlq"
    kafka_topic_partitions: int = Field(default=6, ge=1)
    kafka_consumer_group: str = "docpilot-ingest-workers"

    # --- object storage ------------------------------------------------------------------
    storage_backend: StorageBackend = StorageBackend.S3
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_bucket: str = "docpilot-documents"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr | None = SecretStr("minioadmin")
    s3_secret_access_key: SecretStr | None = SecretStr("minioadmin")
    local_storage_path: str = ".data/blobs"
    max_upload_mb: int = Field(default=20, ge=1, le=200)

    # --- auth & rate limiting ------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-insecure-jwt-secret-change-me-0123456789")
    jwt_ttl_minutes: int = Field(default=60, ge=1)
    jwt_issuer: str = "docpilot"
    rate_limit_capacity: int = Field(default=60, ge=1)
    rate_limit_refill_per_sec: float = Field(default=1.0, gt=0)
    signup_enabled: bool = True

    # --- ingestion -----------------------------------------------------------------------
    chunk_max_tokens: int = Field(default=320, ge=32)
    chunk_min_tokens: int = Field(default=80, ge=8)
    chunk_overlap_sentences: int = Field(default=1, ge=0)
    chunk_topic_shift_threshold: float = Field(default=0.08, ge=0.0, le=1.0)
    ingest_max_attempts: int = Field(default=4, ge=1)
    ingest_backoff_base_seconds: float = Field(default=0.5, gt=0)
    ingest_backoff_max_seconds: float = Field(default=20.0, gt=0)
    ingest_lease_seconds: int = Field(default=300, ge=10)
    ingest_concurrency: int = Field(default=4, ge=1)
    worker_metrics_port: int = Field(default=9100, ge=0)

    # --- embeddings ----------------------------------------------------------------------
    embedding_provider: EmbeddingProvider = EmbeddingProvider.FASTEMBED
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_model_path: str | None = None  # pre-downloaded ONNX dir (baked into the image)
    embedding_cache_dir: str = ".models"
    embedding_dim: int = Field(default=384, ge=8, le=4096)
    embedding_batch_size: int = Field(default=32, ge=1)
    embedding_threads: int | None = Field(default=None, ge=1)
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"

    # --- retrieval -----------------------------------------------------------------------
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_candidates: int = Field(default=30, ge=1, le=200)
    rrf_k: int = Field(default=60, ge=1)
    keyword_ranker: Literal["bm25", "ts_rank"] = "bm25"
    min_vector_similarity: float = Field(default=0.15, ge=-1.0, le=1.0)
    reranker: RerankerKind = RerankerKind.NONE
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"

    # --- generation ----------------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.AUTO
    llm_model: str | None = None  # provider default when unset
    llm_max_tokens: int = Field(default=700, ge=16)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    # Extractive answerer: minimum question/sentence cosine similarity to answer at all.
    extractive_min_similarity: float = Field(default=0.4, ge=-1.0, le=1.0)
    anthropic_api_key: SecretStr | None = None
    # Opus 5.5 always thinks and thinking counts toward max_tokens, hence a larger budget.
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    anthropic_max_tokens: int = Field(default=8000, ge=256)
    ollama_base_url: str = "http://localhost:11434"

    # --- caching -------------------------------------------------------------------------
    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=3600, ge=1)

    # --- observability -------------------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    metrics_enabled: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _force_asyncpg_driver(cls, value: str) -> str:
        # Hosted providers (Neon, Render) hand out plain postgres:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        if self.chunk_min_tokens >= self.chunk_max_tokens:
            raise ValueError("CHUNK_MIN_TOKENS must be smaller than CHUNK_MAX_TOKENS")
        if self.app_env is Environment.PRODUCTION:
            secret = self.jwt_secret.get_secret_value()
            if secret.startswith("dev-only") or len(secret) < 32:
                raise ValueError(
                    "JWT_SECRET must be set to a random value of 32+ chars in production"
                )
        if self.embedding_provider is EmbeddingProvider.OPENAI and self.openai_api_key is None:
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        if self.llm_provider is LLMProvider.OPENAI and self.openai_api_key is None:
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
        if self.llm_provider is LLMProvider.ANTHROPIC and self.anthropic_api_key is None:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return _DEFAULT_MODELS.get(self.resolved_llm_provider, self.resolved_llm_provider.value)

    @property
    def resolved_llm_provider(self) -> LLMProvider:
        """``auto`` picks a hosted provider only when its key exists; never requires one."""
        if self.llm_provider is not LLMProvider.AUTO:
            return self.llm_provider
        if self.anthropic_api_key is not None:
            return LLMProvider.ANTHROPIC
        if self.openai_api_key is not None:
            return LLMProvider.OPENAI
        return LLMProvider.EXTRACTIVE


_DEFAULT_MODELS = {
    LLMProvider.OLLAMA: "qwen2.5:1.5b",
    LLMProvider.OPENAI: "gpt-4o-mini",
    LLMProvider.ANTHROPIC: "anthropic-model",
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
