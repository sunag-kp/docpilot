"""The labeled Q&A set (eval/qa.jsonl)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvalItem:
    id: str
    question: str
    category: str
    source: str | None
    evidence: list[str] = field(default_factory=list)  # a chunk is relevant if it contains one
    facts: list[str] = field(default_factory=list)  # a correct answer contains all of them

    @property
    def answerable(self) -> bool:
        return self.source is not None


def load_dataset(path: str | Path) -> list[EvalItem]:
    items = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        item = EvalItem(
            id=row["id"],
            question=row["question"],
            category=row["category"],
            source=row.get("source"),
            evidence=list(row.get("evidence", [])),
            facts=list(row.get("facts", [])),
        )
        if item.answerable and not (item.evidence and item.facts):
            raise ValueError(f"line {number}: answerable item {item.id!r} needs evidence and facts")
        items.append(item)
    if len({i.id for i in items}) != len(items):
        raise ValueError("duplicate item ids in dataset")
    return items
