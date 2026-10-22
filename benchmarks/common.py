"""Shared setup for the Locust load tests: a tenant with the evaluation corpus indexed."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "eval" / "corpus"


def load_questions() -> list[str]:
    rows = [
        json.loads(line) for line in (ROOT / "eval" / "qa.jsonl").read_text().splitlines() if line
    ]
    return [row["question"] for row in rows]


def create_tenant(host: str, *, upload_corpus: bool) -> dict[str, str]:
    """Sign up a fresh tenant (optionally indexing the corpus) and return an API key header."""
    with httpx.Client(base_url=host, timeout=60) as client:
        signup = client.post(
            "/v1/auth/signup",
            json={
                "tenant_name": "Load test",
                "email": f"load-{uuid.uuid4().hex[:10]}@bench.example",
                "password": "load-test-password",
            },
        ).raise_for_status()
        auth = {"Authorization": f"Bearer {signup.json()['access_token']}"}
        key = client.post("/v1/api-keys", json={"name": "locust"}, headers=auth).raise_for_status()
        headers = {"X-API-Key": key.json()["key"]}
        if upload_corpus:
            for path in sorted(CORPUS.iterdir()):
                client.post(
                    "/v1/documents", files={"file": (path.name, path.read_bytes())}, headers=headers
                ).raise_for_status()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                docs = client.get("/v1/documents", headers=headers).json()["items"]
                if docs and all(d["status"] == "ready" for d in docs):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("corpus was not indexed in time")
        return headers
