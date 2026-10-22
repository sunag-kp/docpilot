from __future__ import annotations

from scripts.export_openapi import OUTPUT, render


def test_committed_openapi_schema_is_current() -> None:
    assert OUTPUT.read_text() == render(), (
        "docs/openapi.json is stale: run `uv run python scripts/export_openapi.py`"
    )
