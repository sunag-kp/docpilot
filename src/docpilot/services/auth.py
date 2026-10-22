"""Authentication use cases: tenant signup, login, JWT and per-tenant API keys."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from docpilot.domain.errors import AuthenticationError, ConflictError, NotFoundError
from docpilot.domain.models import ApiKey, Principal, Tenant, User
from docpilot.services.ports import Clock, PasswordHasher, TokenCodec, UnitOfWorkFactory

API_KEY_PREFIX = "dp_"
_DISPLAY_PREFIX_LEN = 10


def hash_api_key(raw_key: str) -> str:
    # API keys carry 256 bits of entropy, so a fast hash is safe here (unlike passwords):
    # brute-forcing the preimage is infeasible and lookups stay O(1) via a unique index.
    return hashlib.sha256(raw_key.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedToken:
    access_token: str
    expires_at: datetime
    tenant_id: UUID
    user_id: UUID


class AuthService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        hasher: PasswordHasher,
        tokens: TokenCodec,
        clock: Clock,
        *,
        signup_enabled: bool = True,
    ) -> None:
        self._uow = uow_factory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._signup_enabled = signup_enabled
        # Verified against when the email is unknown so both paths cost the same (no
        # user enumeration through response timing).
        self._dummy_hash = hasher.hash(secrets.token_urlsafe(16))

    async def signup(self, tenant_name: str, email: str, password: str) -> IssuedToken:
        if not self._signup_enabled:
            raise AuthenticationError("signup is disabled")
        email = email.strip().lower()
        now = self._clock.now()
        tenant = Tenant(id=uuid4(), name=tenant_name.strip(), created_at=now)
        user = User(
            id=uuid4(),
            tenant_id=tenant.id,
            email=email,
            password_hash=self._hasher.hash(password),
            created_at=now,
        )
        async with self._uow() as uow:
            if await uow.users.get_by_email(email) is not None:
                raise ConflictError("an account with this email already exists")
            await uow.tenants.add(tenant)
            await uow.users.add(user)
            await uow.commit()
        return self._issue(user)

    async def login(self, email: str, password: str) -> IssuedToken:
        async with self._uow() as uow:
            user = await uow.users.get_by_email(email.strip().lower())
        if user is None:
            self._hasher.verify(password, self._dummy_hash)
            raise AuthenticationError("invalid email or password")
        if not self._hasher.verify(password, user.password_hash):
            raise AuthenticationError("invalid email or password")
        return self._issue(user)

    def authenticate_token(self, token: str) -> Principal:
        claims = self._tokens.decode(token)
        return Principal(tenant_id=claims.tenant_id, subject_id=claims.subject_id, kind="user")

    async def authenticate_api_key(self, raw_key: str) -> Principal:
        if not raw_key.startswith(API_KEY_PREFIX):
            raise AuthenticationError("invalid API key")
        async with self._uow() as uow:
            key = await uow.api_keys.get_active_by_hash(hash_api_key(raw_key))
        if key is None:
            raise AuthenticationError("invalid API key")
        return Principal(tenant_id=key.tenant_id, subject_id=key.id, kind="api_key")

    async def create_api_key(self, principal: Principal, name: str) -> tuple[ApiKey, str]:
        raw_key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        key = ApiKey(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            name=name.strip(),
            prefix=raw_key[:_DISPLAY_PREFIX_LEN],
            created_at=self._clock.now(),
        )
        async with self._uow() as uow:
            await uow.api_keys.add(key, hash_api_key(raw_key))
            await uow.commit()
        return key, raw_key

    async def list_api_keys(self, principal: Principal) -> list[ApiKey]:
        async with self._uow() as uow:
            return await uow.api_keys.list_for_tenant(principal.tenant_id)

    async def revoke_api_key(self, principal: Principal, key_id: UUID) -> None:
        async with self._uow() as uow:
            revoked = await uow.api_keys.revoke(principal.tenant_id, key_id, self._clock.now())
            await uow.commit()
        if not revoked:
            raise NotFoundError("API key not found")

    def _issue(self, user: User) -> IssuedToken:
        token, expires_at = self._tokens.issue(user.id, user.tenant_id)
        return IssuedToken(
            access_token=token, expires_at=expires_at, tenant_id=user.tenant_id, user_id=user.id
        )
