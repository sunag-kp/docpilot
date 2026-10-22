"""Process entrypoint: ``docpilot-api`` / ``python -m docpilot.api.main``."""

from __future__ import annotations

import os

import uvicorn

from docpilot.api.app import create_app

app = create_app()


def run() -> None:
    uvicorn.run(
        "docpilot.api.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - container entrypoint
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
    )


if __name__ == "__main__":
    run()
