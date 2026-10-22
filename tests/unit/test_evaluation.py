from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from docpilot.domain.answers import Citation, GenerationRequest
from docpilot.evaluation.dataset import load_dataset
from docpilot.evaluation.judge import FaithfulnessJudge, answer_sentences, parse_verdict
from docpilot.evaluation.metrics import (
    fact_recall,
    first_relevant_rank,
    hit_rate_at_k,
    is_relevant,
    mean_reciprocal_rank,
    percentile,
)

ROOT = Path(__file__).resolve().parents[2]


def test_relevance_and_rank_are_whitespace_and_case_insensitive() -> None:
    texts = ["nothing here", "Full-time staff accrue 1.75 days of paid\ntime off", "other"]
    assert is_relevant(texts[1], ["1.75 DAYS of paid time off"])
    assert first_relevant_rank(texts, ["1.75 days of paid time off"]) == 2
    assert first_relevant_rank(texts, ["absent"]) is None


def test_hit_rate_and_mrr() -> None:
    ranks = [1, 3, None, 2]
    assert hit_rate_at_k(ranks, 1) == 0.25
    assert hit_rate_at_k(ranks, 3) == 0.75
    assert mean_reciprocal_rank(ranks, 10) == pytest.approx((1 + 1 / 3 + 0 + 1 / 2) / 4)
    assert mean_reciprocal_rank(ranks, 2) == pytest.approx((1 + 1 / 2) / 4)
    assert hit_rate_at_k([], 5) == mean_reciprocal_rank([]) == 0.0


def test_fact_recall_requires_every_fact() -> None:
    assert fact_recall("Retried 8 times, signed with HMAC-SHA256 [1].", ["8 times", "hmac-sha256"])
    assert not fact_recall("Retried 8 times [1].", ["8 times", "HMAC-SHA256"])


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([], 50) == 0.0


def test_repository_dataset_is_well_formed() -> None:
    items = load_dataset(ROOT / "eval" / "qa.jsonl")
    assert len(items) >= 50
    unanswerable = [i for i in items if not i.answerable]
    assert len(unanswerable) >= 5
    assert all(i.evidence and i.facts for i in items if i.answerable)


def test_dataset_rejects_unlabelled_items(tmp_path: Path) -> None:
    bad = tmp_path / "qa.jsonl"
    bad.write_text(json.dumps({"id": "x", "question": "q", "category": "c", "source": "a.md"}))
    with pytest.raises(ValueError, match="needs evidence"):
        load_dataset(bad)


def test_parse_verdict_reads_numbered_labels_and_tolerates_formatting() -> None:
    raw = """Here are the labels:
**1:** SUPPORTED
Sentence 2 - Unsupported (the sources say April)
3. Backups are kept for 35 days. SUPPORTED
3: UNSUPPORTED
7: SUPPORTED
Some trailing chatter about what is SUPPORTED."""
    verdict = parse_verdict(raw, sentence_count=4)
    # 1 and 3 supported, 2 unsupported; the repeated 3, the out-of-range 7 and sentence 4
    # (never labelled) don't count.
    assert (verdict.supported, verdict.unsupported) == (2, 1)
    assert verdict.faithfulness == pytest.approx(2 / 3)
    assert parse_verdict("no structured output", sentence_count=2).faithfulness is None


def test_answer_sentences_drop_citation_markers() -> None:
    answer = "Carry over up to 8 days [2]. They expire on March 31. [2] [3]"
    assert answer_sentences(answer) == ["Carry over up to 8 days.", "They expire on March 31."]


class ScriptedLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[GenerationRequest] = []

    @property
    def name(self) -> str:
        return "scripted"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        self.requests.append(request)
        yield self.reply


async def test_judge_sends_sources_question_and_numbered_sentences() -> None:
    llm = ScriptedLLM("1: SUPPORTED\n2: UNSUPPORTED")
    source = Citation(1, uuid4(), uuid4(), "h.md", "Employees accrue 1.75 days.")
    verdict = await FaithfulnessJudge(llm).judge(
        "How much PTO?", "Employees accrue 1.75 days [1]. It doubles after a year [1].", [source]
    )
    assert verdict.faithfulness == 0.5
    message = llm.requests[0].render_user_message()
    assert "Employees accrue 1.75 days." in message
    assert "1. Employees accrue 1.75 days.\n2. It doubles after a year." in message
    assert llm.requests[0].system.startswith("You are a strict fact-checking grader")


async def test_judge_skips_answers_without_sentences() -> None:
    llm = ScriptedLLM("1: SUPPORTED")
    verdict = await FaithfulnessJudge(llm).judge("Q?", "[1]", [])
    assert verdict.faithfulness is None
    assert llm.requests == []
