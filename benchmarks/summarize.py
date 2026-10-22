"""Turn Locust CSV output into benchmarks/results/<run>/summary.md (+ environment.json).

uv run python benchmarks/summarize.py benchmarks/results/<run>
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
from pathlib import Path


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def memory_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return 0.0


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()  # noqa: S607
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as handle:
        return list(csv.DictReader(handle))


def table(stats: list[dict[str, str]]) -> list[str]:
    lines = [
        "| Endpoint | Requests | Failures | Req/s | p50 (ms) | p95 (ms) | p99 (ms) |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['Name']} | {row['Request Count']} | {row['Failure Count']} "
        f"| {float(row['Requests/s']):.1f} | {row['50%']} | {row['95%']} | {row['99%']} |"
        for row in stats
    )
    return lines


def stages(path: Path, title: str) -> list[str]:
    if not path.exists():
        return []
    snapshot = json.loads(path.read_text())
    lines = [
        f"{title} (from the services' Prometheus histograms, {snapshot['window']} window; "
        "includes time waiting for the CPU / event loop):",
        "",
        "| Stage | p50 (ms) | p95 (ms) |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {name} | {v.get('p50_ms', '-')} | {v.get('p95_ms', '-')} |"
        for name, v in snapshot["stages"].items()
    )
    if snapshot.get("peak_consumer_lag") is not None:
        lines += ["", f"Peak Kafka consumer lag: {snapshot['peak_consumer_lag']} messages."]
    return [*lines, ""]


DEFAULT_CONFIG = (
    "local MiniLM embeddings (ONNX, CPU) with a 10k-entry embedding LRU cache, BM25 + "
    "pgvector hybrid retrieval, built-in extractive answerer (no LLM), 1 API process, "
    "2 ingestion workers, rate limit raised for the test, 5% trace sampling."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()
    run = Path(args.run)
    environment = {
        "commit": git_commit(),
        "cpu": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_gb": memory_gb(),
        "os": platform.platform(),
        "note": "API, 2 workers, Postgres, Redis, Redpanda, MinIO, Prometheus, Grafana, Jaeger "
        "and Locust all share this one machine.",
    }
    (run / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    lines = [
        f"# Load test — {run.name}",
        "",
        f"Commit `{environment['commit']}` · {environment['logical_cpus']} vCPU "
        f"({environment['cpu']}) · {environment['memory_gb']} GB RAM · {environment['note']}",
        "",
        f"Configuration: {args.config}",
        "",
    ]
    query = [r for r in rows(run / "query_stats.csv") if r["Name"] != "Aggregated"]
    if query:
        lines += [
            "## Query API — 32 concurrent users, 2 minutes",
            "",
            "`query:miss` defeats the answer cache (full retrieval + answer); `query:hit` is "
            "served from Redis; `stream:*` time the SSE endpoint to its first token and to "
            "the final event.",
            "",
            *table(query),
            "",
            *stages(run / "query_stages.json", "Where the time goes"),
        ]
    single = [r for r in rows(run / "query_single_user_stats.csv") if r["Name"] != "Aggregated"]
    if single:
        lines += ["## Query API — 1 user (unloaded latency), 1 minute", "", *table(single), ""]
    ingest = [r for r in rows(run / "ingest_stats.csv") if r["Name"] != "Aggregated"]
    throughput_path = run / "ingest_throughput.json"
    if ingest or throughput_path.exists():
        lines += ["## Ingestion — 8 uploaders for 1 minute, then drain", ""]
        if throughput_path.exists():
            t = json.loads(throughput_path.read_text())
            lines += [
                f"**{t['docs_per_minute']} documents/minute** end to end "
                f"(upload → parsed, chunked, embedded, indexed): "
                f"{t['documents_ready']} documents ({t['by_kind']}), "
                f"{t['chunks']} chunks in {t['wall_seconds']} s; {t['documents_failed']} failed. "
                f"Upload → ready latency p50 {t['upload_to_ready_seconds_p50']} s, "
                f"p95 {t['upload_to_ready_seconds_p95']} s.",
                "",
            ]
        lines += [*table(ingest), "", *stages(run / "ingest_stages.json", "Per-document stages")]
    (run / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
