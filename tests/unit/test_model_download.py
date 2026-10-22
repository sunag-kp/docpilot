from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from docpilot.infra.embeddings import model_download
from docpilot.infra.embeddings.model_download import ModelSource, ensure_model, model_dir


def tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in ("onnx/model.onnx", "onnx/tokenizer.json", "onnx/config.json", "../evil.txt"):
            data = name.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.fixture
def offline_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_hub(source: ModelSource, target: Path) -> None:
        raise ConnectionError("hub blocked")

    monkeypatch.setattr(model_download, "_from_hub", no_hub)


def use_mirror(monkeypatch: pytest.MonkeyPatch, payload: bytes, sha: str) -> None:
    monkeypatch.setitem(
        model_download.KNOWN_MODELS, "test/model", ModelSource("x/y", "https://mirror/m.tgz", sha)
    )
    monkeypatch.setattr(
        model_download.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload)
    )


def test_falls_back_to_checksummed_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline_hub: None
) -> None:
    payload = tarball()
    use_mirror(monkeypatch, payload, hashlib.sha256(payload).hexdigest())
    path = ensure_model("test/model", tmp_path)
    assert path == model_dir(tmp_path, "test/model")
    assert sorted(p.name for p in path.iterdir()) == [
        "config.json",
        "evil.txt",
        "model.onnx",
        "tokenizer.json",
    ]
    assert not (tmp_path / "evil.txt").exists()  # archive paths are flattened, never trusted
    assert ensure_model("test/model", tmp_path) == path  # cached: no second download


def test_checksum_mismatch_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline_hub: None
) -> None:
    use_mirror(monkeypatch, tarball(), "0" * 64)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        ensure_model("test/model", tmp_path)


def test_unknown_model_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown embedding model"):
        ensure_model("nobody/nothing", tmp_path)
