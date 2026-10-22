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
# almost never match. The question is reduced to its English lexemes (stop words dropped,
# stemmed) and OR-ed; candidates are chunks containing at least one of them.
#
# Ranking: Postgres' built-in ts_rank_cd has no notion of how *rare* a term is, so a
# question word that appears everywhere ("days") counts as much as the one that matters
# ("contractors"). We therefore compute Okapi BM25 in SQL: IDF from the tenant's corpus,
# term-frequency saturation (k1) and length normalisation (b) with the chunk token count.
# Cost grows with the number of matching chunks; beyond ~10^5 chunks per tenant a
# precomputed term-statistics table (or ParadeDB pg_search) would replace this.
_BM25_SQL = """
WITH terms AS (
    SELECT DISTINCT lexeme FROM unnest(to_tsvector('english', :query))
),
q AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query FROM terms
),
corpus AS (
    SELECT count(*)::float8 AS n, greatest(avg(token_count), 1)::float8 AS avgdl
    FROM chunks WHERE tenant_id = :tenant_id
),
matches AS (
    SELECT c.id, c.document_id, c.chunk_index, c.text, c.section, c.page_start, c.page_end,
           c.token_count, c.tsv
    FROM chunks c, q
    WHERE q.query IS NOT NULL AND c.tenant_id = :tenant_id AND c.tsv @@ q.query
),
tf AS (
    SELECT m.id, t.lexeme, cardinality(t.positions)::float8 AS tf, m.token_count
    FROM matches m, unnest(m.tsv) AS t
    WHERE t.lexeme IN (SELECT lexeme FROM terms)
),
df AS (
    SELECT lexeme, count(*)::float8 AS df FROM tf GROUP BY lexeme
)
SELECT m.id, m.document_id, m.chunk_index, m.text, m.section, m.page_start, m.page_end,
       d.filename,
       sum(
           ln(1 + (corpus.n - df.df + 0.5) / (df.df + 0.5))
           * tf.tf * (:k1 + 1)
           / (tf.tf + :k1 * (1 - :b + :b * tf.token_count / corpus.avgdl))
       ) AS rank
FROM tf
JOIN df USING (lexeme)
JOIN matches m ON m.id = tf.id
JOIN documents d ON d.id = m.document_id
CROSS JOIN corpus
WHERE true {document_filter}
GROUP BY m.id, m.document_id, m.chunk_index, m.text, m.section, m.page_start, m.page_end,
         d.filename
ORDER BY rank DESC, m.id
LIMIT :limit
"""

# Baseline kept for the evaluation ablation (KEYWORD_RANKER=ts_rank).
_TS_RANK_SQL = """
WITH q AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
    FROM unnest(to_tsvector('english', :query))
)
SELECT c.id, c.document_id, c.chunk_index, c.text, c.section, c.page_start, c.page_end,
       d.filename, ts_rank_cd(c.tsv, q.query) AS rank
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
    def __init__(
        self,
        engine: AsyncEngine,
        embedding_dim: int,
        *,
        ef_search: int = 100,
        keyword_ranker: str = "bm25",
        bm25_k1: float = 1.2,
        bm25_b: float = 0.75,
    ) -> None:
        if keyword_ranker not in {"bm25", "ts_rank"}:
            raise ValueError(f"unknown keyword ranker {keyword_ranker!r}")
        self._engine = engine
        self._chunks = make_chunks_table(embedding_dim)
        self._ef_search = ef_search
        self._ranker = keyword_ranker
        self._k1 = bm25_k1
        self._b = bm25_b

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
        alias = "m" if self._ranker == "bm25" else "c"
        document_filter = ""
        if document_ids:
            document_filter = f"AND {alias}.document_id = ANY(:document_ids)"
            params["document_ids"] = list(document_ids)
        if self._ranker == "bm25":
            params |= {"k1": self._k1, "b": self._b}
            sql = text(_BM25_SQL.format(document_filter=document_filter))
        else:
            sql = text(_TS_RANK_SQL.format(document_filter=document_filter))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sql, params)).all()
        # Squash the unbounded score into [0, 1) for display; ranks are what RRF uses.
        return [
            _scored(row, keyword_rank=float(row.rank) / (float(row.rank) + 1.0)) for row in rows
        ]


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
