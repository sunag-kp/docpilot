#!/usr/bin/env bash
# Assemble the Hugging Face Space repository (build context) into a directory.
#   deploy/huggingface/assemble.sh <out-dir>
set -euo pipefail

OUT="${1:?usage: assemble.sh <out-dir>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
rm -rf "$OUT"
mkdir -p "$OUT/eval"

cd "$ROOT"
cp -r src migrations alembic.ini pyproject.toml uv.lock LICENSE "$OUT/"
cp -r eval/corpus_src "$OUT/eval/"
mkdir -p "$OUT/ui"
tar -c --exclude=node_modules --exclude=dist -C ui . | tar -x -C "$OUT/ui"
cp deploy/huggingface/Dockerfile deploy/huggingface/start.sh deploy/huggingface/README.md "$OUT/"
find "$OUT" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "assembled $(find "$OUT" -type f | wc -l) files in $OUT"
