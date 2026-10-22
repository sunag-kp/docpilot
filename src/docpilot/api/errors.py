"""Map domain errors to RFC 7807 ``application/problem+json`` responses."""

from __future__ import annotations

import math

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from docpilot.domain.errors import (
    AuthenticationError,
    ConflictError,
    DocumentTooLargeError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    UnsupportedDocumentError,
)
from docpilot.observability.logging import get_logger

_log = get_logger(__name__)

_STATUS: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    UnsupportedDocumentError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    DocumentTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
}


def problem(
    request: Request,
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:docpilot:error:{code}",
        "title": code.replace("_", " ").capitalize(),
        "status": status_code,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        body, status_code=status_code, headers=headers, media_type="application/problem+json"
    )


def _status_for(exc: DomainError) -> int:
    for cls in type(exc).__mro__:
        if cls in _STATUS:
            return _STATUS[cls]
    return status.HTTP_400_BAD_REQUEST


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(max(1, math.ceil(exc.retry_after_seconds)))
    if isinstance(exc, AuthenticationError):
        headers["WWW-Authenticate"] = "Bearer"
    return problem(request, _status_for(exc), exc.code, exc.message, headers=headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return problem(
        request,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "request validation failed",
        extra={"errors": errors},
    )


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_error", path=request.url.path)
    return problem(
        request, status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "internal server error"
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
