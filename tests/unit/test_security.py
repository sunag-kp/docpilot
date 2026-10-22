from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from docpilot.domain.errors import AuthenticationError
from docpilot.infra.security import JwtTokenCodec, ScryptPasswordHasher, SystemClock
from tests.fakes import FixedClock

SECRET = "x" * 40


def test_scrypt_round_trip_and_salting() -> None:
    hasher = ScryptPasswordHasher(n=2**10)
    first, second = hasher.hash("hunter22"), hasher.hash("hunter22")
    assert first != second  # unique salt per hash
    assert first.startswith("scrypt$1024$8$1$")
    assert hasher.verify("hunter22", first)
    assert not hasher.verify("hunter23", first)


@pytest.mark.parametrize("encoded", ["", "bcrypt$x", "scrypt$1$2$3$!!$??", "scrypt$a$b$c$d$e"])
def test_scrypt_rejects_malformed_hashes(encoded: str) -> None:
    assert not ScryptPasswordHasher(n=2**10).verify("pw", encoded)


def test_jwt_round_trip() -> None:
    codec = JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=SystemClock())
    user, tenant = uuid4(), uuid4()
    token, expires_at = codec.issue(user, tenant)
    claims = codec.decode(token)
    assert (claims.subject_id, claims.tenant_id) == (user, tenant)
    assert claims.expires_at == expires_at.replace(microsecond=0)


def test_jwt_expired_token_rejected() -> None:
    past = FixedClock(datetime.now(UTC) - timedelta(hours=2))
    token, _ = JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=past).issue(
        uuid4(), uuid4()
    )
    with pytest.raises(AuthenticationError):
        JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=SystemClock()).decode(token)


def test_jwt_wrong_secret_or_issuer_rejected() -> None:
    token, _ = JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=SystemClock()).issue(
        uuid4(), uuid4()
    )
    with pytest.raises(AuthenticationError):
        JwtTokenCodec("y" * 40, issuer="docpilot", ttl_minutes=5, clock=SystemClock()).decode(token)
    with pytest.raises(AuthenticationError):
        JwtTokenCodec(SECRET, issuer="other", ttl_minutes=5, clock=SystemClock()).decode(token)


def test_jwt_alg_none_attack_rejected() -> None:
    now = int(datetime.now(UTC).timestamp())
    forged = jwt.encode(
        {"sub": str(uuid4()), "tid": str(uuid4()), "iss": "docpilot", "iat": now, "exp": now + 60},
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=SystemClock()).decode(forged)


def test_jwt_missing_tenant_claim_rejected() -> None:
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {"sub": str(uuid4()), "iss": "docpilot", "iat": now, "exp": now + 60}, SECRET, "HS256"
    )
    with pytest.raises(AuthenticationError):
        JwtTokenCodec(SECRET, issuer="docpilot", ttl_minutes=5, clock=SystemClock()).decode(token)
