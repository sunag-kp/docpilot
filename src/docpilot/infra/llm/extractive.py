"""Built-in keyless answerer: extractive, grounded by construction.

It never generates text. It embeds the question and every sentence of the retrieved
passages with the local embedding model, picks the sentences closest in meaning (with a
small bonus for sharing rare words), and cites each one. Every sentence is copied from a
source and labelled with it, so the answer cannot hallucinate — but it cannot paraphrase
or combine facts either; for that, use Ollama (local) or a hosted provider.

If even the best sentence is not similar enough to the question, it refuses.
"""

from __future__ import annotations

import asyncio
import math
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass

from docpilot.domain.answers import REFUSAL, Citation, GenerationRequest
from docpilot.domain.text import content_terms, split_sentences
from docpilot.services.ports import Embedder

_LEXICAL_WEIGHT = 0.15  # tie-breaker for exact identifiers ("NW-4012") embeddings blur


@dataclass(frozen=True, slots=True)
class _Candidate:
    source: Citation
    rank: int
    position: int
    sentence: str
    similarity: float
    overlap: float

    @property
    def score(self) -> float:
        return self.similarity + _LEXICAL_WEIGHT * self.overlap


class ExtractiveLLM:
    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        min_similarity: float = 0.4,
        max_sentences: int = 3,
        max_per_source: int = 2,
        margin: float = 0.12,
    ) -> None:
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._max_sentences = max_sentences
        self._max_per_source = max_per_source
        self._margin = margin

    @property
    def name(self) -> str:
        return "extractive"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        answer = await self.compose(request)
        words = answer.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else f"{word} "
            await asyncio.sleep(0)  # cooperative: lets a disconnected client cancel us

    async def compose(self, request: GenerationRequest) -> str:
        rows = [
            (source, rank, position, sentence)
            for rank, source in enumerate(request.sources)
            for position, sentence in enumerate(split_sentences(source.text))
        ]
        if not rows:
            return REFUSAL
        overlaps = _idf_overlap(request.question, [r[3] for r in rows])
        similarities = await self._similarities(request.question, [r[3] for r in rows], overlaps)
        candidates = sorted(
            (
                _Candidate(source, rank, position, sentence, similarity, overlap)
                for (source, rank, position, sentence), similarity, overlap in zip(
                    rows, similarities, overlaps, strict=True
                )
            ),
            key=lambda c: -c.score,
        )
        best = candidates[0]
        if best.similarity < self._min_similarity:
            return REFUSAL

        chosen: list[_Candidate] = []
        per_source: Counter[int] = Counter()
        for candidate in candidates:
            if candidate.score < best.score - self._margin:
                break
            if per_source[candidate.source.number] >= self._max_per_source:
                continue
            chosen.append(candidate)
            per_source[candidate.source.number] += 1
            if len(chosen) == self._max_sentences:
                break
        chosen.sort(key=lambda c: (c.rank, c.position))  # read in document order
        return " ".join(f"{c.sentence.rstrip().rstrip('.')} [{c.source.number}]." for c in chosen)

    async def _similarities(
        self, question: str, sentences: list[str], overlaps: list[float]
    ) -> list[float]:
        if self._embedder is None:  # lexical-only mode (no embedder wired, e.g. unit tests)
            return overlaps
        vectors = await self._embedder.embed_documents([question, *sentences])
        query = vectors[0]
        return [sum(a * b for a, b in zip(query, v, strict=True)) for v in vectors[1:]]


def _idf_overlap(question: str, sentences: list[str]) -> list[float]:
    """Share of the question's information (IDF mass) each sentence contains, in [0, 1]."""
    question_terms = set(content_terms(question))
    sentence_terms = [set(content_terms(s)) for s in sentences]
    document_frequency: Counter[str] = Counter()
    for terms in sentence_terms:
        document_frequency.update(terms)
    total = len(sentences)

    def idf(term: str) -> float:
        return math.log(1 + total / (1 + document_frequency[term]))

    mass = sum(idf(t) for t in question_terms) or 1.0
    return [sum(idf(t) for t in question_terms & terms) / mass for terms in sentence_terms]
