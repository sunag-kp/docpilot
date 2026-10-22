from __future__ import annotations

import pytest

from docpilot.domain.text import (
    content_terms,
    cosine,
    estimate_tokens,
    normalize_question,
    split_sentences,
    stem,
    term_vector,
)


def test_split_sentences_handles_abbreviations_and_decimals() -> None:
    text = "Employees accrue 1.5 days per month, e.g. 18 days. Requests go to HR! Is that clear?"
    assert split_sentences(text) == [
        "Employees accrue 1.5 days per month, e.g. 18 days.",
        "Requests go to HR!",
        "Is that clear?",
    ]


def test_split_sentences_normalises_whitespace_and_empty() -> None:
    assert split_sentences("  One.\n\n  Two.  ") == ["One.", "Two."]
    assert split_sentences("   ") == []


def test_estimate_tokens_counts_words_and_punctuation() -> None:
    assert estimate_tokens("Hello, world!") == 4
    assert estimate_tokens("") == 0


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("policies", "policy"),
        ("running", "runn"),
        ("approved", "approv"),
        ("days", "day"),
        ("is", "is"),
    ],
)
def test_stem_strips_common_suffixes(word: str, expected: str) -> None:
    assert stem(word) == expected


def test_content_terms_drop_stopwords() -> None:
    assert content_terms("What is the vacation policy?") == ["vacation", "policy"]


def test_cosine_similarity_bounds() -> None:
    a = term_vector(["vacation days accrue monthly"])
    b = term_vector(["monthly vacation accrual of days"])
    c = term_vector(["kitchen cleaning schedule"])
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, b) > cosine(a, c) == 0.0
    assert cosine(a, term_vector([])) == 0.0


def test_normalize_question_ignores_case_punctuation_and_spacing() -> None:
    assert normalize_question("  What's the   VACATION policy?? ") == normalize_question(
        "what's the vacation policy"
    )
