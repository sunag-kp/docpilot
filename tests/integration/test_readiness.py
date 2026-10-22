from __future__ import annotations

from docpilot.config import Settings
from docpilot.container import build_container


async def test_container_reaches_real_postgres_and_redis(settings: Settings) -> None:
    container = await build_container(settings)
    try:
        for check in container.readiness_checks.values():
            await check()
    finally:
        await container.aclose()
