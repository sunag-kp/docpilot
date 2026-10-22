"""Upload an assembled Space directory to Hugging Face (used by the deploy workflow).

    HF_TOKEN=... HF_SPACE=<user>/docpilot python deploy/huggingface/upload.py <dir>

Creates the Docker Space on first use. Hugging Face then builds the image from the
uploaded Dockerfile and serves it on the free CPU tier.
"""

from __future__ import annotations

import os
import sys

from huggingface_hub import HfApi


def main() -> None:
    folder = sys.argv[1]
    space = os.environ["HF_SPACE"]
    api = HfApi(token=os.environ["HF_TOKEN"])
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    sha = os.environ.get("GITHUB_SHA", "local")[:7]
    info = api.upload_folder(
        repo_id=space,
        repo_type="space",
        folder_path=folder,
        commit_message=f"Deploy {sha}",
        # Replace these trees wholesale so files removed in the repo disappear here too.
        delete_patterns=["src/**", "ui/**", "migrations/**", "eval/**"],
    )
    sys.stdout.write(f"deployed {sha} to https://huggingface.co/spaces/{space} ({info.oid})\n")


if __name__ == "__main__":
    main()
