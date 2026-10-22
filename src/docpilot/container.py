"""Composition root: builds every adapter from settings exactly once.

This is the only module that knows which concrete implementation backs each port.
The API and the worker both build a ``Container`` at startup and tear it down on
shutdown; tests build one from in-memory fakes instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from redis.asyncio import Redis

from docpilot.config import (
    EmbeddingProvider,
    EventBusBackend,
    LLMProvider,
    RerankerKind,
    Settings,
    StorageBackend,
)
from docpilot.domain.chunking import Chunker, ChunkingConfig
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.db.engine import create_engine, ping
from docpilot.infra.db.repositories import SqlUnitOfWork
from docpilot.infra.db.search import PgChunkSearch
from docpilot.infra.embeddings.hashing import HashingEmbedder
from docpilot.infra.llm.echo import EchoLLM
from docpilot.infra.llm.extractive import ExtractiveLLM
from docpilot.infra.parsing import DefaultDocumentParser
from docpilot.infra.redis.cache import RedisAnswerCache
from docpilot.infra.redis.ratelimit import RedisTokenBucketRateLimiter
from docpilot.infra.security import JwtTokenCodec, ScryptPasswordHasher, SystemClock
from docpilot.infra.storage.local import InMemoryObjectStorage, LocalObjectStorage
from docpilot.observability.logging import get_logger
from docpilot.observability.tracing import instrument_clients
from docpilot.services.answering import QuestionAnswering
from docpilot.services.auth import AuthService
from docpilot.services.documents import DocumentService
from docpilot.services.ingestion import IngestionPipeline, RetryPolicy, StaleDocumentSweeper
from docpilot.services.ports import (
    LLM,
    Embedder,
    EventConsumer,
    EventPublisher,
    ObjectStorage,
    RateLimiter,
    Reranker,
    UnitOfWork,
)
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig

_log = get_logger(__name__)

AsyncCallback = Callable[[], Awaitable[None]]


@dataclass
class Container:
    settings: Settings
    auth: AuthService | None = None
    documents: DocumentService | None = None
    answering: QuestionAnswering | None = None
    rate_limiter: RateLimiter | None = None
    pipeline: IngestionPipeline | None = None
    sweeper: StaleDocumentSweeper | None = None
    consumer_factory: Callable[[], EventConsumer] | None = None
    readiness_checks: dict[str, AsyncCallback] = field(default_factory=dict)
    closers: list[AsyncCallback] = field(default_factory=list)

    async def aclose(self) -> None:
        # Close in reverse order of construction (consumers before their dependencies).
        for close in reversed(self.closers):
            try:
                await close()
            except Exception:
                _log.exception("container_close_failed")


def build_embedder(settings: Settings) -> Embedder:
    embedder = _build_raw_embedder(settings)
    if (
        settings.embedding_cache_size
        and settings.embedding_provider is not EmbeddingProvider.HASHING
    ):
        from docpilot.infra.embeddings.cache import CachingEmbedder  # noqa: PLC0415

        return CachingEmbedder(embedder, max_entries=settings.embedding_cache_size)
    return embedder


def _build_raw_embedder(settings: Settings) -> Embedder:
    match settings.embedding_provider:
        case EmbeddingProvider.HASHING:
            return HashingEmbedder(settings.embedding_dim)
        case EmbeddingProvider.OPENAI:
            from docpilot.infra.embeddings.openai import OpenAIEmbedder  # noqa: PLC0415

            assert settings.openai_api_key is not None
            return OpenAIEmbedder(
                settings.openai_api_key.get_secret_value(),
                settings.openai_embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.openai_base_url,
            )
        case EmbeddingProvider.FASTEMBED:
            from docpilot.infra.embeddings.local import FastEmbedEmbedder  # noqa: PLC0415
            from docpilot.infra.embeddings.model_download import ensure_model  # noqa: PLC0415

            path = (
                Path(settings.embedding_model_path)
                if settings.embedding_model_path
                else ensure_model(settings.embedding_model, settings.embedding_cache_dir)
            )
            return FastEmbedEmbedder(
                settings.embedding_model,
                path,
                dim=settings.embedding_dim,
                batch_size=settings.embedding_batch_size,
                threads=settings.embedding_threads,
            )


def build_llm(settings: Settings, embedder: Embedder | None = None) -> LLM:
    """Keyless by default; hosted providers only when explicitly configured with a key."""
    provider, model = settings.resolved_llm_provider, settings.resolved_llm_model
    match provider:
        case LLMProvider.EXTRACTIVE | LLMProvider.AUTO:
            return ExtractiveLLM(embedder, min_similarity=settings.extractive_min_similarity)
        case LLMProvider.ECHO:
            return EchoLLM()
        case LLMProvider.OLLAMA:
            from docpilot.infra.llm.ollama import OllamaLLM  # noqa: PLC0415

            return OllamaLLM(
                model,
                base_url=settings.ollama_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        case LLMProvider.OPENAI:
            from docpilot.infra.llm.hosted import OpenAILLM  # noqa: PLC0415

            assert settings.openai_api_key is not None
            return OpenAILLM(
                settings.openai_api_key.get_secret_value(),
                model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
            )
        case LLMProvider.ANTHROPIC:
            from docpilot.infra.llm.hosted import AnthropicLLM  # noqa: PLC0415

            assert settings.anthropic_api_key is not None
            if not settings.llm_model:
                raise ValueError("the Anthropic provider requires LLM_MODEL (a model id)")
            return AnthropicLLM(
                settings.anthropic_api_key.get_secret_value(),
                model,
                effort=settings.anthropic_effort,
                max_tokens=settings.anthropic_max_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
            )


def build_reranker(settings: Settings) -> Reranker | None:
    if settings.reranker is RerankerKind.CROSS_ENCODER:
        from docpilot.infra.rerank import CrossEncoderReranker  # noqa: PLC0415

        return CrossEncoderReranker(settings.reranker_model, cache_dir=settings.embedding_cache_dir)
    return None


async def build_container(settings: Settings) -> Container:
    container = Container(settings=settings)
    clock = SystemClock()

    engine = create_engine(settings)
    container.closers.append(engine.dispose)
    instrument_clients(settings)

    def uow_factory() -> UnitOfWork:
        return SqlUnitOfWork(engine, settings.embedding_dim)

    async def db_ping() -> None:
        await ping(engine)

    container.readiness_checks["postgres"] = db_ping

    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    container.closers.append(redis.aclose)

    async def redis_ping() -> None:
        await redis.ping()

    container.readiness_checks["redis"] = redis_ping

    storage = await _build_storage(settings, container)
    publisher, consumer_factory = await _build_bus(settings, container)
    embedder = build_embedder(settings)
    cache = RedisAnswerCache(redis, ttl_seconds=settings.cache_ttl_seconds)
    on_corpus_changed = cache.bump_corpus_version
    llm = build_llm(settings, embedder)
    if (close_llm := getattr(llm, "aclose", None)) is not None:
        container.closers.append(close_llm)

    container.auth = AuthService(
        uow_factory,
        ScryptPasswordHasher(),
        JwtTokenCodec(
            settings.jwt_secret.get_secret_value(),
            issuer=settings.jwt_issuer,
            ttl_minutes=settings.jwt_ttl_minutes,
            clock=clock,
        ),
        clock,
        signup_enabled=settings.signup_enabled,
    )
    container.documents = DocumentService(
        uow_factory,
        storage,
        publisher,
        clock,
        max_bytes=settings.max_upload_bytes,
        on_corpus_changed=on_corpus_changed,
    )
    container.answering = QuestionAnswering(
        HybridRetriever(
            PgChunkSearch(engine, settings.embedding_dim, keyword_ranker=settings.keyword_ranker),
            embedder,
            RetrievalConfig(
                candidates=settings.retrieval_candidates,
                rrf_k=settings.rrf_k,
                min_vector_similarity=settings.min_vector_similarity,
            ),
            reranker=build_reranker(settings),
        ),
        llm,
        cache if settings.cache_enabled else None,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
    )
    container.rate_limiter = RedisTokenBucketRateLimiter(
        redis,
        capacity=settings.rate_limit_capacity,
        refill_per_sec=settings.rate_limit_refill_per_sec,
    )
    container.pipeline = IngestionPipeline(
        uow_factory,
        storage,
        DefaultDocumentParser(),
        Chunker(
            ChunkingConfig(
                max_tokens=settings.chunk_max_tokens,
                min_tokens=settings.chunk_min_tokens,
                overlap_sentences=settings.chunk_overlap_sentences,
                topic_shift_threshold=settings.chunk_topic_shift_threshold,
            )
        ),
        embedder,
        publisher,
        clock,
        retry_policy=RetryPolicy(
            max_attempts=settings.ingest_max_attempts,
            base_delay_seconds=settings.ingest_backoff_base_seconds,
            max_delay_seconds=settings.ingest_backoff_max_seconds,
        ),
        lease_seconds=settings.ingest_lease_seconds,
        embed_batch_size=settings.embedding_batch_size,
        on_corpus_changed=on_corpus_changed,
    )
    container.sweeper = StaleDocumentSweeper(
        uow_factory, publisher, clock, stale_after_seconds=settings.ingest_lease_seconds * 2
    )
    container.consumer_factory = consumer_factory
    return container


async def _build_storage(settings: Settings, container: Container) -> ObjectStorage:
    match settings.storage_backend:
        case StorageBackend.MEMORY:
            return InMemoryObjectStorage()
        case StorageBackend.LOCAL:
            return LocalObjectStorage(settings.local_storage_path)
        case StorageBackend.S3:
            from docpilot.infra.storage.s3 import S3ObjectStorage  # noqa: PLC0415

            s3 = S3ObjectStorage(
                settings.s3_bucket,
                endpoint_url=settings.s3_endpoint_url,
                region=settings.s3_region,
                access_key_id=(
                    settings.s3_access_key_id.get_secret_value()
                    if settings.s3_access_key_id
                    else None
                ),
                secret_access_key=(
                    settings.s3_secret_access_key.get_secret_value()
                    if settings.s3_secret_access_key
                    else None
                ),
            )
            await s3.ensure_bucket()
            container.readiness_checks["object_storage"] = s3.ping
            return s3


async def _build_bus(
    settings: Settings, container: Container
) -> tuple[EventPublisher, Callable[[], EventConsumer]]:
    if settings.event_bus is EventBusBackend.MEMORY:
        bus = InMemoryEventBus()
        return bus, lambda: bus

    from docpilot.infra.bus.kafka import (  # noqa: PLC0415
        KafkaEventConsumer,
        KafkaEventPublisher,
    )

    publisher = KafkaEventPublisher(settings)
    await publisher.start()
    container.closers.append(publisher.stop)
    container.readiness_checks["kafka"] = publisher.ping

    def consumer_factory() -> EventConsumer:
        return KafkaEventConsumer(settings, publisher, concurrency=settings.ingest_concurrency)

    return publisher, consumer_factory
