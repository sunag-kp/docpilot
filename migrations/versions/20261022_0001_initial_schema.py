"""Initial schema: tenants, users, API keys, documents and chunks (pgvector + full-text).

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from docpilot.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dim = get_settings().embedding_dim  # changing the model's dimension needs a re-embed migration
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE tenants (
            id          uuid PRIMARY KEY,
            name        text NOT NULL,
            created_at  timestamptz NOT NULL
        )""")
    op.execute("""
        CREATE TABLE users (
            id             uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            email          text NOT NULL,
            password_hash  text NOT NULL,
            created_at     timestamptz NOT NULL
        )""")
    op.execute("CREATE UNIQUE INDEX users_email_key ON users (lower(email))")
    op.execute("CREATE INDEX users_tenant_idx ON users (tenant_id)")
    op.execute("""
        CREATE TABLE api_keys (
            id          uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            name        text NOT NULL,
            prefix      text NOT NULL,
            key_hash    text NOT NULL UNIQUE,
            created_at  timestamptz NOT NULL,
            revoked_at  timestamptz
        )""")
    op.execute("CREATE INDEX api_keys_tenant_idx ON api_keys (tenant_id)")
    op.execute("CREATE TYPE document_status AS ENUM ('queued', 'processing', 'ready', 'failed')")
    op.execute("CREATE TYPE document_kind AS ENUM ('pdf', 'docx', 'markdown', 'text')")
    op.execute("""
        CREATE TABLE documents (
            id            uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            filename      text NOT NULL,
            kind          document_kind NOT NULL,
            size_bytes    bigint NOT NULL,
            sha256        text NOT NULL,
            storage_key   text NOT NULL,
            status        document_status NOT NULL DEFAULT 'queued',
            error         text,
            attempts      integer NOT NULL DEFAULT 0,
            page_count    integer,
            chunk_count   integer,
            locked_until  timestamptz,
            created_at    timestamptz NOT NULL,
            updated_at    timestamptz NOT NULL,
            processed_at  timestamptz,
            CONSTRAINT documents_tenant_sha256_key UNIQUE (tenant_id, sha256)
        )""")
    op.execute(
        "CREATE INDEX documents_tenant_created_idx ON documents (tenant_id, created_at DESC)"
    )
    # Partial index: the sweeper only ever looks at in-flight documents.
    op.execute(
        "CREATE INDEX documents_inflight_idx ON documents (updated_at) "
        "WHERE status IN ('queued', 'processing')"
    )
    op.execute(f"""
        CREATE TABLE chunks (
            id           uuid PRIMARY KEY,
            document_id  uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            tenant_id    uuid NOT NULL,
            chunk_index  integer NOT NULL,
            text         text NOT NULL,
            section      text,
            page_start   integer,
            page_end     integer,
            token_count  integer NOT NULL,
            embedding    vector({dim}) NOT NULL,
            tsv          tsvector GENERATED ALWAYS AS (
                             setweight(to_tsvector('english', coalesce(section, '')), 'A') ||
                             setweight(to_tsvector('english', text), 'B')
                         ) STORED,
            created_at   timestamptz NOT NULL,
            CONSTRAINT chunks_document_index_key UNIQUE (document_id, chunk_index)
        )""")
    op.execute("CREATE INDEX chunks_tenant_idx ON chunks (tenant_id)")
    op.execute("CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX chunks_embedding_hnsw_idx ON chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TYPE IF EXISTS document_kind")
    op.execute("DROP TYPE IF EXISTS document_status")
    op.execute("DROP TABLE IF EXISTS api_keys")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS tenants")
