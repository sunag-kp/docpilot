from __future__ import annotations

import asyncio
from uuid import uuid4

from redis.asyncio import Redis

from docpilot.config import Settings
from docpilot.infra.redis.ratelimit import RedisTokenBucketRateLimiter


async def test_token_bucket_allows_burst_then_limits_then_refills(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    limiter = RedisTokenBucketRateLimiter(redis, capacity=5, refill_per_sec=20)
    key = f"test:{uuid4()}"
    try:
        decisions = [await limiter.acquire(key) for _ in range(6)]
        assert [d.allowed for d in decisions] == [True] * 5 + [False]
        assert decisions[4].remaining == 0
        assert 0 < decisions[5].retry_after_seconds <= 0.05 + 1e-6
        await asyncio.sleep(0.12)  # 20 tokens/s -> at least 2 tokens back
        assert (await limiter.acquire(key, cost=2)).allowed
    finally:
        await redis.aclose()


async def test_concurrent_requests_never_overspend(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    limiter = RedisTokenBucketRateLimiter(redis, capacity=10, refill_per_sec=0.001)
    key = f"test:{uuid4()}"
    try:
        results = await asyncio.gather(*(limiter.acquire(key) for _ in range(50)))
        assert sum(d.allowed for d in results) == 10  # the Lua script is atomic
    finally:
        await redis.aclose()


async def test_fails_open_when_redis_is_unreachable() -> None:
    redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limiter = RedisTokenBucketRateLimiter(redis, capacity=3, refill_per_sec=1)
    try:
        decision = await limiter.acquire("anything")
        assert decision.allowed
    finally:
        await redis.aclose()
