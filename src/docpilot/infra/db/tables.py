"""SQLAlchemy Core table definitions (mirrors migrations/versions/*_initial_schema.py).

Core instead of the ORM: queries here are hand-shaped (pgvector distance, full-text rank,
lease claims) and explicit SQL keeps them visible and cheap.
"""

from __future__ import annotations

from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

from docpilot.domain.models import DocumentKind, DocumentStatus

metadata = MetaData()


def _tenant_fk() -> Column[Any]:
    return Column(
        "tenant_id",
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


tenants = Table(
    "tenants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    _tenant_fk(),
    Column("email", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    _tenant_fk(),
    Column("name", Text, nullable=False),
    Column("prefix", Text, nullable=False),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

document_status = Enum(
    DocumentStatus,
    name="document_status",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)
document_kind = Enum(
    DocumentKind,
    name="document_kind",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)

documents = Table(
    "documents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    _tenant_fk(),
    Column("filename", Text, nullable=False),
    Column("kind", document_kind, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("storage_key", Text, nullable=False),
    Column("status", document_status, nullable=False),
    Column("error", Text),
    Column("attempts", Integer, nullable=False, default=0),
    Column("page_count", Integer),
    Column("chunk_count", Integer),
    Column("locked_until", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True)),
)


def make_chunks_table(embedding_dim: int) -> Table:
    return Table(
        "chunks",
        MetaData(),
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("document_id", UUID(as_uuid=True), nullable=False),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("text", Text, nullable=False),
        Column("section", Text),
        Column("page_start", Integer),
        Column("page_end", Integer),
        Column("token_count", Integer, nullable=False),
        Column("embedding", Vector(embedding_dim), nullable=False),
        Column("tsv", TSVECTOR),  # GENERATED ALWAYS column; never written by the app
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
