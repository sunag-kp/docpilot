"""FastAPI dependencies: container access, authentication and rate limiting."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Request, Response, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from docpilot.container import Container
from docpilot.domain.errors import AuthenticationError, RateLimitedError
from docpilot.domain.models import Principal
from docpilot.observability.metrics import RATE_LIMITED
from docpilot.services.answering import QuestionAnswering
from docpilot.services.auth import API_KEY_PREFIX, AuthService
from docpilot.services.documents import DocumentService
from docpilot.services.ports import RateLimiter

_bearer = HTTPBearer(auto_error=False, description="JWT from /v1/auth/login or an API key")
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False, description="Per-tenant API key")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_auth_service(container: ContainerDep) -> AuthService:
    assert container.auth is not None
    return container.auth


def get_document_service(container: ContainerDep) -> DocumentService:
    assert container.documents is not None
    return container.documents


def get_answering(container: ContainerDep) -> QuestionAnswering:
    assert container.answering is not None
    return container.answering


def get_rate_limiter(container: ContainerDep) -> RateLimiter:
    assert container.rate_limiter is not None
    return container.rate_limiter


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
AnsweringDep = Annotated[QuestionAnswering, Depends(get_answering)]


async def get_principal(
    auth: AuthServiceDep,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
    api_key: Annotated[str | None, Security(_api_key)],
) -> Principal:
    if api_key:
        principal = await auth.authenticate_api_key(api_key)
    elif bearer is not None and bearer.credentials.startswith(API_KEY_PREFIX):
        principal = await auth.authenticate_api_key(bearer.credentials)
    elif bearer is not None:
        principal = auth.authenticate_token(bearer.credentials)
    else:
        raise AuthenticationError("missing credentials: send a Bearer token or X-API-Key")
    structlog.contextvars.bind_contextvars(tenant_id=str(principal.tenant_id))
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def rate_limited(cost: int = 1) -> Callable[..., Awaitable[Principal]]:
    """Authenticate, then spend ``cost`` tokens from the tenant's bucket."""

    async def dependency(
        principal: PrincipalDep,
        response: Response,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    ) -> Principal:
        decision = await limiter.acquire(principal.rate_limit_key, cost)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        if not decision.allowed:
            RATE_LIMITED.inc()
            raise RateLimitedError("rate limit exceeded", decision.retry_after_seconds)
        return principal

    return dependency
