from __future__ import annotations

from uuid import uuid4

import httpx

from tests.unit.api_helpers import signup


async def test_metrics_use_route_templates_not_raw_paths(client: httpx.AsyncClient) -> None:
    headers = await signup(client)
    missing = uuid4()
    assert (await client.get(f"/v1/documents/{missing}", headers=headers)).status_code == 404
    body = (await client.get("/metrics")).text

    assert (
        'docpilot_http_requests_total{method="GET",route="/v1/documents/{document_id}",status="404"}'
        in body
    )
    assert str(missing) not in body  # bounded label cardinality
    assert 'route="/metrics"' not in body  # scrapes don't measure themselves
    assert "docpilot_http_request_duration_seconds_bucket" in body
    assert "docpilot_http_requests_in_flight" in body
