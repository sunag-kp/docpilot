"""Domain entities and value objects. Pure Python: no framework or I/O imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class DocumentKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    TEXT = "text"

    @property
    def content_type(self) -> str:
        return _CONTENT_TYPES[self]


_CONTENT_TYPES = {
    DocumentKind.PDF: "application/pdf",
    DocumentKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentKind.MARKDOWN: "text/markdown",
    DocumentKind.TEXT: "text/plain",
}


@dataclass(frozen=True, slots=True)
class Tenant:
    id: UUID
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    tenant_id: UUID
    email: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: UUID
    tenant_id: UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. Every data access is scoped by ``tenant_id``."""

    tenant_id: UUID
    subject_id: UUID
    kind: Literal["user", "api_key"]

    @property
    def rate_limit_key(self) -> str:
        return f"tenant:{self.tenant_id}"


@dataclass(frozen=True, slots=True)
class Document:
    id: UUID
    tenant_id: UUID
    filename: str
    kind: DocumentKind
    size_bytes: int
    sha256: str
    storage_key: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    error: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    processed_at: datetime | None = None


# --- parsing & chunking ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Block:
    """A structural unit produced by a parser: a heading or a paragraph of body text."""

    text: str
    page: int | None = None
    heading_level: int = 0  # 0 = body text, 1..6 = heading depth

    @property
    def is_heading(self) -> bool:
        return self.heading_level > 0


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    blocks: list[Block]
    page_count: int | None = None

    @property
    def text_length(self) -> int:
        return sum(len(block.text) for block in self.blocks)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    index: int
    text: str
    token_count: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None

    @property
    def embedding_text(self) -> str:
        """Prefix the section heading so the vector carries its context."""
        return f"{self.section}\n{self.text}" if self.section else self.text


@dataclass(frozen=True, slots=True)
class Chunk:
    id: UUID
    document_id: UUID
    tenant_id: UUID
    index: int
    text: str
    token_count: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    draft: ChunkDraft
    embedding: list[float] = field(repr=False)
