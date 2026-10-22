"""S3-compatible object storage (MinIO locally; AWS S3, R2, B2, ... in production)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from docpilot.domain.errors import PermanentIngestionError, TransientIngestionError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class S3ObjectStorage:
    """boto3 is synchronous, so calls run in a worker thread to keep the event loop free."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None,
        region: str,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=3,
                read_timeout=30,
                s3={"addressing_style": "path"},
            ),
        )

    async def ensure_bucket(self) -> None:
        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(_ensure)

    async def ping(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body

        try:
            return await asyncio.to_thread(_get)
        except ClientError as exc:
            if _error_code(exc) in {"NoSuchKey", "404"}:
                raise PermanentIngestionError(f"source object {key!r} not found") from exc
            raise TransientIngestionError(f"storage error: {exc}") from exc
        except BotoCoreError as exc:
            raise TransientIngestionError(f"storage error: {exc}") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)


def _error_code(exc: ClientError) -> str:
    error: Any = exc.response.get("Error", {})
    return str(error.get("Code", ""))
