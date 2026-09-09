"""Celery task: transcribe an audio file and enqueue action-item extraction.

Flow
----
1. Load the meeting from the database.
2. Mark ``meeting.status = "extracting"`` (acts as "transcription in progress").
3. Download the audio from S3.
4. Call the configured transcription provider.
5. Format the transcript with speaker labels.
6. Persist the transcript text to ``meeting.transcript_text``.
7. Save the raw transcript to S3 and store the key in ``meeting.transcript_ref``.
8. Enqueue ``extract_meeting_task`` so the extraction pipeline continues.
9. On any unhandled exception: revert ``meeting.status = "draft"`` and retry
   with exponential backoff (up to ``max_retries=2``).

The task bridges sync Celery into async SQLAlchemy / provider calls via
``asyncio.run``, mirroring the pattern established in ``extraction_worker.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from celery import Task
from sqlalchemy import select

from app.models import Meeting
from app.services.storage import S3StorageService
from app.services.transcription import (
    format_transcript_with_speakers,
    get_transcription_provider,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_transcription(meeting_id: str, s3_key: str) -> dict[str, Any]:
    """Core async transcription logic.

    Returns a summary dict with ``meeting_id`` and ``transcript_chars``.
    """
    # Local import avoids circular deps at module load time (mirrors extraction_worker)
    from app.database import AsyncSessionLocal

    meeting_uuid = uuid.UUID(meeting_id)
    storage = S3StorageService()

    async with AsyncSessionLocal() as session:
        # ------------------------------------------------------------------
        # 1. Load meeting
        # ------------------------------------------------------------------
        result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_uuid)
        )
        meeting: Meeting | None = result.scalars().first()

        if meeting is None:
            logger.error(
                "transcription_worker.meeting_not_found",
                extra={"meeting_id": meeting_id},
            )
            raise ValueError(f"Meeting {meeting_id} not found")

        # ------------------------------------------------------------------
        # 2. Mark as in-progress
        # ------------------------------------------------------------------
        meeting.status = "extracting"  # reuse existing enum value for "in progress"
        await session.flush()

        logger.info(
            "transcription_worker.start",
            extra={"meeting_id": meeting_id, "s3_key": s3_key},
        )

        # ------------------------------------------------------------------
        # 3. Download audio from S3
        # ------------------------------------------------------------------
        audio_bytes = await storage.download_file(s3_key)
        logger.debug(
            "transcription_worker.audio_downloaded",
            extra={"meeting_id": meeting_id, "bytes": len(audio_bytes)},
        )

        # Derive the original filename from the S3 key
        filename = s3_key.rsplit("/", 1)[-1] if "/" in s3_key else s3_key

        # ------------------------------------------------------------------
        # 4. Transcribe
        # ------------------------------------------------------------------
        provider = get_transcription_provider()
        transcription_result = await provider.transcribe(audio_bytes, filename)

        logger.info(
            "transcription_worker.transcribed",
            extra={
                "meeting_id": meeting_id,
                "provider": type(provider).__name__,
                "segments": len(transcription_result.segments),
                "duration_seconds": transcription_result.duration_seconds,
            },
        )

        # ------------------------------------------------------------------
        # 5. Format transcript
        # ------------------------------------------------------------------
        formatted_transcript = format_transcript_with_speakers(transcription_result)

        # ------------------------------------------------------------------
        # 6. Persist transcript text to the meeting row
        # ------------------------------------------------------------------
        meeting.transcript_text = formatted_transcript

        # ------------------------------------------------------------------
        # 7. Save raw transcript to S3 and store the object key
        # ------------------------------------------------------------------
        transcript_s3_key = await storage.save_transcript(
            text=formatted_transcript,
            tenant_id=str(meeting.tenant_id),
            meeting_id=meeting_id,
        )
        meeting.transcript_ref = transcript_s3_key

        # ------------------------------------------------------------------
        # 8. Commit the updated meeting before enqueueing the next task
        # ------------------------------------------------------------------
        await session.commit()

        logger.info(
            "transcription_worker.saved",
            extra={
                "meeting_id": meeting_id,
                "transcript_ref": transcript_s3_key,
                "chars": len(formatted_transcript),
            },
        )

    # ------------------------------------------------------------------
    # 9. Enqueue extraction task
    # ------------------------------------------------------------------
    from app.workers.extraction_worker import extract_meeting_task  # noqa: PLC0415

    extract_meeting_task.delay(meeting_id)

    logger.info(
        "transcription_worker.extraction_enqueued",
        extra={"meeting_id": meeting_id},
    )

    return {
        "meeting_id": meeting_id,
        "transcript_chars": len(formatted_transcript),
    }


async def _mark_meeting_draft(meeting_id: str) -> None:
    """Revert meeting status to 'draft' on failure (best-effort, never raises)."""
    from app.database import AsyncSessionLocal

    try:
        meeting_uuid = uuid.UUID(meeting_id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Meeting).where(Meeting.id == meeting_uuid)
            )
            meeting: Meeting | None = result.scalars().first()
            if meeting:
                meeting.status = "draft"
                await session.commit()
    except Exception as inner_exc:
        logger.error(
            "transcription_worker.status_rollback_failed",
            extra={"meeting_id": meeting_id, "error": str(inner_exc)},
        )


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.transcribe_audio",
    bind=True,
    max_retries=2,
    time_limit=600,  # hard kill after 10 minutes (matches spec)
    soft_time_limit=540,  # SIGTERM warning at 9 minutes
)
def transcribe_audio_task(self: Task, meeting_id: str, s3_key: str) -> dict[str, Any]:
    """Celery entry-point: transcribe audio and trigger extraction.

    Parameters
    ----------
    meeting_id:
        String UUID of the meeting to process.
    s3_key:
        S3 object key of the uploaded audio file.
    """
    logger.info(
        "transcription_worker.task_start",
        extra={"meeting_id": meeting_id, "s3_key": s3_key, "attempt": self.request.retries + 1},
    )

    try:
        return asyncio.run(_run_transcription(meeting_id, s3_key))

    except Exception as exc:
        logger.exception(
            "transcription_worker.task_error",
            extra={
                "meeting_id": meeting_id,
                "attempt": self.request.retries + 1,
                "error": str(exc),
            },
        )

        # Revert meeting status so the UI does not show a stuck spinner
        asyncio.run(_mark_meeting_draft(meeting_id))

        # Exponential backoff: 60 s → 120 s (max_retries=2, so 2 retries total)
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
