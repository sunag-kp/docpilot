"""Composition root: builds every adapter from settings exactly once.

This is the only module that knows which concrete implementation backs each port.
The API and the worker both build a ``Container`` at startup and tear it down on
shutdown; tests build one from in-memory fakes instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from redis.asyncio import Redis

from docpilot.config import Settings
from docpilot.infra.db.engine import create_engine, ping

AsyncCallback = Callable[[], Awaitable[None]]


@dataclass
class Container:
    settings: Settings
    readiness_checks: dict[str, AsyncCallback] = field(default_factory=dict)
    closers: list[AsyncCallback] = field(default_factory=list)

    async def aclose(self) -> None:
        # Close in reverse order of construction (consumers before their dependencies).
        for close in reversed(self.closers):
            await close()


async def build_container(settings: Settings) -> Container:
    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)

    async def redis_ping() -> None:
        await redis.ping()

    async def db_ping() -> None:
        await ping(engine)

    return Container(
        settings=settings,
        readiness_checks={"postgres": db_ping, "redis": redis_ping},
        closers=[engine.dispose, redis.aclose],
    )
