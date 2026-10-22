"""Write the API's OpenAPI schema to docs/openapi.json (published with the Pages demo).

    uv run python scripts/export_openapi.py

A unit test fails when the committed file no longer matches the code.
"""

from __future__ import annotations

import json
from pathlib import Path

from docpilot.api.app import create_app
from docpilot.config import Settings

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def render() -> str:
    schema = create_app(Settings(_env_file=None)).openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render())
    print(f"wrote {OUTPUT}")
