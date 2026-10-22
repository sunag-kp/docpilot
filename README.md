# DocPilot

[![CI](https://github.com/sunag-kp/docpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/sunag-kp/docpilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)

**Multi-tenant document Q&A: upload PDFs, DOCX and Markdown, ask questions, get streamed answers with citations to the exact source passages.**

> Work in progress — built milestone by milestone. See the commit history.

## Quick start

Requires Docker. No accounts or API keys needed.

```bash
make up          # builds and starts the API, worker and all infrastructure
open http://localhost:8000/docs
make down        # stop and remove volumes
```

## Development

```bash
make install     # uv sync (Python 3.12)
make lint        # ruff, ruff format --check, mypy --strict, import-linter
make test        # unit + integration tests against the docker-compose infra
make fmt         # auto-format
```

## License

[MIT](LICENSE)
