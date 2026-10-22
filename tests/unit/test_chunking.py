from __future__ import annotations

import itertools

import pytest

from docpilot.domain.chunking import Chunker, ChunkingConfig
from docpilot.domain.models import Block, ParsedDocument
from docpilot.domain.text import estimate_tokens, split_sentences

VACATION = (
    "Full-time employees accrue 1.5 days of paid vacation per month. Vacation requests must "
    "be approved by your manager two weeks in advance. Unused vacation of up to 10 days "
    "carries over into the next year."
)
SICK = (
    "Employees receive 10 sick days per year. A doctor's note is required for absences "
    "longer than three consecutive days."
)
KITCHEN = "The office kitchen is cleaned every Friday. Please label food in the refrigerator."


def doc(*blocks: Block) -> ParsedDocument:
    return ParsedDocument(blocks=list(blocks))


def test_headings_always_start_a_new_chunk_with_section_path() -> None:
    chunks = Chunker().chunk(
        doc(
            Block("Handbook", heading_level=1),
            Block("Vacation", heading_level=2),
            Block(VACATION),
            Block("Sick leave", heading_level=2),
            Block(SICK),
        )
    )
    assert [c.section for c in chunks] == ["Handbook > Vacation", "Handbook > Sick leave"]
    assert chunks[1].text == SICK
    assert chunks[0].embedding_text.startswith("Handbook > Vacation\n")


def test_topic_shift_between_paragraphs_splits_once_min_size_reached() -> None:
    cfg = ChunkingConfig(max_tokens=200, min_tokens=10)
    chunks = Chunker(cfg).chunk(doc(Block(SICK), Block(KITCHEN)))
    assert [c.text for c in chunks] == [SICK, KITCHEN]


def test_cohesive_paragraphs_stay_together() -> None:
    cfg = ChunkingConfig(max_tokens=200, min_tokens=10)
    chunks = Chunker(cfg).chunk(
        doc(Block(VACATION), Block("Vacation balances above 10 days are forfeited."))
    )
    assert len(chunks) == 1
    assert "\n\n" in chunks[0].text  # paragraph break preserved inside the chunk


def test_small_chunk_does_not_split_on_topic_shift() -> None:
    cfg = ChunkingConfig(max_tokens=200, min_tokens=100)
    chunks = Chunker(cfg).chunk(doc(Block(SICK), Block(KITCHEN)))
    assert len(chunks) == 1


def test_size_limit_respected_with_sentence_overlap() -> None:
    sentences = [f"Sentence number {i} talks about vacation accrual rules." for i in range(30)]
    cfg = ChunkingConfig(max_tokens=40, min_tokens=10, overlap_sentences=1)
    chunks = Chunker(cfg).chunk(doc(Block(" ".join(sentences))))
    assert len(chunks) > 3
    assert all(c.token_count <= 40 for c in chunks)
    for previous, current in itertools.pairwise(chunks):
        last_sentence = split_sentences(previous.text)[-1]
        assert current.text.startswith(last_sentence)  # one-sentence overlap
    # Nothing is lost: every sentence lands in some chunk.
    joined = " ".join(c.text for c in chunks)
    assert all(s in joined for s in sentences)


def test_no_overlap_when_disabled() -> None:
    sentences = [f"Sentence {i} is about vacation." for i in range(20)]
    cfg = ChunkingConfig(max_tokens=30, min_tokens=5, overlap_sentences=0)
    chunks = Chunker(cfg).chunk(doc(Block(" ".join(sentences))))
    texts = " ".join(c.text for c in chunks)
    assert all(texts.count(s) == 1 for s in sentences)


def test_oversized_sentence_is_split_by_words() -> None:
    long_sentence = " ".join(["word"] * 100) + "."
    cfg = ChunkingConfig(max_tokens=30, min_tokens=5)
    chunks = Chunker(cfg).chunk(doc(Block(long_sentence)))
    assert len(chunks) >= 4
    assert all(estimate_tokens(c.text) <= 30 for c in chunks)


def test_tiny_trailing_chunk_merges_into_previous_same_section() -> None:
    cfg = ChunkingConfig(max_tokens=50, min_tokens=10, overlap_sentences=0)
    text = VACATION + " Thanks."
    chunks = Chunker(cfg).chunk(doc(Block(text)))
    assert chunks[-1].text.endswith("Thanks.")
    assert all(c.token_count >= 10 for c in chunks)


def test_page_ranges_follow_blocks() -> None:
    cfg = ChunkingConfig(max_tokens=200, min_tokens=5)
    chunks = Chunker(cfg).chunk(
        doc(Block(VACATION, page=3), Block("Vacation also accrues during leave.", page=4))
    )
    assert (chunks[0].page_start, chunks[0].page_end) == (3, 4)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_document_with_only_headings_yields_no_chunks() -> None:
    assert Chunker().chunk(doc(Block("Title", heading_level=1))) == []


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError, match="min_tokens"):
        ChunkingConfig(max_tokens=10, min_tokens=10)
