from __future__ import annotations

import json
from uuid import uuid4

import pytest

from docpilot.domain.events import DeadLetter, DocumentUploaded
from docpilot.domain.models import DocumentKind


def make_event() -> DocumentUploaded:
    return DocumentUploaded(
        document_id=uuid4(),
        tenant_id=uuid4(),
        storage_key="tenants/t/documents/d/source.pdf",
        kind=DocumentKind.PDF,
        filename="report.pdf",
    )


def test_event_json_round_trip() -> None:
    event = make_event()
    assert DocumentUploaded.from_json(event.to_json()) == event
    assert event.partition_key == str(event.document_id).encode()


def test_unknown_fields_are_tolerated_for_forward_compatibility() -> None:
    payload = json.loads(make_event().to_json())
    payload["added_in_v2"] = {"anything": True}
    assert DocumentUploaded.from_json(json.dumps(payload)).filename == "report.pdf"


def test_wrong_event_type_is_rejected() -> None:
    payload = json.loads(make_event().to_json())
    payload["event_type"] = "document.deleted"
    with pytest.raises(ValueError, match="unexpected event type"):
        DocumentUploaded.from_json(json.dumps(payload))


def test_dead_letter_envelope_truncates_long_errors() -> None:
    letter = DeadLetter(event=make_event(), error_type="X", error_message="e" * 5000, attempts=4)
    body = json.loads(letter.to_json())
    assert body["attempts"] == 4
    assert len(body["error_message"]) == 2000
    assert body["event"]["kind"] == "pdf"
