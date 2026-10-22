from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from asgi_lifespan import LifespanManager

from docpilot.api.app import create_app
from docpilot.config import Settings
from docpilot.container import AsyncCallback, Container

ClientFactory = Callable[[dict[str, AsyncCallback]], Awaitable[httpx.AsyncClient]]


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("down")


@pytest.fixture
async def make_client(settings: Settings) -> AsyncIterator[ClientFactory]:
    managers: list[LifespanManager] = []

    async def make(checks: dict[str, AsyncCallback]) -> httpx.AsyncClient:
        async def build(s: Settings) -> Container:
            return Container(settings=s, readiness_checks=checks)

        manager = LifespanManager(create_app(settings, container_factory=build))
        await manager.__aenter__()
        managers.append(manager)
        transport = httpx.ASGITransport(app=manager.app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    yield make
    for manager in managers:
        await manager.__aexit__(None, None, None)


async def test_healthz_is_ok_even_when_dependencies_are_down(make_client: ClientFactory) -> None:
    client = await make_client({"postgres": _down})
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_ok_when_all_dependencies_answer(make_client: ClientFactory) -> None:
    client = await make_client({"postgres": _ok, "redis": _ok})
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_readyz_is_503_when_a_dependency_fails(make_client: ClientFactory) -> None:
    client = await make_client({"postgres": _ok, "redis": _down})
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "error: ConnectionError"


async def test_request_id_is_echoed_or_generated(make_client: ClientFactory) -> None:
    client = await make_client({})
    echoed = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
    generated = await client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert len(generated.headers["x-request-id"]) == 32
