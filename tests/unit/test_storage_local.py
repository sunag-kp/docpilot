from __future__ import annotations

from pathlib import Path

import pytest

from docpilot.domain.errors import PermanentIngestionError
from docpilot.infra.storage.local import LocalObjectStorage


async def test_local_storage_round_trip_and_traversal_guard(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "blobs")
    await storage.put("tenants/t/doc/source.md", b"# hi", "text/markdown")
    assert await storage.get("tenants/t/doc/source.md") == b"# hi"
    assert not list((tmp_path / "blobs").rglob("*.tmp"))  # atomic rename left no temp file
    await storage.delete("tenants/t/doc/source.md")
    await storage.delete("tenants/t/doc/source.md")  # idempotent
    with pytest.raises(PermanentIngestionError):
        await storage.get("tenants/t/doc/source.md")
    with pytest.raises(ValueError, match="invalid storage key"):
        await storage.put("../../escape.txt", b"x", "text/plain")
