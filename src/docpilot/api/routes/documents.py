"""Document upload, status and lifecycle."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from docpilot.api.deps import DocumentServiceDep, PrincipalDep, rate_limited
from docpilot.api.schemas import (
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
    UploadResponse,
)
from docpilot.domain.errors import DocumentTooLargeError
from docpilot.domain.models import DocumentStatus, Principal

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_UPLOAD_COST = 5  # uploads are heavier than queries; they spend more of the bucket


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": UploadResponse, "description": "Identical file already uploaded"}},
)
async def upload_document(
    response: Response,
    documents: DocumentServiceDep,
    principal: Annotated[Principal, Depends(rate_limited(_UPLOAD_COST))],
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown or plain text")],
) -> UploadResponse:
    """Accept a file and queue it for asynchronous ingestion (202 + status URL)."""
    limit = documents.max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise DocumentTooLargeError(f"file exceeds {limit // (1024 * 1024)} MB")
    result = await documents.upload(principal, file.filename or "", data)
    response.headers["Location"] = f"/v1/documents/{result.document.id}"
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return UploadResponse(
        **DocumentResponse.from_domain(result.document).model_dump(), duplicate=not result.created
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    documents: DocumentServiceDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
) -> DocumentListResponse:
    items, total = await documents.list_documents(
        principal, limit=limit, offset=offset, status=status_filter
    )
    return DocumentListResponse(
        items=[DocumentResponse.from_domain(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID, documents: DocumentServiceDep, principal: PrincipalDep
) -> DocumentResponse:
    """Per-document ingestion status: queued → processing → ready | failed."""
    return DocumentResponse.from_domain(await documents.get(principal, document_id))


@router.get("/{document_id}/chunks", response_model=list[ChunkResponse])
async def list_chunks(
    document_id: UUID, documents: DocumentServiceDep, principal: PrincipalDep
) -> list[ChunkResponse]:
    return [ChunkResponse.from_domain(c) for c in await documents.chunks(principal, document_id)]


@router.post("/{document_id}/retry", response_model=DocumentResponse, status_code=202)
async def retry_document(
    document_id: UUID, documents: DocumentServiceDep, principal: PrincipalDep
) -> DocumentResponse:
    """Re-queue a failed (dead-lettered) document."""
    return DocumentResponse.from_domain(await documents.retry(principal, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID, documents: DocumentServiceDep, principal: PrincipalDep
) -> None:
    await documents.delete(principal, document_id)
