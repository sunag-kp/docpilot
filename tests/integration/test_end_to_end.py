"""Full stack through HTTP: API -> MinIO -> Kafka -> worker -> Postgres, real adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager

from docpilot.api.app import create_app
from docpilot.config import Settings
from tests.integration.conftest import integration_settings
from tests.samples import make_docx, make_pdf


@pytest.fixture
async def client(migrated: None) -> AsyncIterator[httpx.AsyncClient]:
    settings: Settings = integration_settings(
        run_worker_in_process=True,
        kafka_topic_ingest=f"test.e2e.{uuid4().hex[:8]}",
        rate_limit_capacity=500,
    )
    app = create_app(settings)
    async with LifespanManager(app, startup_timeout=60, shutdown_timeout=60) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as c:
            yield c


async def wait_for_status(
    client: httpx.AsyncClient, doc_id: str, headers: dict[str, str], wanted: set[str]
) -> dict[str, object]:
    for _ in range(150):
        body: dict[str, object] = (
            await client.get(f"/v1/documents/{doc_id}", headers=headers)
        ).json()
        if body["status"] in wanted:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError(f"document {doc_id} stuck in {body['status']}")


async def test_readiness_reports_every_dependency(client: httpx.AsyncClient) -> None:
    body = (await client.get("/readyz")).json()
    assert body["checks"] == {
        "postgres": "ok",
        "redis": "ok",
        "object_storage": "ok",
        "kafka": "ok",
    }


async def test_upload_pdf_docx_markdown_through_kafka_to_ready(client: httpx.AsyncClient) -> None:
    signup = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": "E2E", "email": f"{uuid4().hex}@e2e.io", "password": "s3cret-pass"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    marker = uuid4().hex  # unique content per run so dedup never short-circuits the test
    files = {
        "policy.pdf": make_pdf(
            [
                ["SECURITY POLICY", f"Passwords rotate every 90 days. Ref {marker}."],
                ["Second page text."],
            ]
        ),
        "guide.docx": make_docx([("Guide", 1), (f"Install the agent first. Ref {marker}.", 0)]),
        "notes.md": f"# Notes\n\nKafka partitions preserve order. Ref {marker}.\n".encode(),
        "broken.pdf": b"%PDF-1.7 truncated " + marker.encode(),
    }
    ids: dict[str, str] = {}
    for name, data in files.items():
        response = await client.post("/v1/documents", files={"file": (name, data)}, headers=headers)
        assert response.status_code == 202, response.text
        ids[name] = response.json()["id"]

    results = {
        name: await wait_for_status(client, doc_id, headers, {"ready", "failed"})
        for name, doc_id in ids.items()
    }
    assert results["policy.pdf"]["status"] == "ready"
    assert results["policy.pdf"]["page_count"] == 2
    assert results["guide.docx"]["status"] == "ready"
    assert results["notes.md"]["status"] == "ready"
    assert results["broken.pdf"]["status"] == "failed"
    assert "PermanentIngestionError" in str(results["broken.pdf"]["error"])

    chunks = (await client.get(f"/v1/documents/{ids['policy.pdf']}/chunks", headers=headers)).json()
    assert chunks[0]["section"] == "SECURITY POLICY"
    assert chunks[0]["page_start"] == 1


async def test_ask_questions_stream_answers_and_cache_invalidation(
    client: httpx.AsyncClient,
) -> None:
    signup = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": "E2E", "email": f"{uuid4().hex}@e2e.io", "password": "s3cret-pass"},
    )
    headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    marker = uuid4().hex
    leave = f"# Leave\n\nEmployees accrue 1.5 vacation days per month. Ref {marker}.\n"
    first = await client.post(
        "/v1/documents",
        files={"file": ("leave.md", leave.encode())},
        headers=headers,
    )
    await wait_for_status(client, first.json()["id"], headers, {"ready"})

    question = {"question": "How many vacation days do employees accrue per month?"}
    answer = (await client.post("/v1/query", json=question, headers=headers)).json()
    assert "1.5 vacation days per month" in answer["answer"]
    assert answer["cited_numbers"] == [1]
    assert answer["cached"] is False

    stream = await client.post("/v1/query/stream", json=question, headers=headers)
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: done" in stream.text
    assert '"cached":true' in stream.text  # identical question served from Redis

    second = await client.post(
        "/v1/documents",
        files={
            "file": ("remote.md", f"# Remote\n\nRemote work is allowed. Ref {marker}.\n".encode())
        },
        headers=headers,
    )
    await wait_for_status(client, second.json()["id"], headers, {"ready"})
    after_upload = (await client.post("/v1/query", json=question, headers=headers)).json()
    assert after_upload["cached"] is False  # new document bumped the corpus version

    refusal = (
        await client.post(
            "/v1/query", json={"question": "Who painted the Mona Lisa?"}, headers=headers
        )
    ).json()
    assert refusal["refused"] is True
