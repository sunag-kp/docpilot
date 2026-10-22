from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from docpilot.domain.errors import ConflictError
from docpilot.domain.models import (
    ApiKey,
    ChunkDraft,
    Document,
    DocumentKind,
    DocumentStatus,
    EmbeddedChunk,
    Tenant,
    User,
)
from docpilot.services.ports import UnitOfWork


async def make_tenant(uow_factory: Callable[[], UnitOfWork]) -> Tenant:
    tenant = Tenant(id=uuid4(), name="t", created_at=datetime.now(UTC))
    async with uow_factory() as uow:
        await uow.tenants.add(tenant)
        await uow.commit()
    return tenant


def make_document(tenant: Tenant, sha: str | None = None) -> Document:
    now = datetime.now(UTC)
    return Document(
        id=uuid4(),
        tenant_id=tenant.id,
        filename="a.md",
        kind=DocumentKind.MARKDOWN,
        size_bytes=10,
        sha256=sha or uuid4().hex,
        storage_key=f"k/{uuid4()}",
        status=DocumentStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )


async def test_uncommitted_work_is_rolled_back(uow_factory: Callable[[], UnitOfWork]) -> None:
    tenant = Tenant(id=uuid4(), name="ghost", created_at=datetime.now(UTC))
    async with uow_factory() as uow:
        await uow.tenants.add(tenant)
    async with uow_factory() as uow:
        assert await uow.tenants.get(tenant.id) is None


async def test_user_email_is_unique_case_insensitively(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    tenant = await make_tenant(uow_factory)
    email = f"{uuid4().hex}@x.io"
    async with uow_factory() as uow:
        await uow.users.add(User(uuid4(), tenant.id, email, "h", datetime.now(UTC)))
        await uow.commit()
    async with uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.users.add(User(uuid4(), tenant.id, email.upper(), "h", datetime.now(UTC)))
    async with uow_factory() as uow:
        found = await uow.users.get_by_email(email.upper())
    assert found is not None
    assert found.tenant_id == tenant.id


async def test_api_keys_lookup_list_and_revoke(uow_factory: Callable[[], UnitOfWork]) -> None:
    tenant, other = await make_tenant(uow_factory), await make_tenant(uow_factory)
    key = ApiKey(uuid4(), tenant.id, "ci", "dp_abc", datetime.now(UTC))
    digest = uuid4().hex
    async with uow_factory() as uow:
        await uow.api_keys.add(key, digest)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.api_keys.get_active_by_hash(digest) == key
        assert await uow.api_keys.list_for_tenant(tenant.id) == [key]
        assert not await uow.api_keys.revoke(other.id, key.id, datetime.now(UTC))
        assert await uow.api_keys.revoke(tenant.id, key.id, datetime.now(UTC))
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.api_keys.get_active_by_hash(digest) is None


async def test_document_dedup_list_and_tenant_scoping(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    tenant, other = await make_tenant(uow_factory), await make_tenant(uow_factory)
    first = make_document(tenant, sha="same")
    async with uow_factory() as uow:
        assert await uow.documents.add(first) == first
        duplicate = await uow.documents.add(replace(make_document(tenant, sha="same")))
        assert duplicate.id == first.id  # ON CONFLICT returns the existing row
        await uow.documents.add(make_document(tenant))
        await uow.commit()
    async with uow_factory() as uow:
        items, total = await uow.documents.list_for_tenant(tenant.id, limit=1, offset=0)
        assert (len(items), total) == (1, 2)
        assert await uow.documents.list_for_tenant(other.id, limit=10, offset=0) == ([], 0)
        assert await uow.documents.get(other.id, first.id) is None
        assert await uow.documents.delete(other.id, first.id) is None
        queued, n = await uow.documents.list_for_tenant(
            tenant.id, limit=10, offset=0, status=DocumentStatus.READY
        )
        assert (queued, n) == ([], 0)


async def test_claim_is_exclusive_under_concurrency(uow_factory: Callable[[], UnitOfWork]) -> None:
    tenant = await make_tenant(uow_factory)
    doc = make_document(tenant)
    async with uow_factory() as uow:
        await uow.documents.add(doc)
        await uow.commit()

    async def claim() -> Document | None:
        async with uow_factory() as uow:
            claimed = await uow.documents.claim(doc.id, lease_seconds=60)
            await uow.commit()
            return claimed

    results = await asyncio.gather(*(claim() for _ in range(8)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1  # eight racing workers, exactly one wins the lease
    assert winners[0].status is DocumentStatus.PROCESSING
    assert winners[0].attempts == 1


async def test_expired_lease_is_reclaimable_and_stale_sweep(
    uow_factory: Callable[[], UnitOfWork], engine: AsyncEngine
) -> None:
    tenant = await make_tenant(uow_factory)
    doc = make_document(tenant)
    async with uow_factory() as uow:
        await uow.documents.add(doc)
        assert await uow.documents.claim(doc.id, lease_seconds=60) is not None
        await uow.commit()
    async with engine.begin() as conn:  # simulate a worker that died an hour ago
        await conn.execute(
            text("UPDATE documents SET locked_until = now() - interval '1 hour' WHERE id = :id"),
            {"id": doc.id},
        )
    async with uow_factory() as uow:
        stale = await uow.documents.touch_stale(
            datetime.now(UTC) - timedelta(minutes=5), limit=1000
        )
        await uow.commit()
    assert doc.id in {d.id for d in stale}
    async with uow_factory() as uow:
        reclaimed = await uow.documents.claim(doc.id, lease_seconds=60)
        await uow.commit()
    assert reclaimed is not None
    assert reclaimed.attempts == 2


async def test_chunks_replace_ready_and_failure_transitions(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    tenant = await make_tenant(uow_factory)
    doc = make_document(tenant)
    vector = [0.0] * 383 + [1.0]
    chunks = [EmbeddedChunk(ChunkDraft(i, f"chunk {i}", 2, "S", 1, 1), vector) for i in range(3)]
    async with uow_factory() as uow:
        await uow.documents.add(doc)
        await uow.chunks.replace_for_document(doc, chunks)
        await uow.chunks.replace_for_document(doc, chunks[:2])  # idempotent re-run
        await uow.documents.mark_ready(doc.id, page_count=None, chunks=2)
        await uow.commit()
    async with uow_factory() as uow:
        stored = await uow.chunks.list_for_document(doc.id)
        ready = await uow.documents.get(tenant.id, doc.id)
    assert [c.index for c in stored] == [0, 1]
    assert ready is not None
    assert (ready.status, ready.chunk_count) == (DocumentStatus.READY, 2)
    assert ready.processed_at is not None

    async with uow_factory() as uow:
        await uow.documents.mark_failed(doc.id, "x" * 3000)
        await uow.commit()
    async with uow_factory() as uow:
        failed = await uow.documents.get(tenant.id, doc.id)
        requeued = await uow.documents.requeue(tenant.id, doc.id)
        await uow.commit()
    assert failed is not None
    assert len(failed.error or "") == 2000
    assert requeued is not None
    assert (requeued.status, requeued.attempts) == (DocumentStatus.QUEUED, 0)

    async with uow_factory() as uow:
        await uow.documents.delete(tenant.id, doc.id)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.chunks.list_for_document(doc.id) == []  # cascaded
