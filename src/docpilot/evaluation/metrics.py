"""Retrieval and answer-quality metrics.

* **hit-rate@k** — share of answerable questions with a relevant chunk in the top k.
* **MRR@k** — mean of 1/rank of the first relevant chunk (0 if none in the top k); rewards
  ranking the evidence first, not just somewhere in the context window.
* **fact recall** — the answer contains every expected fact (deterministic, no judge).
* **refusal accuracy** — unanswerable questions are refused, answerable ones are not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def normalize(text: str) -> str:
    return " ".join(text.casefold().replace("\u2019", "'").split())


def is_relevant(text: str, evidence: Sequence[str]) -> bool:
    haystack = normalize(text)
    return any(normalize(e) in haystack for e in evidence)


def first_relevant_rank(texts: Sequence[str], evidence: Sequence[str]) -> int | None:
    for rank, text in enumerate(texts, start=1):
        if is_relevant(text, evidence):
            return rank
    return None


def hit_rate_at_k(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mean_reciprocal_rank(ranks: Sequence[int | None], k: int = 10) -> float:
    if not ranks:
        return 0.0
    return sum(1 / r for r in ranks if r is not None and r <= k) / len(ranks)


def fact_recall(answer: str, facts: Sequence[str]) -> bool:
    haystack = normalize(answer)
    return all(normalize(f) in haystack for f in facts)


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(q / 100 * len(ordered)) - 1)
    return ordered[index]
