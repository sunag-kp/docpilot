from __future__ import annotations

from uuid import uuid4

from redis.asyncio import Redis

from docpilot.config import Settings
from docpilot.domain.answers import Citation
from docpilot.infra.redis.cache import RedisAnswerCache
from docpilot.services.ports import CachedAnswer

ANSWER = CachedAnswer(
    answer="Twenty days [1].",
    citations=[
        Citation(1, uuid4(), uuid4(), "h.md", "Employees get 20 days.", "Leave", 2, 2, 0.03, 0.71)
    ],
    refused=False,
    model="extractive",
)


async def test_round_trip_first_hit_wins_and_versions_bump(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    cache = RedisAnswerCache(redis, ttl_seconds=60)
    prefix = f"test:{uuid4()}"
    try:
        assert await cache.get([f"{prefix}:a", f"{prefix}:b"]) is None
        await cache.set([f"{prefix}:a", f"{prefix}:b"], ANSWER)
        assert await cache.get([f"{prefix}:missing", f"{prefix}:b"]) == ANSWER
        assert 0 < await redis.ttl(f"{prefix}:a") <= 60

        tenant = uuid4()
        assert await cache.corpus_version(tenant) == 0
        await cache.bump_corpus_version(tenant)
        await cache.bump_corpus_version(tenant)
        assert await cache.corpus_version(tenant) == 2
    finally:
        await redis.aclose()


async def test_cache_degrades_to_a_miss_when_redis_is_down() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    cache = RedisAnswerCache(redis, ttl_seconds=60)
    try:
        await cache.set(["k"], ANSWER)  # no exception
        assert await cache.get(["k"]) is None
        assert await cache.corpus_version(uuid4()) == -1
        await cache.bump_corpus_version(uuid4())
    finally:
        await redis.aclose()
