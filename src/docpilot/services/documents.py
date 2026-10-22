"""Document upload and lifecycle use cases."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import PurePath
from uuid import UUID, uuid4

from docpilot.domain.errors import (
    DocumentTooLargeError,
    InvalidInputError,
    NotFoundError,
    UnsupportedDocumentError,
)
from docpilot.domain.events import DocumentUploaded
from docpilot.domain.models import Chunk, Document, DocumentKind, DocumentStatus, Principal
from docpilot.observability.logging import get_logger
from docpilot.observability.metrics import DOCUMENTS_UPLOADED
from docpilot.services.ports import Clock, EventPublisher, ObjectStorage, UnitOfWorkFactory

_log = get_logger(__name__)

_EXTENSIONS = {
    ".pdf": DocumentKind.PDF,
    ".docx": DocumentKind.DOCX,
    ".md": DocumentKind.MARKDOWN,
    ".markdown": DocumentKind.MARKDOWN,
    ".txt": DocumentKind.TEXT,
}
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f/\\]")

CorpusChangedHook = Callable[[UUID], Awaitable[None]]


def sanitize_filename(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name
    name = _UNSAFE_FILENAME_CHARS.sub("", name).strip()
    if not name or name in {".", ".."}:
        raise InvalidInputError("filename is required")
    return name[-255:]


def detect_kind(filename: str, data: bytes) -> DocumentKind:
    """Trust the bytes, not the extension: the extension must agree with the content."""
    suffix = PurePath(filename).suffix.lower()
    kind = _EXTENSIONS.get(suffix)
    if kind is None:
        allowed = ", ".join(sorted(_EXTENSIONS))
        raise UnsupportedDocumentError(f"unsupported file type {suffix or '(none)'}; use {allowed}")
    if kind is DocumentKind.PDF and not data.startswith(b"%PDF-"):
        raise UnsupportedDocumentError("file has a .pdf extension but is not a PDF")
    if kind is DocumentKind.DOCX and not _is_docx(data):
        raise UnsupportedDocumentError("file has a .docx extension but is not a Word document")
    if kind in (DocumentKind.MARKDOWN, DocumentKind.TEXT):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedDocumentError("text documents must be UTF-8 encoded") from exc
    return kind


def _is_docx(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def storage_key_for(document: Document) -> str:
    suffix = PurePath(document.filename).suffix.lower()
    return f"tenants/{document.tenant_id}/documents/{document.id}/source{suffix}"


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    created: bool


class DocumentService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: ObjectStorage,
        publisher: EventPublisher,
        clock: Clock,
        *,
        max_bytes: int,
        on_corpus_changed: CorpusChangedHook | None = None,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._publisher = publisher
        self._clock = clock
        self._max_bytes = max_bytes
        self._on_corpus_changed = on_corpus_changed

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def upload(self, principal: Principal, filename: str, data: bytes) -> UploadResult:
        filename = sanitize_filename(filename)
        if len(data) > self._max_bytes:
            raise DocumentTooLargeError(f"file exceeds {self._max_bytes // (1024 * 1024)} MB")
        if not data:
            raise InvalidInputError("file is empty")
        kind = detect_kind(filename, data)
        sha256 = hashlib.sha256(data).hexdigest()

        # Idempotent upload: the same bytes uploaded twice by a tenant map to one document.
        async with self._uow() as uow:
            existing = await uow.documents.get_by_sha256(principal.tenant_id, sha256)
        if existing is not None:
            DOCUMENTS_UPLOADED.labels(kind.value, "true").inc()
            return UploadResult(document=existing, created=False)

        now = self._clock.now()
        document = Document(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            filename=filename,
            kind=kind,
            size_bytes=len(data),
            sha256=sha256,
            storage_key="",
            status=DocumentStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        document = replace(document, storage_key=storage_key_for(document))
        # Blob first, then row: a crash in between leaves an orphan blob (harmless, can be
        # garbage-collected) rather than a row pointing at nothing.
        await self._storage.put(document.storage_key, data, kind.content_type)
        async with self._uow() as uow:
            stored = await uow.documents.add(document)
            await uow.commit()
        if stored.id != document.id:  # lost a concurrent race for the same bytes
            await self._storage.delete(document.storage_key)
            DOCUMENTS_UPLOADED.labels(kind.value, "true").inc()
            return UploadResult(document=stored, created=False)

        await self._publish(stored)
        DOCUMENTS_UPLOADED.labels(kind.value, "false").inc()
        return UploadResult(document=stored, created=True)

    async def get(self, principal: Principal, document_id: UUID) -> Document:
        async with self._uow() as uow:
            document = await uow.documents.get(principal.tenant_id, document_id)
        if document is None:
            raise NotFoundError("document not found")
        return document

    async def list_documents(
        self,
        principal: Principal,
        *,
        limit: int = 50,
        offset: int = 0,
        status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]:
        async with self._uow() as uow:
            return await uow.documents.list_for_tenant(
                principal.tenant_id, limit=limit, offset=offset, status=status
            )

    async def chunks(self, principal: Principal, document_id: UUID) -> list[Chunk]:
        document = await self.get(principal, document_id)
        async with self._uow() as uow:
            return await uow.chunks.list_for_document(document.id)

    async def delete(self, principal: Principal, document_id: UUID) -> None:
        async with self._uow() as uow:
            document = await uow.documents.delete(principal.tenant_id, document_id)
            await uow.commit()
        if document is None:
            raise NotFoundError("document not found")
        await self._storage.delete(document.storage_key)
        if self._on_corpus_changed is not None:
            await self._on_corpus_changed(principal.tenant_id)

    async def retry(self, principal: Principal, document_id: UUID) -> Document:
        """Re-queue a failed document (e.g. after fixing a dead-lettered cause)."""
        async with self._uow() as uow:
            document = await uow.documents.requeue(principal.tenant_id, document_id)
            await uow.commit()
        if document is None:
            raise NotFoundError("no failed document with this id")
        await self._publish(document)
        return document

    async def _publish(self, document: Document) -> None:
        event = DocumentUploaded(
            document_id=document.id,
            tenant_id=document.tenant_id,
            storage_key=document.storage_key,
            kind=document.kind,
            filename=document.filename,
        )
        try:
            await self._publisher.publish_document_uploaded(event)
        except Exception:
            # The row is committed as `queued`; the stale-document sweeper re-publishes it,
            # so a broker outage delays ingestion instead of losing the upload.
            _log.warning(
                "publish_failed_will_be_swept", document_id=str(document.id), exc_info=True
            )
