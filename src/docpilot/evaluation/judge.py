"""LLM-as-judge for answer faithfulness.

The harness splits the answer into sentences and numbers them; the judge only labels each
sentence SUPPORTED or UNSUPPORTED by the sources. Faithfulness = supported / labelled
sentences. Letting a small local model also *extract* the claims proved unreliable in CI:
it graded the same claim both ways and invented fragments ("UNSUPPORTED: Yes"). With the
claim list fixed by the harness, the judge's job is one yes/no per numbered sentence, in a
line-based format small models follow reliably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from docpilot.domain.answers import Citation, GenerationRequest
from docpilot.domain.text import split_sentences
from docpilot.services.ports import LLM

JUDGE_SYSTEM = """You are a strict fact-checking grader. You receive numbered source \
passages and the numbered SENTENCES of an answer that was written from them.

For each sentence, decide whether the sources explicitly state what it says. A sentence \
that adds anything the sources do not state (a number, a condition, a consequence) is \
UNSUPPORTED, even if it is plausible.

Reply with one line per sentence, in order: the sentence number, a colon, then the word \
SUPPORTED or the word UNSUPPORTED. Output nothing else."""

_CITATION = re.compile(r"\s*\[\d{1,2}\](?:\s*\[\d{1,2}\])*")
_NUMBERED = re.compile(r"^\W*(?:sentence\s*)?(\d+)\b(.*)$", re.IGNORECASE)
_LABEL = re.compile(r"\b(UNSUPPORTED|SUPPORTED)\b")  # anywhere after the number, upper case
_LEADING_LABEL = re.compile(r"^\W*(unsupported|supported)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Verdict:
    supported: int
    unsupported: int
    raw: str

    @property
    def total(self) -> int:
        return self.supported + self.unsupported

    @property
    def faithfulness(self) -> float | None:
        return self.supported / self.total if self.total else None


def answer_sentences(answer: str) -> list[str]:
    """The sentences to grade: citation markers removed, empty pieces dropped."""
    return [s for s in split_sentences(_CITATION.sub("", answer)) if any(c.isalnum() for c in s)]


def parse_verdict(raw: str, sentence_count: int) -> Verdict:
    """Read "N: LABEL" lines; the first label for each sentence number counts.

    Tolerates bold, "Sentence N -", the sentence echoed before its label and a reason after
    it. Numbers outside 1..sentence_count are ignored, as are repeated labels.
    """
    labels: dict[int, bool] = {}
    for line in raw.splitlines():
        numbered = _NUMBERED.match(line)
        if numbered is None:
            continue
        number, rest = int(numbered.group(1)), numbered.group(2)
        if not 1 <= number <= sentence_count or number in labels:
            continue
        found = _LABEL.search(rest) or _LEADING_LABEL.match(rest)
        if found is not None:
            labels[number] = found.group(1).upper() == "SUPPORTED"
    supported = sum(labels.values())
    return Verdict(supported=supported, unsupported=len(labels) - supported, raw=raw)


class FaithfulnessJudge:
    def __init__(self, llm: LLM, *, max_tokens: int = 200) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return self._llm.name

    async def judge(self, question: str, answer: str, sources: list[Citation]) -> Verdict:
        sentences = answer_sentences(answer)
        if not sentences:
            return Verdict(supported=0, unsupported=0, raw="")
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(sentences, 1))
        task = f"The user asked: {question}\n\nSENTENCES to grade:\n{numbered}"
        request = GenerationRequest(
            question=task,
            sources=sources,
            system=JUDGE_SYSTEM,
            max_tokens=self._max_tokens,
            temperature=0.0,
        )
        raw = "".join([piece async for piece in self._llm.stream(request)])
        return parse_verdict(raw, len(sentences))
