"""Structure-, topic- and size-aware chunking.

Retrieval quality depends more on *where* chunks are cut than on almost anything else, so
the chunker cuts in this order of preference:

1. **Section boundaries** — a heading always starts a new chunk, so every chunk belongs to
   exactly one section and its citation label is never wrong. Short sections stay small
   but keep their context: the section path is prepended to the text that gets embedded.
2. **Topic shifts** — between paragraphs we measure lexical cohesion (cosine similarity of
   content-word vectors over a few sentences either side, as in TextTiling, Hearst 1997).
   A drop below ``topic_shift_threshold`` ends the chunk once it has ``min_tokens``. It
   needs no model call, so it is cheap and deterministic.
3. **Size** — a chunk never exceeds ``max_tokens``; it is cut at a sentence boundary and
   the next chunk repeats the last ``overlap_sentences`` sentences for context.

Sentences are never split unless a single sentence is longer than ``max_tokens``.
"""

from __future__ import annotations

from dataclasses import dataclass

from docpilot.domain.models import Block, ChunkDraft, ParsedDocument
from docpilot.domain.text import cosine, estimate_tokens, split_sentences, term_vector


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    max_tokens: int = 320
    min_tokens: int = 80
    overlap_sentences: int = 1
    topic_shift_threshold: float = 0.08
    cohesion_window: int = 2

    def __post_init__(self) -> None:
        if not 0 < self.min_tokens < self.max_tokens:
            raise ValueError("require 0 < min_tokens < max_tokens")


@dataclass(slots=True, eq=False)
class _Unit:
    text: str
    tokens: int
    page: int | None
    section: str | None
    starts_block: bool = False
    starts_section: bool = False
    topic_shift: bool = False


class Chunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]:
        units = self._to_units(document.blocks)
        self._mark_topic_shifts(units)
        groups = self._group(units)
        return [self._draft(i, group) for i, group in enumerate(groups)]

    # -- 1. flatten blocks into sentence units carrying page/section context ----------------
    def _to_units(self, blocks: list[Block]) -> list[_Unit]:
        units: list[_Unit] = []
        headings: list[tuple[int, str]] = []
        pending_section_break = False
        for block in blocks:
            if block.is_heading:
                headings = [h for h in headings if h[0] < block.heading_level]
                headings.append((block.heading_level, " ".join(block.text.split())))
                pending_section_break = True
                continue
            section = " > ".join(text for _, text in headings) or None
            for position, sentence in enumerate(split_sentences(block.text)):
                for piece in self._split_oversized(sentence):
                    units.append(
                        _Unit(
                            text=piece,
                            tokens=estimate_tokens(piece),
                            page=block.page,
                            section=section,
                            starts_block=position == 0,
                            starts_section=pending_section_break,
                        )
                    )
                    pending_section_break = False
        return units

    def _split_oversized(self, sentence: str) -> list[str]:
        if estimate_tokens(sentence) <= self.config.max_tokens:
            return [sentence]
        pieces: list[str] = []
        current: list[str] = []
        for word in sentence.split():
            if current and estimate_tokens(" ".join([*current, word])) > self.config.max_tokens:
                pieces.append(" ".join(current))
                current = []
            current.append(word)
        if current:
            pieces.append(" ".join(current))
        return pieces

    # -- 2. TextTiling-style lexical cohesion at paragraph boundaries -----------------------
    def _mark_topic_shifts(self, units: list[_Unit]) -> None:
        window = self.config.cohesion_window
        for i, unit in enumerate(units):
            if i == 0 or not unit.starts_block or unit.starts_section:
                continue
            left = term_vector(u.text for u in units[max(0, i - window) : i])
            right = term_vector(u.text for u in units[i : i + window])
            unit.topic_shift = cosine(left, right) < self.config.topic_shift_threshold

    # -- 3. greedy packing under the size budget ------------------------------------------
    def _group(self, units: list[_Unit]) -> list[list[_Unit]]:
        cfg = self.config
        groups: list[list[_Unit]] = []
        current: list[_Unit] = []
        tokens = 0
        carried = 0  # leading units of `current` repeated from the previous chunk
        for unit in units:
            if current:
                topic_break = unit.topic_shift and tokens >= cfg.min_tokens
                if unit.starts_section or topic_break:
                    groups.append(current)
                    current, tokens, carried = [], 0, 0
                elif tokens + unit.tokens > cfg.max_tokens:
                    groups.append(current)
                    current = self._overlap(current, unit)
                    tokens, carried = sum(u.tokens for u in current), len(current)
            current.append(unit)
            tokens += unit.tokens
        if current:
            fresh = current[carried:]
            fresh_tokens = sum(u.tokens for u in fresh)
            # Fold a tiny trailing chunk into its predecessor (same section) when it fits.
            if (
                groups
                and fresh
                and fresh_tokens < cfg.min_tokens
                and groups[-1][-1].section == fresh[0].section
                and not fresh[0].starts_section
                and sum(u.tokens for u in groups[-1]) + fresh_tokens <= cfg.max_tokens
            ):
                groups[-1].extend(fresh)
            else:
                groups.append(current)
        return groups

    def _overlap(self, previous: list[_Unit], incoming: _Unit) -> list[_Unit]:
        n = self.config.overlap_sentences
        if n == 0:
            return []
        tail = [u for u in previous[-n:] if u.section == incoming.section]
        if sum(u.tokens for u in tail) + incoming.tokens > self.config.max_tokens:
            return []
        return [
            _Unit(u.text, u.tokens, u.page, u.section, starts_block=False, starts_section=False)
            for u in tail
        ]

    @staticmethod
    def _draft(index: int, group: list[_Unit]) -> ChunkDraft:
        parts: list[str] = []
        for i, unit in enumerate(group):
            if i > 0:
                parts.append("\n\n" if unit.starts_block else " ")
            parts.append(unit.text)
        text = "".join(parts)
        pages = [u.page for u in group if u.page is not None]
        return ChunkDraft(
            index=index,
            text=text,
            token_count=estimate_tokens(text),
            section=group[0].section,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
        )
