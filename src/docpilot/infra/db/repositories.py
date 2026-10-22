"""PostgreSQL repositories over a single connection/transaction (see ``SqlUnitOfWork``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from docpilot.domain.errors import ConflictError
from docpilot.domain.models import (
    ApiKey,
    Chunk,
    Document,
    DocumentKind,
    DocumentStatus,
    EmbeddedChunk,
    Tenant,
    User,
)
from docpilot.infra.db.tables import api_keys, documents, make_chunks_table, tenants, users


def _document(row: Any) -> Document:
    return Document(
        id=row.id,
        tenant_id=row.tenant_id,
        filename=row.filename,
        kind=DocumentKind(row.kind),
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_key=row.storage_key,
        status=DocumentStatus(row.status),
        error=row.error,
        attempts=row.attempts,
        page_count=row.page_count,
        chunk_count=row.chunk_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        processed_at=row.processed_at,
    )


def _api_key(row: Any) -> ApiKey:
    return ApiKey(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class SqlTenantRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def add(self, tenant: Tenant) -> None:
        await self._conn.execute(
            insert(tenants).values(id=tenant.id, name=tenant.name, created_at=tenant.created_at)
        )

    async def get(self, tenant_id: UUID) -> Tenant | None:
        row = (await self._conn.execute(select(tenants).where(tenants.c.id == tenant_id))).first()
        return Tenant(id=row.id, name=row.name, created_at=row.created_at) if row else None


class SqlUserRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def add(self, user: User) -> None:
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    insert(users).values(
                        id=user.id,
                        tenant_id=user.tenant_id,
                        email=user.email,
                        password_hash=user.password_hash,
                        created_at=user.created_at,
                    )
                )
        except IntegrityError as exc:  # concurrent signup with the same email
            raise ConflictError("an account with this email already exists") from exc

    async def get_by_email(self, email: str) -> User | None:
        query = select(users).where(func.lower(users.c.email) == email.lower())
        row = (await self._conn.execute(query)).first()
        if row is None:
            return None
        return User(
            id=row.id,
            tenant_id=row.tenant_id,
            email=row.email,
            password_hash=row.password_hash,
            created_at=row.created_at,
        )


class SqlApiKeyRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def add(self, key: ApiKey, key_hash: str) -> None:
        await self._conn.execute(
            insert(api_keys).values(
                id=key.id,
                tenant_id=key.tenant_id,
                name=key.name,
                prefix=key.prefix,
                key_hash=key_hash,
                created_at=key.created_at,
            )
        )

    async def get_active_by_hash(self, key_hash: str) -> ApiKey | None:
        query = select(api_keys).where(
            api_keys.c.key_hash == key_hash, api_keys.c.revoked_at.is_(None)
        )
        row = (await self._conn.execute(query)).first()
        return _api_key(row) if row else None

    async def list_for_tenant(self, tenant_id: UUID) -> list[ApiKey]:
        query = (
            select(api_keys)
            .where(api_keys.c.tenant_id == tenant_id)
            .order_by(api_keys.c.created_at.desc())
        )
        return [_api_key(row) for row in await self._conn.execute(query)]

    async def revoke(self, tenant_id: UUID, key_id: UUID, at: datetime) -> bool:
        result = await self._conn.execute(
            update(api_keys)
            .where(
                api_keys.c.id == key_id,
                api_keys.c.tenant_id == tenant_id,
                api_keys.c.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        return bool(result.rowcount)


class SqlDocumentRepository:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def add(self, document: Document) -> Document:
        stmt = (
            pg_insert(documents)
            .values(
                id=document.id,
                tenant_id=document.tenant_id,
                filename=document.filename,
                kind=document.kind,
                size_bytes=document.size_bytes,
                sha256=document.sha256,
                storage_key=document.storage_key,
                status=document.status,
                attempts=document.attempts,
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
            .on_conflict_do_nothing(constraint="documents_tenant_sha256_key")
            .returning(documents)
        )
        row = (await self._conn.execute(stmt)).first()
        if row is not None:
            return _document(row)
        existing = await self.get_by_sha256(document.tenant_id, document.sha256)
        assert existing is not None
        return existing

    async def get(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        query = select(documents).where(
            documents.c.id == document_id, documents.c.tenant_id == tenant_id
        )
        row = (await self._conn.execute(query)).first()
        return _document(row) if row else None

    async def get_by_sha256(self, tenant_id: UUID, sha256: str) -> Document | None:
        query = select(documents).where(
            documents.c.tenant_id == tenant_id, documents.c.sha256 == sha256
        )
        row = (await self._conn.execute(query)).first()
        return _document(row) if row else None

    async def list_for_tenant(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        status: DocumentStatus | None = None,
    ) -> tuple[list[Document], int]:
        conditions = [documents.c.tenant_id == tenant_id]
        if status is not None:
            conditions.append(documents.c.status == status)
        query = (
            select(documents, func.count().over().label("total"))
            .where(*conditions)
            .order_by(documents.c.created_at.desc(), documents.c.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._conn.execute(query)).all()
        if rows:
            return [_document(row) for row in rows], int(rows[0].total)
        total = await self._conn.scalar(select(func.count()).where(*conditions))
        return [], int(total or 0)

    async def delete(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        stmt = (
            delete(documents)
            .where(documents.c.id == document_id, documents.c.tenant_id == tenant_id)
            .returning(documents)
        )
        row = (await self._conn.execute(stmt)).first()
        return _document(row) if row else None

    async def claim(self, document_id: UUID, lease_seconds: int) -> Document | None:
        # Lease times use the database clock only, so app-server clock skew can't make two
        # workers believe they both hold the lease.
        now = func.now()
        stmt = (
            update(documents)
            .where(
                documents.c.id == document_id,
                or_(
                    documents.c.status == DocumentStatus.QUEUED,
                    and_(
                        documents.c.status == DocumentStatus.PROCESSING,
                        documents.c.locked_until < now,
                    ),
                ),
            )
            .values(
                status=DocumentStatus.PROCESSING,
                attempts=documents.c.attempts + 1,
                locked_until=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            .returning(documents)
        )
        row = (await self._conn.execute(stmt)).first()
        return _document(row) if row else None

    async def release_for_retry(self, document_id: UUID, error: str) -> None:
        await self._conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                status=DocumentStatus.QUEUED,
                locked_until=None,
                error=error[:2000],
                updated_at=func.now(),
            )
        )

    async def mark_ready(self, document_id: UUID, page_count: int | None, chunks: int) -> None:
        await self._conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                status=DocumentStatus.READY,
                page_count=page_count,
                chunk_count=chunks,
                error=None,
                locked_until=None,
                updated_at=func.now(),
                processed_at=func.now(),
            )
        )

    async def mark_failed(self, document_id: UUID, error: str) -> None:
        await self._conn.execute(
            update(documents)
            .where(documents.c.id == document_id)
            .values(
                status=DocumentStatus.FAILED,
                error=error[:2000],
                locked_until=None,
                updated_at=func.now(),
            )
        )

    async def requeue(self, tenant_id: UUID, document_id: UUID) -> Document | None:
        stmt = (
            update(documents)
            .where(
                documents.c.id == document_id,
                documents.c.tenant_id == tenant_id,
                documents.c.status == DocumentStatus.FAILED,
            )
            .values(status=DocumentStatus.QUEUED, attempts=0, error=None, updated_at=func.now())
            .returning(documents)
        )
        row = (await self._conn.execute(stmt)).first()
        return _document(row) if row else None

    async def touch_stale(self, older_than: datetime, limit: int) -> list[Document]:
        # SKIP LOCKED lets several workers sweep concurrently without picking the same rows.
        stale_ids = (
            select(documents.c.id)
            .where(
                or_(
                    and_(
                        documents.c.status == DocumentStatus.QUEUED,
                        documents.c.updated_at < older_than,
                    ),
                    and_(
                        documents.c.status == DocumentStatus.PROCESSING,
                        documents.c.locked_until < func.now(),
                    ),
                )
            )
            .order_by(documents.c.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = (
            update(documents)
            .where(documents.c.id.in_(stale_ids))
            .values(updated_at=func.now())
            .returning(documents)
        )
        return [_document(row) for row in await self._conn.execute(stmt)]


class SqlChunkRepository:
    def __init__(self, conn: AsyncConnection, embedding_dim: int) -> None:
        self._conn = conn
        self._chunks = make_chunks_table(embedding_dim)

    async def replace_for_document(
        self, document: Document, chunks: Sequence[EmbeddedChunk]
    ) -> None:
        table = self._chunks
        await self._conn.execute(delete(table).where(table.c.document_id == document.id))
        if not chunks:
            return
        created_at = datetime.now(UTC)
        await self._conn.execute(
            insert(table),
            [
                {
                    "id": uuid4(),
                    "document_id": document.id,
                    "tenant_id": document.tenant_id,
                    "chunk_index": item.draft.index,
                    "text": item.draft.text,
                    "section": item.draft.section,
                    "page_start": item.draft.page_start,
                    "page_end": item.draft.page_end,
                    "token_count": item.draft.token_count,
                    "embedding": item.embedding,
                    "created_at": created_at,
                }
                for item in chunks
            ],
        )

    async def list_for_document(self, document_id: UUID) -> list[Chunk]:
        table = self._chunks
        query = (
            select(
                table.c.id,
                table.c.document_id,
                table.c.tenant_id,
                table.c.chunk_index,
                table.c.text,
                table.c.token_count,
                table.c.section,
                table.c.page_start,
                table.c.page_end,
            )
            .where(table.c.document_id == document_id)
            .order_by(table.c.chunk_index)
        )
        return [
            Chunk(
                id=row.id,
                document_id=row.document_id,
                tenant_id=row.tenant_id,
                index=row.chunk_index,
                text=row.text,
                token_count=row.token_count,
                section=row.section,
                page_start=row.page_start,
                page_end=row.page_end,
            )
            for row in await self._conn.execute(query)
        ]


class SqlUnitOfWork:
    """One connection + one transaction; rolled back unless ``commit()`` is called."""

    tenants: SqlTenantRepository
    users: SqlUserRepository
    api_keys: SqlApiKeyRepository
    documents: SqlDocumentRepository
    chunks: SqlChunkRepository

    def __init__(self, engine: AsyncEngine, embedding_dim: int) -> None:
        self._engine = engine
        self._embedding_dim = embedding_dim
        self._conn: AsyncConnection | None = None
        self._tx: AsyncTransaction | None = None

    async def __aenter__(self) -> Self:
        self._conn = await self._engine.connect()
        self._tx = await self._conn.begin()
        self.tenants = SqlTenantRepository(self._conn)
        self.users = SqlUserRepository(self._conn)
        self.api_keys = SqlApiKeyRepository(self._conn)
        self.documents = SqlDocumentRepository(self._conn)
        self.chunks = SqlChunkRepository(self._conn, self._embedding_dim)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._conn is not None
        try:
            if self._tx is not None and self._tx.is_active:
                await self._tx.rollback()
        finally:
            await self._conn.close()

    async def commit(self) -> None:
        assert self._tx is not None
        await self._tx.commit()
