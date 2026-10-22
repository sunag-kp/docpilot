"""Domain errors. The API layer maps each one to an HTTP status (RFC 7807 problem)."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, user-facing failures."""

    code = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class AuthenticationError(DomainError):
    code = "unauthenticated"


class PermissionDeniedError(DomainError):
    code = "forbidden"


class InvalidInputError(DomainError):
    code = "invalid_input"


class UnsupportedDocumentError(InvalidInputError):
    code = "unsupported_document"


class DocumentTooLargeError(InvalidInputError):
    code = "document_too_large"


class RateLimitedError(DomainError):
    code = "rate_limited"

    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


# --- ingestion -------------------------------------------------------------------------


class IngestionError(Exception):
    """Base for pipeline failures; ``retryable`` decides between backoff and dead-letter."""

    retryable = True


class TransientIngestionError(IngestionError):
    """Infrastructure hiccup (storage, embedding backend, DB): retry with backoff."""


class PermanentIngestionError(IngestionError):
    """Bad input that will never succeed (corrupt PDF, empty document): dead-letter now."""

    retryable = False
