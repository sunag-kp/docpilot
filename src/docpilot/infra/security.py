"""Password hashing (scrypt, stdlib) and JWT access tokens (HS256)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from docpilot.domain.errors import AuthenticationError
from docpilot.services.ports import Clock, TokenClaims


class ScryptPasswordHasher:
    """scrypt is memory-hard (OWASP-recommended) and ships with Python: no native deps.

    Encoded as ``scrypt$n$r$p$salt$hash`` so parameters can be raised later while old
    hashes keep verifying.
    """

    def __init__(self, n: int = 2**14, r: int = 8, p: int = 1) -> None:
        self._n, self._r, self._p = n, r, p

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt, self._n, self._r, self._p)
        return "$".join(
            ["scrypt", str(self._n), str(self._r), str(self._p), _b64(salt), _b64(digest)]
        )

    def verify(self, password: str, encoded: str) -> bool:
        try:
            scheme, n, r, p, salt, digest = encoded.split("$")
            if scheme != "scrypt":
                return False
            candidate = self._derive(password, _unb64(salt), int(n), int(r), int(p))
        except ValueError:
            return False
        return hmac.compare_digest(candidate, _unb64(digest))

    @staticmethod
    def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
        return hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024**2, dklen=32
        )


class JwtTokenCodec:
    _ALGORITHM = "HS256"

    def __init__(self, secret: str, *, issuer: str, ttl_minutes: int, clock: Clock) -> None:
        self._secret = secret
        self._issuer = issuer
        self._ttl = timedelta(minutes=ttl_minutes)
        self._clock = clock

    def issue(self, subject_id: UUID, tenant_id: UUID) -> tuple[str, datetime]:
        now = self._clock.now()
        expires_at = now + self._ttl
        payload = {
            "sub": str(subject_id),
            "tid": str(tenant_id),
            "iss": self._issuer,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_hex(8),
        }
        return jwt.encode(payload, self._secret, algorithm=self._ALGORITHM), expires_at

    def decode(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._ALGORITHM],  # pinned: never trust the token's own "alg"
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "tid", "iss"]},
            )
            return TokenClaims(
                subject_id=UUID(payload["sub"]),
                tenant_id=UUID(payload["tid"]),
                expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise AuthenticationError("invalid or expired token") from exc


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
