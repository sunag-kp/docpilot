"""Built-in keyless answerer: extractive, grounded by construction.

It never calls a model. It picks the source sentences that best overlap the question
(IDF-weighted content terms, plus a prior from the passage's retrieval similarity) and
cites each one. Because every sentence is copied from a source and labelled with it, the
answer cannot hallucinate — but it also cannot paraphrase or reason across sources, so
real generation should use Ollama (local) or a hosted provider.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass

from docpilot.domain.answers import REFUSAL, Citation, GenerationRequest
from docpilot.domain.text import content_terms, split_sentences


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: Citation
    rank: int
    position: int
    sentence: str
    overlap: float

    @property
    def score(self) -> float:
        similarity = self.source.vector_similarity or 0.0
        return self.overlap + 0.3 * similarity + 0.1 / (self.rank + 1)


class ExtractiveLLM:
    def __init__(
        self,
        *,
        max_sentences: int = 3,
        max_per_source: int = 2,
        semantic_fallback_similarity: float = 0.45,
    ) -> None:
        self._max_sentences = max_sentences
        self._max_per_source = max_per_source
        self._semantic_fallback = semantic_fallback_similarity

    @property
    def name(self) -> str:
        return "extractive"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        answer = self.compose(request)
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else f"{word} "
            await asyncio.sleep(0)  # cooperative: lets a disconnected client cancel us

    def compose(self, request: GenerationRequest) -> str:
        question_terms = set(content_terms(request.question))
        candidates: list[tuple[Citation, int, int, str, set[str]]] = []
        for rank, source in enumerate(request.sources):
            for position, sentence in enumerate(split_sentences(source.text)):
                candidates.append((source, rank, position, sentence, set(content_terms(sentence))))
        if not candidates:
            return REFUSAL

        document_frequency: Counter[str] = Counter()
        for *_, terms in candidates:
            document_frequency.update(terms)
        total = len(candidates)

        def idf(term: str) -> float:
            return math.log(1 + total / (1 + document_frequency[term]))

        query_weight = sum(idf(t) for t in question_terms) or 1.0
        scored = [
            _Candidate(
                source=source,
                rank=rank,
                position=position,
                sentence=sentence,
                overlap=sum(idf(t) for t in question_terms & terms) / query_weight,
            )
            for source, rank, position, sentence, terms in candidates
        ]
        with_overlap = sorted((c for c in scored if c.overlap > 0), key=lambda c: -c.score)
        if with_overlap:
            best = with_overlap[0].score
            pool = [c for c in with_overlap if c.score >= 0.6 * best]
        else:
            # No shared words: only answer when retrieval is confident the top passage is a
            # paraphrase of the question (e.g. "time off" vs "vacation"); otherwise refuse.
            top = request.sources[0]
            if (top.vector_similarity or 0.0) < self._semantic_fallback:
                return REFUSAL
            pool = [c for c in scored if c.source.number == top.number][:2]

        chosen: list[_Candidate] = []
        per_source: Counter[int] = Counter()
        for candidate in pool:
            if per_source[candidate.source.number] >= self._max_per_source:
                continue
            chosen.append(candidate)
            per_source[candidate.source.number] += 1
            if len(chosen) == self._max_sentences:
                break
        chosen.sort(key=lambda c: (c.rank, c.position))  # read in document order
        return " ".join(f"{_strip_end(c.sentence)} [{c.source.number}]." for c in chosen)


def _strip_end(sentence: str) -> str:
    return sentence.rstrip().rstrip(".")
