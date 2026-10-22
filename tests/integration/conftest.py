"""Integration tests run against real Postgres/Redis/Redpanda/MinIO.

Locally: `make test` starts the docker-compose infrastructure first. If it is not
reachable, integration tests are skipped with a hint — except in CI (CI=true), where an
unreachable dependency must fail the build instead of silently skipping.

Tests use their own database (``<name>_test``) on the same server, so a running dev stack
never sees test rows (its stale-document sweeper would otherwise re-publish them).
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from docpilot.config import EmbeddingProvider, Environment, Settings
from docpilot.infra.db.engine import create_engine
from docpilot.infra.db.repositories import SqlUnitOfWork
from docpilot.services.ports import UnitOfWork

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = uuid4().hex[:8]  # isolates Kafka topics / groups / buckets per test session
_BASE_URL = Settings(_env_file=None).database_url  # type: ignore[call-arg]
TEST_DATABASE_URL = make_url(_BASE_URL).set(database=f"{make_url(_BASE_URL).database}_test")


def integration_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": Environment.TEST,
        "log_json": False,
        "log_level": "WARNING",
        "embedding_provider": EmbeddingProvider.HASHING,
        "kafka_topic_ingest": f"test.{RUN_ID}.documents.uploaded",
        "kafka_topic_dlq": f"test.{RUN_ID}.documents.uploaded.dlq",
        "kafka_consumer_group": f"test-{RUN_ID}",
        "kafka_topic_partitions": 3,
        "s3_bucket": f"test-{RUN_ID}",
        "database_url": TEST_DATABASE_URL.render_as_string(hide_password=False),
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
def settings(migrated: None) -> Settings:
    return integration_settings()


async def _create_test_database() -> None:
    admin = create_async_engine(_BASE_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE_URL.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_URL.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
async def migrated() -> None:
    await _create_test_database()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url",
        TEST_DATABASE_URL.render_as_string(hide_password=False).replace("%", "%%"),
    )
    # Alembic's env.py calls asyncio.run(); give it a thread with no running loop.
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def engine(migrated: None, settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    yield engine
    await engine.dispose()


@pytest.fixture
def uow_factory(engine: AsyncEngine, settings: Settings) -> Callable[[], UnitOfWork]:
    return lambda: SqlUnitOfWork(engine, settings.embedding_dim)
