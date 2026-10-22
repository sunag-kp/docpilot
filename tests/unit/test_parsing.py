from __future__ import annotations

import pytest

from docpilot.domain.errors import PermanentIngestionError
from docpilot.domain.models import DocumentKind
from docpilot.infra.parsing import DefaultDocumentParser, parse_markdown, parse_text
from tests.samples import make_docx, make_pdf

parser = DefaultDocumentParser()


def test_markdown_headings_lists_links_and_front_matter() -> None:
    md = """---
title: ignored
---
# Handbook

Intro with a [link](https://example.com) and **bold** and `code`.

## Leave
- Vacation: 20 days
- Sick days: 10

Setext Heading
--------------

```
fenced code stays
```

| a | b |
|---|---|
| 1 | 2 |
"""
    blocks = parse_markdown(md).blocks
    assert [(b.text, b.heading_level) for b in blocks if b.is_heading] == [
        ("Handbook", 1),
        ("Leave", 2),
        ("Setext Heading", 2),
    ]
    body = [b.text for b in blocks if not b.is_heading]
    assert body[0] == "Intro with a link and bold and code."
    assert body[1] == "Vacation: 20 days. Sick days: 10."
    assert "fenced code stays" in body[2]
    assert body[3] == "a | b. 1 | 2."
    assert "title: ignored" not in " ".join(body)


def test_plain_text_paragraphs() -> None:
    blocks = parse_text("First para\nwraps here.\n\n\nSecond para.").blocks
    assert [b.text for b in blocks] == ["First para wraps here.", "Second para."]


def test_pdf_keeps_page_numbers_and_detects_headings() -> None:
    data = make_pdf(
        [
            ["LEAVE POLICY", "Employees accrue 1.5 days of vacation per month of service."],
            ["2.1 Sick Leave", "Ten sick days are granted every calendar year."],
        ]
    )
    parsed = parser.parse(data, DocumentKind.PDF)
    assert parsed.page_count == 2
    headings = [(b.text, b.heading_level, b.page) for b in parsed.blocks if b.is_heading]
    assert headings == [("LEAVE POLICY", 1, 1), ("2.1 Sick Leave", 2, 2)]
    body = [b for b in parsed.blocks if not b.is_heading]
    assert body[0].page == 1
    assert "1.5 days" in body[0].text
    assert body[-1].page == 2


def test_docx_headings_paragraphs_and_tables() -> None:
    data = make_docx(
        [("Security Policy", 1), ("Passwords", 2), ("Rotate passwords every 90 days.", 0)],
        table=[["Role", "MFA"], ["Admin", "Required"]],
    )
    blocks = parser.parse(data, DocumentKind.DOCX).blocks
    assert [(b.text, b.heading_level) for b in blocks] == [
        ("Security Policy", 1),
        ("Passwords", 2),
        ("Rotate passwords every 90 days.", 0),
        ("Role | MFA", 0),
        ("Admin | Required", 0),
    ]


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"%PDF-1.4 truncated garbage", DocumentKind.PDF),
        (b"PK\x03\x04 not a real zip", DocumentKind.DOCX),
    ],
)
def test_corrupt_files_fail_permanently(data: bytes, kind: DocumentKind) -> None:
    with pytest.raises(PermanentIngestionError):
        parser.parse(data, kind)


def test_document_without_body_text_fails_permanently() -> None:
    with pytest.raises(PermanentIngestionError, match="no extractable text"):
        parser.parse(b"# Only a heading\n", DocumentKind.MARKDOWN)
