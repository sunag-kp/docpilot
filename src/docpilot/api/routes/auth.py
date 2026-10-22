"""Signup, login and per-tenant API keys."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from docpilot.api.deps import AuthServiceDep, PrincipalDep, rate_limited
from docpilot.api.schemas import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    LoginRequest,
    SignupRequest,
    TokenResponse,
)
from docpilot.domain.models import Principal
from docpilot.services.auth import IssuedToken

router = APIRouter(prefix="/v1", tags=["auth"])


def _token(issued: IssuedToken) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        expires_at=issued.expires_at,
        tenant_id=issued.tenant_id,
        user_id=issued.user_id,
    )


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
async def signup(body: SignupRequest, auth: AuthServiceDep) -> TokenResponse:
    """Create a tenant and its first user; returns an access token."""
    return _token(await auth.signup(body.tenant_name, body.email, body.password))


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenResponse:
    return _token(await auth.login(body.email, body.password))


@router.post("/api-keys", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreatedResponse)
async def create_api_key(
    body: ApiKeyCreateRequest,
    auth: AuthServiceDep,
    principal: Annotated[Principal, Depends(rate_limited())],
) -> ApiKeyCreatedResponse:
    """Create a tenant API key. The secret is returned once and stored only as a hash."""
    key, raw = await auth.create_api_key(principal, body.name)
    return ApiKeyCreatedResponse(**ApiKeyResponse.from_domain(key).model_dump(), key=raw)


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(auth: AuthServiceDep, principal: PrincipalDep) -> list[ApiKeyResponse]:
    return [ApiKeyResponse.from_domain(k) for k in await auth.list_api_keys(principal)]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: UUID, auth: AuthServiceDep, principal: PrincipalDep) -> None:
    await auth.revoke_api_key(principal, key_id)
