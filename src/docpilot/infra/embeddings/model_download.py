"""Fetch the local embedding model once (build time or first start).

Sources, in order: the Hugging Face Hub (what fastembed itself uses), then a public mirror
of the identical ONNX export verified by SHA-256. The Docker image runs this at build time
so containers start fully offline.

    python -m docpilot.infra.embeddings.model_download --cache-dir .models
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import snapshot_download

from docpilot.observability.logging import get_logger

_log = get_logger(__name__)
_REQUIRED_FILES = ("model.onnx", "tokenizer.json", "config.json")


@dataclass(frozen=True, slots=True)
class ModelSource:
    hf_repo: str
    mirror_url: str | None = None
    mirror_sha256: str | None = None


KNOWN_MODELS: dict[str, ModelSource] = {
    # Apache-2.0, 22M params, 384-d. Mirror = the export Chroma ships for its default model.
    "sentence-transformers/all-MiniLM-L6-v2": ModelSource(
        hf_repo="Qdrant/all-MiniLM-L6-v2-onnx",
        mirror_url="https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz",
        mirror_sha256="913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3",
    ),
    # MIT, 33M params, 384-d; stronger on retrieval benchmarks (HF only).
    "BAAI/bge-small-en-v1.5": ModelSource(hf_repo="Qdrant/bge-small-en-v1.5-onnx-Q"),
}


def model_dir(cache_dir: str | Path, model_name: str) -> Path:
    return Path(cache_dir) / model_name.replace("/", "--")


def is_complete(path: Path) -> bool:
    return all((path / name).is_file() for name in _REQUIRED_FILES)


def ensure_model(model_name: str, cache_dir: str | Path) -> Path:
    target = model_dir(cache_dir, model_name)
    if is_complete(target):
        return target
    source = KNOWN_MODELS.get(model_name)
    if source is None:
        raise ValueError(f"unknown embedding model {model_name!r}; known: {sorted(KNOWN_MODELS)}")
    target.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    try:
        _from_hub(source, target)
    except Exception as exc:
        errors.append(f"huggingface: {exc}")
        _log.warning("model_hub_download_failed", model=model_name, error=str(exc))
    if not is_complete(target) and source.mirror_url:
        try:
            _from_mirror(source, target)
        except Exception as exc:
            errors.append(f"mirror: {exc}")
    if not is_complete(target):
        raise RuntimeError(f"could not download {model_name}: {'; '.join(errors)}")
    _log.info("model_ready", model=model_name, path=str(target))
    return target


def _from_hub(source: ModelSource, target: Path) -> None:
    snapshot_download(repo_id=source.hf_repo, local_dir=str(target), etag_timeout=10)


def _from_mirror(source: ModelSource, target: Path) -> None:
    assert source.mirror_url is not None
    assert source.mirror_sha256 is not None
    with urllib.request.urlopen(source.mirror_url, timeout=120) as response:  # noqa: S310
        payload: bytes = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != source.mirror_sha256:
        raise ValueError(f"checksum mismatch: {digest}")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name  # flatten; never trust archive paths
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            with (target / name).open("wb") as out:
                shutil.copyfileobj(extracted, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--cache-dir", default=".models")
    args = parser.parse_args()
    print(ensure_model(args.model, args.cache_dir))


if __name__ == "__main__":
    main()
