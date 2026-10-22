"""Distributed token-bucket rate limiter, atomic via a Redis Lua script.

Why a token bucket: it allows short bursts up to ``capacity`` while enforcing a sustained
``refill_per_sec`` — friendlier to interactive clients than a fixed window, and without the
boundary spikes fixed windows allow. The whole read-modify-write runs inside Redis (one
round trip, no race between API replicas), and it uses Redis' clock so replicas with
skewed clocks agree.
"""

from __future__ import annotations

import math

from redis.asyncio import Redis
from redis.exceptions import RedisError

from docpilot.observability.logging import get_logger
from docpilot.observability.metrics import RATE_LIMITER_ERRORS
from docpilot.services.ports import RateLimitDecision

_log = get_logger(__name__)

_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) / 1000 * refill)
local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (cost - tokens) / refill
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil(capacity / refill * 1000) + 1000)
return {allowed, tostring(tokens), tostring(retry_after)}
"""


class RedisTokenBucketRateLimiter:
    def __init__(
        self, redis: Redis, *, capacity: int, refill_per_sec: float, prefix: str = "rl"
    ) -> None:
        self._redis = redis
        self._capacity = capacity
        self._refill = refill_per_sec
        self._prefix = prefix
        self._script = redis.register_script(_BUCKET_SCRIPT)

    async def acquire(self, key: str, cost: int = 1) -> RateLimitDecision:
        try:
            allowed, tokens, retry_after = await self._script(
                keys=[f"{self._prefix}:{key}"], args=[self._capacity, self._refill, cost]
            )
        except RedisError:
            # Fail open: a Redis blip should degrade protection, not take the API down.
            RATE_LIMITER_ERRORS.inc()
            _log.warning("rate_limiter_unavailable_failing_open", exc_info=True)
            return RateLimitDecision(
                allowed=True, limit=self._capacity, remaining=self._capacity, retry_after_seconds=0
            )
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            limit=self._capacity,
            remaining=max(0, math.floor(float(tokens))),
            retry_after_seconds=float(retry_after),
        )
