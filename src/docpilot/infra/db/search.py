"""Hybrid search primitives over Postgres: pgvector ANN and full-text ranking."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from docpilot.domain.retrieval import ScoredChunk
from docpilot.infra.db.tables import documents, make_chunks_table

# Questions are natural language, so AND-ing every term (websearch_to_tsquery) would
# almost never match. Instead the question is reduced to its English lexemes (stop words
# dropped, stemmed) and OR-ed; ts_rank_cd then rewards passages covering more of them,
# densely. Section headings carry weight A, body text weight B (see the migration).
_KEYWORD_SQL = """
WITH q AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
    FROM unnest(to_tsvector('english', :query))
)
SELECT c.id, c.document_id, c.chunk_index, c.text, c.section, c.page_start, c.page_end,
       d.filename, ts_rank_cd(c.tsv, q.query, 32) AS rank
FROM q
JOIN chunks c ON c.tsv @@ q.query
JOIN documents d ON d.id = c.document_id
WHERE q.query IS NOT NULL
  AND c.tenant_id = :tenant_id
  {document_filter}
ORDER BY rank DESC, c.id
LIMIT :limit
"""


class PgChunkSearch:
    def __init__(self, engine: AsyncEngine, embedding_dim: int, *, ef_search: int = 100) -> None:
        self._engine = engine
        self._chunks = make_chunks_table(embedding_dim)
        self._ef_search = ef_search

    async def vector_search(
        self,
        tenant_id: UUID,
        embedding: Sequence[float],
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        chunks = self._chunks
        distance = chunks.c.embedding.cosine_distance(list(embedding)).label("distance")
        conditions = [chunks.c.tenant_id == tenant_id]
        if document_ids:
            conditions.append(chunks.c.document_id.in_(list(document_ids)))
        # Inner query is a pure ORDER BY distance LIMIT k, so Postgres can use the HNSW
        # index; the filename join happens on the k survivors only.
        nearest = (
            select(
                chunks.c.id,
                chunks.c.document_id,
                chunks.c.chunk_index,
                chunks.c.text,
                chunks.c.section,
                chunks.c.page_start,
                chunks.c.page_end,
                distance,
            )
            .where(*conditions)
            .order_by(distance)
            .limit(limit)
            .subquery()
        )
        query = (
            select(nearest, documents.c.filename)
            .join(documents, documents.c.id == nearest.c.document_id)
            .order_by(nearest.c.distance, nearest.c.id)
        )
        async with self._engine.begin() as conn:
            # Filtering by tenant inside an HNSW scan can return fewer than k rows;
            # iterative scans (pgvector >= 0.8) keep walking the graph until k match.
            await conn.execute(
                text(
                    "SELECT set_config('hnsw.ef_search', :ef, true),"
                    " set_config('hnsw.iterative_scan', 'relaxed_order', true)"
                ),
                {"ef": str(self._ef_search)},
            )
            rows = (await conn.execute(query)).all()
        return [_scored(row, vector_similarity=1.0 - float(row.distance)) for row in rows]

    async def keyword_search(
        self,
        tenant_id: UUID,
        query: str,
        limit: int,
        document_ids: Sequence[UUID] | None = None,
    ) -> list[ScoredChunk]:
        params: dict[str, Any] = {"query": query, "tenant_id": tenant_id, "limit": limit}
        document_filter = ""
        if document_ids:
            document_filter = "AND c.document_id = ANY(:document_ids)"
            params["document_ids"] = list(document_ids)
        sql = text(_KEYWORD_SQL.format(document_filter=document_filter))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        return [_scored(row, keyword_rank=float(row.rank)) for row in rows]


def _scored(
    row: Any, *, vector_similarity: float | None = None, keyword_rank: float | None = None
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=row.id,
        document_id=row.document_id,
        filename=row.filename,
        index=row.chunk_index,
        text=row.text,
        section=row.section,
        page_start=row.page_start,
        page_end=row.page_end,
        vector_similarity=vector_similarity,
        keyword_rank=keyword_rank,
    )
