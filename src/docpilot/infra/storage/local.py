"""Filesystem and in-memory storage: single-node dev mode and tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from docpilot.domain.errors import PermanentIngestionError


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):  # defence against "../" keys
            raise ValueError(f"invalid storage key {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic rename: readers never see a partial file

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise PermanentIngestionError(f"source object {key!r} not found") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError as exc:
            raise PermanentIngestionError(f"source object {key!r} not found") from exc

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
