"""End-to-end smoke test against a running stack (`make up` first).

    uv run python scripts/smoke_test.py --base-url http://localhost:8000

Signs up a fresh tenant, uploads a document, waits for the worker to index it and checks
the chunks. Exits non-zero on any failure, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

SAMPLE = b"""# Smoke Test Handbook

## Vacation
Full-time employees accrue 1.5 days of paid vacation per month of service.

## Security
Passwords must be rotated every 90 days and multi-factor authentication is mandatory.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        ready = client.get("/readyz")
        print("readyz:", ready.status_code, ready.json())
        ready.raise_for_status()

        email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
        token = client.post(
            "/v1/auth/signup",
            json={"tenant_name": "Smoke", "email": email, "password": "smoke-test-pass"},
        ).raise_for_status().json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        marker = uuid.uuid4().hex.encode()
        upload = client.post(
            "/v1/documents",
            files={"file": ("handbook.md", SAMPLE + b"\nRef " + marker + b".\n")},
            headers=headers,
        ).raise_for_status()
        doc_id = upload.json()["id"]
        print("uploaded:", doc_id, upload.status_code)

        deadline = time.monotonic() + args.timeout
        status = "queued"
        while time.monotonic() < deadline:
            status = client.get(f"/v1/documents/{doc_id}", headers=headers).json()["status"]
            if status in {"ready", "failed"}:
                break
            time.sleep(0.5)
        print("status:", status)
        if status != "ready":
            return 1

        chunks = client.get(f"/v1/documents/{doc_id}/chunks", headers=headers).json()
        sections = [c["section"] for c in chunks]
        print("chunks:", sections)
        if sections != ["Smoke Test Handbook > Vacation", "Smoke Test Handbook > Security"]:
            return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
