"""Integration events exchanged over the event bus.

Events are versioned JSON documents. Consumers must tolerate unknown fields so producers
can add fields without a coordinated deploy (forward compatibility).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from docpilot.domain.models import DocumentKind


@dataclass(frozen=True, slots=True)
class DocumentUploaded:
    document_id: UUID
    tenant_id: UUID
    storage_key: str
    kind: DocumentKind
    filename: str
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    event_type = "document.uploaded"
    schema_version = 1

    @property
    def partition_key(self) -> bytes:
        # Keying by document keeps every event for one document on one partition (ordering).
        return str(self.document_id).encode()

    def to_json(self) -> bytes:
        payload: dict[str, Any] = {
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            **{k: str(v) if isinstance(v, UUID) else v for k, v in asdict(self).items()},
        }
        payload["occurred_at"] = self.occurred_at.isoformat()
        payload["kind"] = self.kind.value
        return json.dumps(payload, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, raw: bytes | str) -> DocumentUploaded:
        data = json.loads(raw)
        if data.get("event_type") != cls.event_type:
            raise ValueError(f"unexpected event type {data.get('event_type')!r}")
        return cls(
            document_id=UUID(data["document_id"]),
            tenant_id=UUID(data["tenant_id"]),
            storage_key=data["storage_key"],
            kind=DocumentKind(data["kind"]),
            filename=data["filename"],
            event_id=UUID(data["event_id"]),
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
        )


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """Envelope written to the dead-letter topic when a document cannot be processed."""

    event: DocumentUploaded
    error_type: str
    error_message: str
    attempts: int
    failed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "event": json.loads(self.event.to_json()),
                "error_type": self.error_type,
                "error_message": self.error_message[:2000],
                "attempts": self.attempts,
                "failed_at": self.failed_at.isoformat(),
            },
            separators=(",", ":"),
        ).encode()
