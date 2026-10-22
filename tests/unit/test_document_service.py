from __future__ import annotations

from uuid import uuid4

import pytest

from docpilot.domain.errors import (
    DocumentTooLargeError,
    InvalidInputError,
    NotFoundError,
    UnsupportedDocumentError,
)
from docpilot.domain.models import DocumentKind, DocumentStatus, Principal
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.storage.local import InMemoryObjectStorage
from docpilot.services.documents import DocumentService, detect_kind, sanitize_filename
from tests.samples import make_docx, make_pdf

ALICE = Principal(tenant_id=uuid4(), subject_id=uuid4(), kind="user")
BOB = Principal(tenant_id=uuid4(), subject_id=uuid4(), kind="user")
MD = b"# Title\n\nSome body text about vacation.\n"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\x\\a.md", "a.md"),
        ("a\x00b.txt", "ab.txt"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "..", "/"])
def test_sanitize_filename_rejects_empty(raw: str) -> None:
    with pytest.raises(InvalidInputError):
        sanitize_filename(raw)


def test_detect_kind_checks_magic_bytes() -> None:
    assert detect_kind("a.pdf", make_pdf([["Hello world."]])) is DocumentKind.PDF
    assert detect_kind("a.docx", make_docx([("Hi", 0)])) is DocumentKind.DOCX
    assert detect_kind("a.MD", b"# hi") is DocumentKind.MARKDOWN
    assert detect_kind("a.txt", "héllo".encode()) is DocumentKind.TEXT
    for name, data in [
        ("a.pdf", b"not a pdf"),
        ("a.docx", b"PK\x03\x04garbage"),
        ("a.docx", b"plain"),
        ("a.txt", b"\xff\xfe\x00bad"),
        ("a.exe", b"MZ"),
        ("noext", b"x"),
    ]:
        with pytest.raises(UnsupportedDocumentError):
            detect_kind(name, data)


async def test_upload_stores_blob_persists_row_and_publishes_event(
    document_service: DocumentService, storage: InMemoryObjectStorage, bus: InMemoryEventBus
) -> None:
    result = await document_service.upload(ALICE, "handbook.md", MD)
    doc = result.document
    assert result.created
    assert doc.status is DocumentStatus.QUEUED
    assert doc.storage_key == f"tenants/{ALICE.tenant_id}/documents/{doc.id}/source.md"
    assert storage.objects[doc.storage_key] == (MD, "text/markdown")
    assert [e.document_id for e in bus.published] == [doc.id]


async def test_same_bytes_are_deduplicated_per_tenant(
    document_service: DocumentService, bus: InMemoryEventBus
) -> None:
    first = await document_service.upload(ALICE, "a.md", MD)
    again = await document_service.upload(ALICE, "renamed.md", MD)
    other_tenant = await document_service.upload(BOB, "a.md", MD)
    assert not again.created
    assert again.document.id == first.document.id
    assert other_tenant.created
    assert other_tenant.document.id != first.document.id
    assert len(bus.published) == 2


async def test_upload_validation(document_service: DocumentService) -> None:
    with pytest.raises(DocumentTooLargeError):
        await document_service.upload(ALICE, "big.txt", b"x" * (1024 * 1024 + 1))
    with pytest.raises(InvalidInputError):
        await document_service.upload(ALICE, "empty.txt", b"")


async def test_broker_outage_does_not_fail_the_upload(
    document_service: DocumentService, bus: InMemoryEventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(_: object) -> None:
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(bus, "publish_document_uploaded", down)
    result = await document_service.upload(ALICE, "a.md", MD)
    assert result.created  # row committed as queued; the sweeper will re-publish it
    assert (await document_service.get(ALICE, result.document.id)).status is DocumentStatus.QUEUED


async def test_tenant_isolation_on_read_and_delete(document_service: DocumentService) -> None:
    doc = (await document_service.upload(ALICE, "a.md", MD)).document
    with pytest.raises(NotFoundError):
        await document_service.get(BOB, doc.id)
    with pytest.raises(NotFoundError):
        await document_service.delete(BOB, doc.id)
    items, total = await document_service.list_documents(BOB)
    assert (items, total) == ([], 0)


async def test_list_filters_and_paginates(document_service: DocumentService) -> None:
    for i in range(3):
        await document_service.upload(ALICE, f"{i}.md", MD + str(i).encode())
    items, total = await document_service.list_documents(ALICE, limit=2, offset=0)
    assert (len(items), total) == (2, 3)
    ready, ready_total = await document_service.list_documents(ALICE, status=DocumentStatus.READY)
    assert (ready, ready_total) == ([], 0)


async def test_delete_removes_blob_and_notifies_cache(
    uow_factory, storage: InMemoryObjectStorage, bus: InMemoryEventBus, clock
) -> None:  # type: ignore[no-untyped-def]
    changed: list[object] = []

    async def hook(tenant_id: object) -> None:
        changed.append(tenant_id)

    service = DocumentService(
        uow_factory, storage, bus, clock, max_bytes=10_000, on_corpus_changed=hook
    )
    doc = (await service.upload(ALICE, "a.md", MD)).document
    await service.delete(ALICE, doc.id)
    assert storage.objects == {}
    assert changed == [ALICE.tenant_id]
    with pytest.raises(NotFoundError):
        await service.get(ALICE, doc.id)


async def test_retry_only_requeues_failed_documents(
    document_service: DocumentService, uow_factory, bus: InMemoryEventBus
) -> None:  # type: ignore[no-untyped-def]
    doc = (await document_service.upload(ALICE, "a.md", MD)).document
    with pytest.raises(NotFoundError):
        await document_service.retry(ALICE, doc.id)  # queued, not failed
    async with uow_factory() as uow:
        await uow.documents.mark_failed(doc.id, "boom")
        await uow.commit()
    requeued = await document_service.retry(ALICE, doc.id)
    assert requeued.status is DocumentStatus.QUEUED
    assert requeued.attempts == 0
    assert len(bus.published) == 2
