"""S3StorageService: async wrapper around boto3 for audio and transcript storage.

Boto3 is synchronous; all calls are dispatched to a thread-pool via
``asyncio.get_event_loop().run_in_executor`` so they never block the event loop.

Configuration is driven by the application settings:
    S3_ENDPOINT_URL  – optional custom endpoint (e.g. MinIO, LocalStack)
    S3_ACCESS_KEY    – AWS / MinIO access key ID
    S3_SECRET_KEY    – AWS / MinIO secret access key
    S3_BUCKET        – target bucket name
"""

from __future__ import annotations

import asyncio
import mimetypes
import functools
from typing import Optional

import boto3
import structlog
from botocore.exceptions import ClientError

from app.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton (lazily created per process)
# ---------------------------------------------------------------------------

_storage_service: Optional["S3StorageService"] = None


def get_storage_service() -> "S3StorageService":
    """Return the singleton S3StorageService, creating it on first call."""
    global _storage_service
    if _storage_service is None:
        _storage_service = S3StorageService()
    return _storage_service


# ---------------------------------------------------------------------------
# Helper — run boto3 calls in a thread pool
# ---------------------------------------------------------------------------


async def _run_in_executor(func, *args, **kwargs):
    """Execute a synchronous callable in the default thread-pool executor."""
    loop = asyncio.get_event_loop()
    partial_func = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, partial_func)


# ---------------------------------------------------------------------------
# S3StorageService
# ---------------------------------------------------------------------------


class S3StorageService:
    """Async-friendly S3 storage service backed by boto3.

    All public methods are coroutines; the underlying boto3 client calls are
    dispatched to a thread-pool to avoid blocking the asyncio event loop.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket

        # Build boto3 client kwargs — endpoint_url is optional (omit for real AWS)
        client_kwargs: dict = {
            "service_name": "s3",
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url

        self._client = boto3.client(**client_kwargs)

        logger.debug(
            "storage.s3_client_created",
            bucket=self._bucket,
            endpoint=settings.s3_endpoint_url or "aws-default",
        )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_audio(
        self,
        file_bytes: bytes,
        filename: str,
        tenant_id: str,
        meeting_id: str,
    ) -> str:
        """Upload raw audio bytes to S3.

        Parameters
        ----------
        file_bytes:
            Raw audio content.
        filename:
            Original filename (used for key suffix and MIME detection).
        tenant_id:
            UUID string of the owning tenant (used as key prefix for isolation).
        meeting_id:
            UUID string of the meeting (used as key segment).

        Returns
        -------
        str
            The S3 object key that was used for the upload.
        """
        key = f"{tenant_id}/{meeting_id}/audio/{filename}"
        content_type = _detect_content_type(filename)

        def _upload() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=file_bytes,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )

        await _run_in_executor(_upload)

        logger.info(
            "storage.audio_uploaded",
            bucket=self._bucket,
            key=key,
            bytes=len(file_bytes),
            content_type=content_type,
        )
        return key

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download_file(self, s3_key: str) -> bytes:
        """Download an object from S3 and return its body as bytes.

        Parameters
        ----------
        s3_key:
            Full S3 object key.

        Returns
        -------
        bytes
            Raw body content.

        Raises
        ------
        ClientError
            Propagated as-is (e.g. 404 NoSuchKey).
        """

        def _download() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=s3_key)
            return response["Body"].read()

        data: bytes = await _run_in_executor(_download)

        logger.debug("storage.file_downloaded", key=s3_key, bytes=len(data))
        return data

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_file(self, s3_key: str) -> None:
        """Delete an S3 object.

        A missing key is logged as a warning but does *not* raise an exception
        — callers may safely call this in cleanup paths without extra guards.

        Parameters
        ----------
        s3_key:
            Full S3 object key.
        """

        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=s3_key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    logger.warning("storage.delete_key_not_found", key=s3_key)
                else:
                    raise

        await _run_in_executor(_delete)
        logger.info("storage.file_deleted", key=s3_key)

    # ------------------------------------------------------------------
    # Pre-signed URL
    # ------------------------------------------------------------------

    async def get_presigned_url(self, s3_key: str, expires: int = 3600) -> str:
        """Generate a pre-signed GET URL for an S3 object.

        Parameters
        ----------
        s3_key:
            Full S3 object key.
        expires:
            URL lifetime in seconds (default: 3600 / 1 hour).

        Returns
        -------
        str
            Pre-signed HTTPS URL.
        """

        def _presign() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": s3_key},
                ExpiresIn=expires,
            )

        url: str = await _run_in_executor(_presign)

        logger.debug("storage.presigned_url_generated", key=s3_key, expires=expires)
        return url

    # ------------------------------------------------------------------
    # Save transcript
    # ------------------------------------------------------------------

    async def save_transcript(
        self,
        text: str,
        tenant_id: str,
        meeting_id: str,
    ) -> str:
        """Persist a plain-text transcript to S3.

        The file is stored at ``{tenant_id}/{meeting_id}/transcript.txt``.

        Parameters
        ----------
        text:
            Full transcript text (UTF-8).
        tenant_id:
            UUID string of the owning tenant.
        meeting_id:
            UUID string of the meeting.

        Returns
        -------
        str
            The S3 object key.
        """
        key = f"{tenant_id}/{meeting_id}/transcript.txt"
        encoded = text.encode("utf-8")

        def _upload() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=encoded,
                ContentType="text/plain; charset=utf-8",
                ServerSideEncryption="AES256",
            )

        await _run_in_executor(_upload)

        logger.info(
            "storage.transcript_saved",
            bucket=self._bucket,
            key=key,
            chars=len(text),
        )
        return key


# ---------------------------------------------------------------------------
# Module-level convenience alias (used in meetings.py delete path)
# ---------------------------------------------------------------------------


async def delete_object(s3_key: str) -> None:
    """Module-level shortcut for ad-hoc S3 deletions (e.g. from router cleanup)."""
    await get_storage_service().delete_file(s3_key)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_content_type(filename: str) -> str:
    """Best-effort MIME type detection from the filename extension."""
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type:
        return mime_type

    # Manual fallbacks for common audio formats
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    _FALLBACKS: dict[str, str] = {
        "mp3": "audio/mpeg",
        "mp4": "video/mp4",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "aac": "audio/aac",
    }
    return _FALLBACKS.get(ext, "application/octet-stream")
