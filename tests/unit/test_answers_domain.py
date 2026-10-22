from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from docpilot.domain.answers import (
    REFUSAL,
    SYSTEM_PROMPT,
    Citation,
    GenerationRequest,
    analyse_answer,
    is_refusal,
)


def citation(number: int, **kw: Any) -> Citation:
    return Citation(
        number=number,
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="a.pdf",
        text=f"text {number}",
        **kw,
    )


def test_analyse_answer_separates_valid_and_invented_citations() -> None:
    report = analyse_answer("A [1]. B [2][7]. C [1].", source_count=3)
    assert report.cited_numbers == [1, 2]
    assert report.invalid_numbers == [7]
    assert not report.grounded  # cited a source that does not exist


def test_grounded_requires_a_citation_unless_refusing() -> None:
    assert analyse_answer("Vacation is 20 days [1].", 1).grounded
    assert not analyse_answer("Vacation is 20 days.", 1).grounded
    refusal = analyse_answer(REFUSAL, 0)
    assert refusal.refused
    assert refusal.grounded


@pytest.mark.parametrize(
    "text",
    [REFUSAL, "I don\u2019t know based on the provided documents", "  i don't know.  "],
)
def test_refusal_detection(text: str) -> None:
    assert is_refusal(text)


def test_not_a_refusal() -> None:
    assert not is_refusal("I don't know the exact figure, but the policy says 20 days [1].")


def test_citation_labels() -> None:
    assert citation(1).label == "a.pdf"
    assert citation(1, section="Leave", page_start=3).label == "a.pdf — Leave — p. 3"
    assert citation(1, page_start=3, page_end=5).label == "a.pdf — pp. 3-5"


def test_prompt_delimits_sources_and_states_rules() -> None:
    request = GenerationRequest(question="Q?", sources=[citation(1), citation(2)])
    message = request.render_user_message()
    assert '<source id="1" origin="a.pdf">\ntext 1\n</source>' in message
    assert message.endswith("Question: Q?")
    assert REFUSAL in SYSTEM_PROMPT
    assert "untrusted" in SYSTEM_PROMPT
