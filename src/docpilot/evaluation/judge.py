"""LLM-as-judge for answer faithfulness.

The judge breaks the answer into claims and labels each one SUPPORTED or UNSUPPORTED by
the sources. Faithfulness = supported / total claims. A line-based protocol (instead of
JSON) keeps small local models — the zero-cost default judge runs on Ollama — reliable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from docpilot.domain.answers import Citation, GenerationRequest
from docpilot.services.ports import LLM

JUDGE_SYSTEM = """You are a strict fact-checking grader. You receive numbered source \
passages and an ANSWER that was written from them.

List every factual claim the ANSWER itself makes (not facts that appear only in the \
sources), one per line, in exactly this format:
SUPPORTED: <claim>
UNSUPPORTED: <claim>
Use SUPPORTED if the sources explicitly state the claim and UNSUPPORTED if they do not \
(including claims that are plausible but absent from the sources). Ignore citation \
markers such as [1]. Output only these lines, with no notes."""

# Small models don't always follow the format, so a label is accepted where it opens a line
# ("1. SUPPORTED: claim", "**UNSUPPORTED** - claim", "SUPPORTED (Source 2)" under the claim)
# or closes one ("2. claim: UNSUPPORTED (source 1 says otherwise)"). Lines naming both labels
# are the model restating the instructions and are skipped.
_LEADING = re.compile(
    r"^\s*[-*\d.)\s]*\**\s*(SUPPORTED|UNSUPPORTED)\**\s*(?:[:\-(]|$)", re.IGNORECASE
)
_TRAILING = re.compile(r"\b(SUPPORTED|UNSUPPORTED)\**\s*(?:\([^)]*\)?)?\s*\.?\s*$")
_LABEL = re.compile(r"\b(?:UN)?SUPPORTED\b")


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


def parse_verdict(raw: str) -> Verdict:
    supported = unsupported = 0
    for line in raw.splitlines():
        if len(_LABEL.findall(line)) > 1:
            continue
        match = _LEADING.match(line) or _TRAILING.search(line)
        if match is None:
            continue
        if match.group(1).upper() == "SUPPORTED":
            supported += 1
        else:
            unsupported += 1
    return Verdict(supported=supported, unsupported=unsupported, raw=raw)


class FaithfulnessJudge:
    def __init__(self, llm: LLM, *, max_tokens: int = 600) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return self._llm.name

    async def judge(self, question: str, answer: str, sources: list[Citation]) -> Verdict:
        task = (
            f"The user asked: {question}\n\nANSWER to grade:\n{answer}\n\n"
            "Label each claim in the ANSWER as SUPPORTED or UNSUPPORTED by the sources."
        )
        request = GenerationRequest(
            question=task,
            sources=sources,
            system=JUDGE_SYSTEM,
            max_tokens=self._max_tokens,
            temperature=0.0,
        )
        raw = "".join([piece async for piece in self._llm.stream(request)])
        return parse_verdict(raw)
