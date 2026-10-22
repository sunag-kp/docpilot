"""Liveness and readiness probes.

* ``/healthz`` — the process is up (used by the orchestrator to restart a wedged pod).
* ``/readyz`` — every hard dependency answers; traffic should only be routed when 200.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from docpilot import __version__

router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_SECONDS = 2.0


class HealthResponse(BaseModel):
    status: str
    version: str = __version__


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readyz(request: Request, response: Response) -> ReadinessResponse:
    checks = request.app.state.container.readiness_checks
    names = list(checks)
    results = await asyncio.gather(
        *(asyncio.wait_for(checks[name](), _CHECK_TIMEOUT_SECONDS) for name in names),
        return_exceptions=True,
    )
    report = {
        name: "ok" if not isinstance(result, BaseException) else f"error: {type(result).__name__}"
        for name, result in zip(names, results, strict=True)
    }
    healthy = all(value == "ok" for value in report.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if healthy else "degraded", checks=report)
