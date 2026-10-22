"""Shared helpers for API tests."""

from __future__ import annotations

import httpx

MD = b"# Handbook\n\n## Vacation\nEmployees accrue 1.5 vacation days per month.\n"


async def signup(client: httpx.AsyncClient, email: str = "dev@acme.test") -> dict[str, str]:
    response = await client.post(
        "/v1/auth/signup",
        json={"tenant_name": "Acme", "email": email, "password": "s3cret-pass"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
