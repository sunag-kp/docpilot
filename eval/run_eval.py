"""Evaluate DocPilot retrieval and answers on the labeled set in eval/qa.jsonl.

Runs in-process against a real Postgres (pgvector + full-text), with the same parser,
chunker, embedder, retriever and answering service the API uses. Kafka and MinIO are not
involved: they move bytes around but do not change what is retrieved or answered.

    make infra            # Postgres (+ friends) via docker compose
    uv run python eval/run_eval.py                                  # keyless defaults
    uv run python eval/run_eval.py --llm ollama --llm-model qwen2.5:3b \\
        --judge ollama --judge-model qwen2.5:7b                     # local LLM + LLM judge

Writes eval/results/<label>.json (every question, every rank) and eval/results/<label>.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from docpilot.config import EmbeddingProvider, LLMProvider, Settings
from docpilot.container import build_embedder, build_llm
from docpilot.domain.chunking import Chunker, ChunkingConfig
from docpilot.domain.models import DocumentStatus, Principal, Tenant
from docpilot.domain.retrieval import SearchMode
from docpilot.evaluation.dataset import EvalItem, load_dataset
from docpilot.evaluation.judge import FaithfulnessJudge
from docpilot.evaluation.metrics import (
    fact_recall,
    first_relevant_rank,
    hit_rate_at_k,
    is_relevant,
    mean_reciprocal_rank,
    percentile,
)
from docpilot.infra.bus.memory import InMemoryEventBus
from docpilot.infra.db.engine import create_engine
from docpilot.infra.db.repositories import SqlUnitOfWork
from docpilot.infra.db.search import PgChunkSearch
from docpilot.infra.parsing import DefaultDocumentParser
from docpilot.infra.security import SystemClock
from docpilot.infra.storage.local import InMemoryObjectStorage
from docpilot.observability.logging import configure_logging
from docpilot.services.answering import (
    AnswerCompleted,
    AnswerStarted,
    QueryRequest,
    QuestionAnswering,
)
from docpilot.services.documents import DocumentService
from docpilot.services.ingestion import IngestionPipeline
from docpilot.services.retrieval import HybridRetriever, RetrievalConfig

ROOT = Path(__file__).resolve().parent
KS = (1, 3, 5, 10)


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    answerable: bool
    ranks: dict[str, int | None] = field(default_factory=dict)
    answer: str = ""
    refused: bool = False
    grounded: bool = False
    facts_found: bool | None = None
    faithfulness: float | None = None
    judge_raw: str | None = None
    latency_ms: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", default=str(ROOT / "qa.jsonl"))
    parser.add_argument("--corpus", default=str(ROOT / "corpus"))
    parser.add_argument(
        "--embedding", choices=[e.value for e in EmbeddingProvider], default="fastembed"
    )
    parser.add_argument("--llm", choices=[p.value for p in LLMProvider], default="extractive")
    parser.add_argument("--llm-model", default=None)
    parser.add_argument(
        "--judge", choices=["none", "ollama", "openai", "anthropic"], default="none"
    )
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--top-k", type=int, default=5, help="passages given to the answerer")
    parser.add_argument("--keyword-ranker", choices=["bm25", "ts_rank"], default="bm25")
    parser.add_argument("--label", default=None, help="results file name (default: derived)")
    parser.add_argument("--keep-data", action="store_true", help="don't delete the eval tenant")
    return parser.parse_args()


async def ingest(
    settings: Settings, uow_factory: Any, embedder: Any, corpus: Path
) -> tuple[Principal, dict[str, Any]]:
    clock = SystemClock()
    principal = Principal(tenant_id=uuid4(), subject_id=uuid4(), kind="user")
    async with uow_factory() as uow:
        await uow.tenants.add(Tenant(principal.tenant_id, "eval", clock.now()))
        await uow.commit()
    bus, storage = InMemoryEventBus(), InMemoryObjectStorage()
    documents = DocumentService(
        uow_factory, storage, bus, clock, max_bytes=settings.max_upload_bytes
    )
    pipeline = IngestionPipeline(
        uow_factory,
        storage,
        DefaultDocumentParser(),
        Chunker(
            ChunkingConfig(
                max_tokens=settings.chunk_max_tokens,
                min_tokens=settings.chunk_min_tokens,
                overlap_sentences=settings.chunk_overlap_sentences,
                topic_shift_threshold=settings.chunk_topic_shift_threshold,
            )
        ),
        embedder,
        bus,
        clock,
        embed_batch_size=settings.embedding_batch_size,
    )
    files = sorted(p for p in corpus.iterdir() if p.is_file())  # noqa: ASYNC240 - CLI startup
    started = time.perf_counter()
    for path in files:
        await documents.upload(principal, path.name, path.read_bytes())
    for event in list(bus.published):
        await pipeline.handle(event)
    elapsed = time.perf_counter() - started
    docs, _ = await documents.list_documents(principal, limit=100)
    failed = [d.filename for d in docs if d.status is not DocumentStatus.READY]
    if failed:
        raise SystemExit(f"ingestion failed for {failed}")
    chunks = sum(d.chunk_count or 0 for d in docs)
    return principal, {
        "documents": len(docs),
        "chunks": chunks,
        "seconds": round(elapsed, 2),
        "formats": sorted({d.kind.value for d in docs}),
    }


async def validate_labels(
    items: list[EvalItem], documents: DocumentService, principal: Principal
) -> None:
    """Fail fast if a label's evidence text does not exist in the corpus."""
    docs, _ = await documents.list_documents(principal, limit=100)
    texts: list[str] = []
    for doc in docs:
        texts.extend(c.text for c in await documents.chunks(principal, doc.id))
    problems = []
    for item in items:
        if not item.answerable:
            continue
        matches = sum(is_relevant(t, item.evidence) for t in texts)
        if matches == 0:
            problems.append(f"{item.id}: evidence not found in any chunk")
    if problems:
        raise SystemExit("broken labels:\n  " + "\n  ".join(problems))


def judge_llm(kind: str, model: str | None, settings: Settings) -> FaithfulnessJudge | None:
    if kind == "none":
        return None
    judge_settings = settings.model_copy(
        update={"llm_provider": LLMProvider(kind), "llm_model": model}
    )
    return FaithfulnessJudge(build_llm(judge_settings))


async def main() -> int:
    args = parse_args()
    configure_logging("WARNING", json=False)
    settings = Settings(
        embedding_provider=EmbeddingProvider(args.embedding),
        llm_provider=LLMProvider(args.llm),
        llm_model=args.llm_model,
        keyword_ranker=args.keyword_ranker,
    )
    await asyncio.to_thread(command.upgrade, Config(str(ROOT.parent / "alembic.ini")), "head")
    engine = create_engine(settings)

    def uow_factory() -> SqlUnitOfWork:
        return SqlUnitOfWork(engine, settings.embedding_dim)

    embedder = build_embedder(settings)
    items = load_dataset(args.dataset)
    principal, ingestion = await ingest(settings, uow_factory, embedder, Path(args.corpus))
    documents = DocumentService(
        uow_factory, InMemoryObjectStorage(), InMemoryEventBus(), SystemClock(), max_bytes=1
    )
    try:
        await validate_labels(items, documents, principal)
        retriever = HybridRetriever(
            PgChunkSearch(engine, settings.embedding_dim, keyword_ranker=settings.keyword_ranker),
            embedder,
            RetrievalConfig(
                candidates=settings.retrieval_candidates,
                rrf_k=settings.rrf_k,
                min_vector_similarity=settings.min_vector_similarity,
            ),
        )
        llm = build_llm(settings, embedder)
        answering = QuestionAnswering(
            retriever, llm, cache=None, max_tokens=settings.llm_max_tokens
        )
        judge = judge_llm(args.judge, args.judge_model, settings)

        results: list[QuestionResult] = []
        for item in items:
            result = QuestionResult(item.id, item.category, item.question, item.answerable)
            for mode in SearchMode:
                retrieved = await retriever.retrieve(
                    principal.tenant_id, item.question, top_k=max(KS), mode=mode
                )
                result.ranks[mode.value] = (
                    first_relevant_rank([c.text for c in retrieved.chunks], item.evidence)
                    if item.answerable
                    else None
                )
            started = time.perf_counter()
            sources = []
            async for event in answering.answer(
                principal.tenant_id, QueryRequest(question=item.question, top_k=args.top_k)
            ):
                if isinstance(event, AnswerStarted):
                    sources = event.citations
                elif isinstance(event, AnswerCompleted):
                    result.answer, result.refused, result.grounded = (
                        event.answer,
                        event.refused,
                        event.grounded,
                    )
            result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            if item.answerable:
                result.facts_found = fact_recall(result.answer, item.facts)
            if judge is not None and not result.refused:
                verdict = await judge.judge(item.question, result.answer, sources)
                result.faithfulness, result.judge_raw = verdict.faithfulness, verdict.raw
            results.append(result)
            print(
                f"  {item.id:<22} ranks={result.ranks} refused={result.refused} "
                f"facts={result.facts_found}",
                file=sys.stderr,
            )

        report = summarise(
            results, args, settings, ingestion, llm.name, judge.name if judge else None
        )
    finally:
        if not args.keep_data:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenants WHERE id = :id"), {"id": principal.tenant_id}
                )
        await engine.dispose()

    label = args.label or f"{args.embedding}-{args.llm}" + (
        f"-judge-{args.judge}" if args.judge != "none" else ""
    )
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{label}.json").write_text(
        json.dumps({**report, "questions": [asdict(r) for r in results]}, indent=2) + "\n"
    )
    (out / f"{label}.md").write_text(render_markdown(report) + "\n")
    print(render_markdown(report))
    print(f"\nwrote eval/results/{label}.json and .md", file=sys.stderr)
    return 0


def summarise(
    results: list[QuestionResult],
    args: argparse.Namespace,
    settings: Settings,
    ingestion: dict[str, Any],
    llm_name: str,
    judge_name: str | None,
) -> dict[str, Any]:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    retrieval = {
        mode.value: {
            **{
                f"hit@{k}": round(hit_rate_at_k([r.ranks[mode.value] for r in answerable], k), 3)
                for k in KS
            },
            "mrr@10": round(mean_reciprocal_rank([r.ranks[mode.value] for r in answerable], 10), 3),
        }
        for mode in SearchMode
    }
    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({r.category for r in answerable}):
        subset = [r for r in answerable if r.category == category]
        by_category[category] = {
            "n": len(subset),
            **{
                f"{mode.value} hit@5": round(
                    hit_rate_at_k([r.ranks[mode.value] for r in subset], 5), 3
                )
                for mode in SearchMode
            },
        }
    faithfulness = [r.faithfulness for r in results if r.faithfulness is not None]
    judged = [r for r in results if not r.refused]
    latencies = [r.latency_ms for r in results]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {
            "embedding": (
                f"fastembed:{settings.embedding_model}"
                if settings.embedding_provider is EmbeddingProvider.FASTEMBED
                else settings.embedding_provider.value
            ),
            "llm": llm_name,
            "judge": judge_name,
            "answer_top_k": args.top_k,
            "retrieval_candidates": settings.retrieval_candidates,
            "rrf_k": settings.rrf_k,
            "keyword_ranker": settings.keyword_ranker,
            "extractive_min_similarity": settings.extractive_min_similarity,
            "min_vector_similarity": settings.min_vector_similarity,
            "chunk_max_tokens": settings.chunk_max_tokens,
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "dataset": {
            "questions": len(results),
            "answerable": len(answerable),
            "unanswerable": len(unanswerable),
        },
        "ingestion": ingestion,
        "retrieval": retrieval,
        "retrieval_by_category": by_category,
        "answers": {
            "fact_recall": round(sum(bool(r.facts_found) for r in answerable) / len(answerable), 3),
            "false_refusal_rate": round(sum(r.refused for r in answerable) / len(answerable), 3),
            "correct_refusal_rate": round(
                sum(r.refused for r in unanswerable) / max(1, len(unanswerable)), 3
            ),
            "citation_validity": round(sum(r.grounded for r in results) / len(results), 3),
            "faithfulness": round(sum(faithfulness) / len(faithfulness), 3)
            if faithfulness
            else None,
            "faithfulness_judged": len(faithfulness),
            "faithfulness_unparseable": len(judged) - len(faithfulness) if judge_name else None,
            "latency_ms_p50": percentile(latencies, 50),
            "latency_ms_p95": percentile(latencies, 95),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    cfg, ds, ans = report["config"], report["dataset"], report["answers"]
    lines = [
        f"# DocPilot evaluation — {report['generated_at']}",
        "",
        f"Embedding `{cfg['embedding']}` · keyword ranker `{cfg['keyword_ranker']}` · "
        f"answerer `{cfg['llm']}` · judge `{cfg['judge'] or 'none'}` · "
        f"{ds['questions']} questions ({ds['answerable']} answerable, "
        f"{ds['unanswerable']} unanswerable) · "
        f"corpus {report['ingestion']['documents']} docs / {report['ingestion']['chunks']} chunks "
        f"({', '.join(report['ingestion']['formats'])})",
        "",
        "## Retrieval (answerable questions)",
        "",
        "| Mode | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 |",
        "|---|---|---|---|---|---|",
    ]
    for mode, m in report["retrieval"].items():
        lines.append(
            f"| {mode} | {m['hit@1']:.3f} | {m['hit@3']:.3f} | {m['hit@5']:.3f} "
            f"| {m['hit@10']:.3f} | {m['mrr@10']:.3f} |"
        )
    lines += [
        "",
        "### hit@5 by question type",
        "",
        "| Type | n | vector | keyword | hybrid |",
        "|---|---|---|---|---|",
    ]
    for category, m in report["retrieval_by_category"].items():
        lines.append(
            f"| {category} | {m['n']} | {m['vector hit@5']:.3f} "
            f"| {m['keyword hit@5']:.3f} | {m['hybrid hit@5']:.3f} |"
        )
    faith = (
        f"{ans['faithfulness']:.3f} ({ans['faithfulness_judged']} answers judged)"
        if ans["faithfulness"] is not None
        else "not measured (no judge)"
    )
    lines += [
        "",
        "## Answers (hybrid retrieval)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fact recall (answer contains the expected facts) | {ans['fact_recall']:.3f} |",
        f"| Correct refusals on unanswerable questions | {ans['correct_refusal_rate']:.3f} |",
        f"| False refusals on answerable questions | {ans['false_refusal_rate']:.3f} |",
        "| Citation validity (cites only real sources, or refuses) "
        f"| {ans['citation_validity']:.3f} |",
        f"| Faithfulness (LLM judge: supported sentences / sentences) | {faith} |",
        "| Answer latency p50 / p95 (ms) "
        f"| {ans['latency_ms_p50']:.0f} / {ans['latency_ms_p95']:.0f} |",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
