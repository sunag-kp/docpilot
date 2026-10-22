from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import pytest

from docpilot.domain.errors import AuthenticationError, ConflictError, NotFoundError
from docpilot.infra.security import JwtTokenCodec, ScryptPasswordHasher, SystemClock
from docpilot.services.auth import AuthService, hash_api_key
from docpilot.services.ports import UnitOfWork


async def test_signup_then_login_and_token_auth(auth_service: AuthService) -> None:
    issued = await auth_service.signup("Acme", "Dev@Acme.test", "s3cret-pass")
    principal = auth_service.authenticate_token(issued.access_token)
    assert principal.tenant_id == issued.tenant_id
    assert principal.kind == "user"

    again = await auth_service.login("dev@acme.TEST", "s3cret-pass")  # email is case-insensitive
    assert again.tenant_id == issued.tenant_id


async def test_duplicate_signup_conflicts(auth_service: AuthService) -> None:
    await auth_service.signup("Acme", "dev@acme.test", "s3cret-pass")
    with pytest.raises(ConflictError):
        await auth_service.signup("Other", "DEV@acme.test", "another-pass")


@pytest.mark.parametrize(("email", "password"), [("dev@acme.test", "wrong"), ("nobody@x.io", "x")])
async def test_login_failures_are_indistinguishable(
    auth_service: AuthService, email: str, password: str
) -> None:
    await auth_service.signup("Acme", "dev@acme.test", "s3cret-pass")
    with pytest.raises(AuthenticationError, match="invalid email or password"):
        await auth_service.login(email, password)


async def test_api_key_lifecycle(auth_service: AuthService) -> None:
    issued = await auth_service.signup("Acme", "dev@acme.test", "s3cret-pass")
    owner = auth_service.authenticate_token(issued.access_token)

    key, raw = await auth_service.create_api_key(owner, " ci-bot ")
    assert raw.startswith("dp_")
    assert len(raw) > 40
    assert key.prefix == raw[:10]
    assert key.name == "ci-bot"

    via_key = await auth_service.authenticate_api_key(raw)
    assert via_key.tenant_id == owner.tenant_id
    assert via_key.kind == "api_key"
    assert [k.id for k in await auth_service.list_api_keys(owner)] == [key.id]

    await auth_service.revoke_api_key(owner, key.id)
    with pytest.raises(AuthenticationError):
        await auth_service.authenticate_api_key(raw)
    with pytest.raises(NotFoundError):
        await auth_service.revoke_api_key(owner, key.id)


async def test_api_key_of_another_tenant_cannot_be_revoked(auth_service: AuthService) -> None:
    a = auth_service.authenticate_token(
        (await auth_service.signup("A", "a@a.io", "password1")).access_token
    )
    b = auth_service.authenticate_token(
        (await auth_service.signup("B", "b@b.io", "password1")).access_token
    )
    key, _ = await auth_service.create_api_key(a, "k")
    with pytest.raises(NotFoundError):
        await auth_service.revoke_api_key(b, key.id)


@pytest.mark.parametrize("raw", ["not-a-key", "dp_unknown", ""])
async def test_unknown_api_keys_rejected(auth_service: AuthService, raw: str) -> None:
    with pytest.raises(AuthenticationError):
        await auth_service.authenticate_api_key(raw)


async def test_signup_can_be_disabled(uow_factory: Callable[[], UnitOfWork]) -> None:
    service = AuthService(
        uow_factory,
        ScryptPasswordHasher(n=2**10),
        JwtTokenCodec("t" * 40, issuer="docpilot", ttl_minutes=5, clock=SystemClock()),
        SystemClock(),
        signup_enabled=False,
    )
    with pytest.raises(AuthenticationError, match="disabled"):
        await service.signup("Acme", "dev@acme.test", "s3cret-pass")


def test_api_key_hash_is_stable_and_not_the_key() -> None:
    raw = f"dp_{uuid4().hex}"
    assert hash_api_key(raw) == hash_api_key(raw)
    assert raw not in hash_api_key(raw)
