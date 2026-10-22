"""Request/response models (the public API contract, rendered in /docs)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from docpilot.domain.models import ApiKey, Chunk, Document, DocumentKind, DocumentStatus

_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SignupRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_name: str = Field(min_length=2, max_length=100, examples=["Acme Corp"])
    email: str = Field(max_length=254, pattern=_EMAIL_PATTERN, examples=["dev@acme.test"])
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_at: datetime
    tenant_id: UUID
    user_id: UUID


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, examples=["ci-bot"])


class ApiKeyResponse(BaseModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, key: ApiKey) -> ApiKeyResponse:
        return cls(
            id=key.id,
            name=key.name,
            prefix=key.prefix,
            created_at=key.created_at,
            revoked_at=key.revoked_at,
        )


class ApiKeyCreatedResponse(ApiKeyResponse):
    key: str = Field(description="The secret. Shown once; store it now.")


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    kind: DocumentKind
    status: DocumentStatus
    size_bytes: int
    sha256: str
    attempts: int
    error: str | None
    page_count: int | None
    chunk_count: int | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None

    @classmethod
    def from_domain(cls, d: Document) -> DocumentResponse:
        return cls(
            id=d.id,
            filename=d.filename,
            kind=d.kind,
            status=d.status,
            size_bytes=d.size_bytes,
            sha256=d.sha256,
            attempts=d.attempts,
            error=d.error,
            page_count=d.page_count,
            chunk_count=d.chunk_count,
            created_at=d.created_at,
            updated_at=d.updated_at,
            processed_at=d.processed_at,
        )


class UploadResponse(DocumentResponse):
    duplicate: bool = Field(description="True if identical bytes were already uploaded")


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class ChunkResponse(BaseModel):
    id: UUID
    index: int
    text: str
    section: str | None
    page_start: int | None
    page_end: int | None
    token_count: int

    @classmethod
    def from_domain(cls, c: Chunk) -> ChunkResponse:
        return cls(
            id=c.id,
            index=c.index,
            text=c.text,
            section=c.section,
            page_start=c.page_start,
            page_end=c.page_end,
            token_count=c.token_count,
        )
