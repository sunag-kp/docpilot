"""Redis answer cache with per-tenant corpus versioning."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from docpilot.domain.answers import Citation
from docpilot.observability.logging import get_logger
from docpilot.services.ports import CachedAnswer

_log = get_logger(__name__)


class RedisAnswerCache:
    """A cache is an optimisation: on any Redis error it behaves as a miss, never fails."""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, keys: Sequence[str]) -> CachedAnswer | None:
        try:
            values = await self._redis.mget(list(keys))
        except RedisError:
            _log.warning("cache_get_failed", exc_info=True)
            return None
        for value in values:
            if value:
                return _decode(value)
        return None

    async def set(self, keys: Sequence[str], answer: CachedAnswer) -> None:
        payload = _encode(answer)
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for key in keys:
                    pipe.set(key, payload, ex=self._ttl)
                await pipe.execute()
        except RedisError:
            _log.warning("cache_set_failed", exc_info=True)

    async def corpus_version(self, tenant_id: UUID) -> int:
        try:
            value = await self._redis.get(_version_key(tenant_id))
        except RedisError:
            return -1  # unknown version: keys won't match anything cached
        return int(value) if value else 0

    async def bump_corpus_version(self, tenant_id: UUID) -> None:
        try:
            await self._redis.incr(_version_key(tenant_id))
        except RedisError:
            _log.warning("corpus_version_bump_failed", tenant_id=str(tenant_id), exc_info=True)


def _version_key(tenant_id: UUID) -> str:
    return f"corpus-version:{tenant_id}"


def _encode(answer: CachedAnswer) -> bytes:
    return json.dumps(
        {
            "answer": answer.answer,
            "refused": answer.refused,
            "model": answer.model,
            "citations": [
                {
                    "number": c.number,
                    "chunk_id": str(c.chunk_id),
                    "document_id": str(c.document_id),
                    "filename": c.filename,
                    "text": c.text,
                    "section": c.section,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "score": c.score,
                    "vector_similarity": c.vector_similarity,
                }
                for c in answer.citations
            ],
        }
    ).encode()


def _decode(raw: bytes | str) -> CachedAnswer:
    data: dict[str, Any] = json.loads(raw)
    return CachedAnswer(
        answer=data["answer"],
        refused=data["refused"],
        model=data["model"],
        citations=[
            Citation(
                number=c["number"],
                chunk_id=UUID(c["chunk_id"]),
                document_id=UUID(c["document_id"]),
                filename=c["filename"],
                text=c["text"],
                section=c["section"],
                page_start=c["page_start"],
                page_end=c["page_end"],
                score=c["score"],
                vector_similarity=c["vector_similarity"],
            )
            for c in data["citations"]
        ],
    )
