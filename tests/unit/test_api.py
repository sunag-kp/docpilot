"""HTTP contract tests: real FastAPI app + real services, in-memory adapters."""

from __future__ import annotations

import httpx

from docpilot.infra.bus.memory import InMemoryEventBus
from tests.fakes import FakeRateLimiter
from tests.unit.api_helpers import MD, signup


async def test_signup_validation_returns_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/signup", json={"tenant_name": "A", "email": "nope", "password": "short"}
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "urn:docpilot:error:validation_error"
    assert {tuple(e["loc"])[-1] for e in body["errors"]} == {"tenant_name", "email", "password"}


async def test_login_and_duplicate_signup(client: httpx.AsyncClient) -> None:
    await signup(client)
    duplicate = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": "Other", "email": "dev@acme.test", "password": "s3cret-pass"},
    )
    assert duplicate.status_code == 409
    ok = await client.post(
        "/v1/auth/login", json={"email": "dev@acme.test", "password": "s3cret-pass"}
    )
    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"
    bad = await client.post("/v1/auth/login", json={"email": "dev@acme.test", "password": "nope"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"] == "Bearer"


async def test_protected_routes_require_credentials(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"Authorization": "Bearer garbage"}, {"X-API-Key": "dp_nope"}):
        response = await client.get("/v1/documents", headers=headers)
        assert response.status_code == 401, headers


async def test_api_key_flow_via_header_and_bearer(client: httpx.AsyncClient) -> None:
    jwt_headers = await signup(client)
    created = await client.post("/v1/api-keys", json={"name": "ci"}, headers=jwt_headers)
    assert created.status_code == 201
    raw = created.json()["key"]

    for headers in ({"X-API-Key": raw}, {"Authorization": f"Bearer {raw}"}):
        assert (await client.get("/v1/documents", headers=headers)).status_code == 200

    listed = (await client.get("/v1/api-keys", headers=jwt_headers)).json()
    assert [k["name"] for k in listed] == ["ci"]
    assert "key" not in listed[0]
    revoked = await client.delete(f"/v1/api-keys/{created.json()['id']}", headers=jwt_headers)
    assert revoked.status_code == 204
    assert (await client.get("/v1/documents", headers={"X-API-Key": raw})).status_code == 401


async def test_upload_status_chunks_and_delete(
    client: httpx.AsyncClient, bus: InMemoryEventBus
) -> None:
    headers = await signup(client)
    response = await client.post(
        "/v1/documents", files={"file": ("handbook.md", MD, "text/markdown")}, headers=headers
    )
    assert response.status_code == 202, response.text
    doc = response.json()
    assert doc["status"] == "queued"
    assert doc["duplicate"] is False
    assert response.headers["location"] == f"/v1/documents/{doc['id']}"
    assert int(response.headers["x-ratelimit-remaining"]) < 1000

    await bus.drain()  # the in-process worker has consumed the upload event
    status = (await client.get(f"/v1/documents/{doc['id']}", headers=headers)).json()
    assert status["status"] == "ready"
    assert status["chunk_count"] == 1

    chunks = (await client.get(f"/v1/documents/{doc['id']}/chunks", headers=headers)).json()
    assert chunks[0]["section"] == "Handbook > Vacation"

    again = await client.post(
        "/v1/documents", files={"file": ("copy.md", MD, "text/markdown")}, headers=headers
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True

    listing = (await client.get("/v1/documents?status=ready", headers=headers)).json()
    assert listing["total"] == 1
    assert (await client.delete(f"/v1/documents/{doc['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/v1/documents/{doc['id']}", headers=headers)).status_code == 404


async def test_tenants_cannot_see_each_other(client: httpx.AsyncClient) -> None:
    alice, bob = await signup(client, "a@a.io"), await signup(client, "b@b.io")
    doc = (await client.post("/v1/documents", files={"file": ("a.md", MD)}, headers=alice)).json()
    assert (await client.get(f"/v1/documents/{doc['id']}", headers=bob)).status_code == 404
    assert (await client.get("/v1/documents", headers=bob)).json()["total"] == 0


async def test_upload_rejections(client: httpx.AsyncClient) -> None:
    headers = await signup(client)
    unsupported = await client.post(
        "/v1/documents", files={"file": ("x.exe", b"MZ")}, headers=headers
    )
    assert unsupported.status_code == 415
    too_big = await client.post(
        "/v1/documents", files={"file": ("big.txt", b"a" * (1024 * 1024 + 10))}, headers=headers
    )
    assert too_big.status_code == 413
    huge = await client.post(
        "/v1/documents", files={"file": ("huge.txt", b"a" * (3 * 1024 * 1024))}, headers=headers
    )
    assert huge.status_code == 413  # stopped by the body-size middleware before parsing


async def test_retry_endpoint_404_for_non_failed(client: httpx.AsyncClient) -> None:
    headers = await signup(client)
    doc = (await client.post("/v1/documents", files={"file": ("a.md", MD)}, headers=headers)).json()
    assert (
        await client.post(f"/v1/documents/{doc['id']}/retry", headers=headers)
    ).status_code == 404


async def test_rate_limit_returns_429_with_retry_after(
    client: httpx.AsyncClient, limiter: FakeRateLimiter
) -> None:
    headers = await signup(client)
    limiter.capacity = 5  # one upload costs 5 tokens
    first = await client.post("/v1/documents", files={"file": ("a.md", MD)}, headers=headers)
    assert first.status_code == 202
    second = await client.post(
        "/v1/documents", files={"file": ("b.md", MD + b"x")}, headers=headers
    )
    assert second.status_code == 429
    assert second.headers["retry-after"] == "3"
    assert second.json()["type"] == "urn:docpilot:error:rate_limited"


async def test_openapi_schema_is_served(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert "/v1/documents" in schema["paths"]
    assert {"HTTPBearer", "APIKeyHeader"} <= set(schema["components"]["securitySchemes"])
