"""Integration tests run against real Postgres/Redis/Redpanda/MinIO.

Locally: `make test` starts the docker-compose infrastructure first. If it is not
reachable, integration tests are skipped with a hint — except in CI (CI=true), where an
unreachable dependency must fail the build instead of silently skipping.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest

from docpilot.config import Environment, Settings


def integration_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": Environment.TEST,
        "log_json": False,
        "log_level": "WARNING",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url if "://" in url else f"tcp://{url}")
    host, port = parsed.hostname or "localhost", parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = os.path.dirname(__file__)
    integration_items = [item for item in items if str(item.fspath).startswith(here)]
    for item in integration_items:
        item.add_marker(pytest.mark.integration)
    if not integration_items or os.environ.get("CI") == "true":
        return
    settings = integration_settings()
    if not (_reachable(settings.database_url, 5432) and _reachable(settings.redis_url, 6379)):
        skip = pytest.mark.skip(reason="infrastructure not running — start it with `make infra`")
        for item in integration_items:
            item.add_marker(skip)


@pytest.fixture
def settings() -> Settings:
    return integration_settings()
