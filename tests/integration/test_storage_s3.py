from __future__ import annotations

import pytest

from docpilot.config import Settings
from docpilot.domain.errors import PermanentIngestionError
from docpilot.infra.storage.s3 import S3ObjectStorage


@pytest.fixture
async def s3(settings: Settings) -> S3ObjectStorage:
    storage = S3ObjectStorage(
        settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key_id="minioadmin",
        secret_access_key="minioadmin",
    )
    await storage.ensure_bucket()
    await storage.ensure_bucket()  # idempotent
    return storage


async def test_put_get_delete_round_trip(s3: S3ObjectStorage) -> None:
    await s3.ping()
    await s3.put("tenants/t/documents/d/source.md", b"# hello", "text/markdown")
    assert await s3.get("tenants/t/documents/d/source.md") == b"# hello"
    await s3.delete("tenants/t/documents/d/source.md")
    with pytest.raises(PermanentIngestionError, match="not found"):
        await s3.get("tenants/t/documents/d/source.md")
