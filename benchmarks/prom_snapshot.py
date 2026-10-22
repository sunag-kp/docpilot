"""Save per-stage latency percentiles from Prometheus right after a load-test run.

    uv run python benchmarks/prom_snapshot.py <query|ingest> <out.json> [--window 5m]

Locust sees end-to-end latency; this records *where* the time went (embedding, search,
generation, parse, store...) from the services' own histograms.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import httpx

PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
METRICS = {
    "query": "docpilot_query_stage_duration_seconds_bucket",
    "ingest": "docpilot_ingest_stage_duration_seconds_bucket",
}


def query(expr: str) -> list[dict[str, object]]:
    response = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": expr}, timeout=10)
    response.raise_for_status()
    result: list[dict[str, object]] = response.json()["data"]["result"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=sorted(METRICS))
    parser.add_argument("out")
    parser.add_argument("--window", default="5m")
    args = parser.parse_args()
    metric = METRICS[args.kind]
    stages: dict[str, dict[str, float]] = {}
    for q in (0.5, 0.95):
        expr = f"histogram_quantile({q}, sum by (le, stage) (rate({metric}[{args.window}])))"
        for row in query(expr):
            value = float(row["value"][1])  # type: ignore[index]
            if not math.isnan(value):  # NaN = the stage had no samples in the window
                stage = str(row["metric"]["stage"])  # type: ignore[index]
                stages.setdefault(stage, {})[f"p{int(q * 100)}_ms"] = round(value * 1000, 1)
    snapshot: dict[str, object] = {"window": args.window, "stages": stages}
    if args.kind == "ingest":
        lag = query(f"max_over_time(sum(docpilot_consumer_lag_messages)[{args.window}:5s])")
        snapshot["peak_consumer_lag"] = int(float(lag[0]["value"][1])) if lag else None  # type: ignore[index]
    Path(args.out).write_text(json.dumps(snapshot, indent=2) + "\n")
    print(json.dumps(snapshot))


if __name__ == "__main__":
    main()
