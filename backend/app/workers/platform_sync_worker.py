"""Celery task: sync cloud recordings from a connected meeting platform.

Flow
----
1. Load the ``PlatformConfig`` from the database for the given workspace + platform.
2. Build the platform connector via the factory (decrypts OAuth credentials).
3. Fetch recordings from the last 24 hours.
4. For each recording:
   a. Check whether a ``Meeting`` row already exists (by ``transcript_ref`` == recording.id).
   b. If new: create a ``Meeting`` with ``source_type="integration"``.
   c. Download the transcript (VTT) if available; parse it and store as ``transcript_text``.
   d. Fetch attendees and store them on the meeting.
   e. Enqueue the extraction task.
5. Update ``PlatformConfig.last_sync_at``.

Beat schedule
-------------
A wrapper task ``tasks.sync_all_platforms`` runs every hour via Celery Beat.
It queries all active ``PlatformConfig`` rows and enqueues a
``sync_platform_recordings`` task per config.

The sync worker bridges sync Celery into async SQLAlchemy via ``asyncio.run``,
mirroring the pattern established in ``extraction_worker.py``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from celery import Task
from celery.schedules import crontab
from sqlalchemy import select

from app.models.meeting import Meeting
from app.models.platform_config import PlatformConfig
from app.services.platforms.base import PlatformAttendee, PlatformRecording
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VTT parsing helper
# ---------------------------------------------------------------------------


def _parse_vtt(vtt_content: str) -> str:
    """Convert a WebVTT transcript into plain text.

    Strips WEBVTT headers, cue timestamps, and HTML tags, leaving only the
    spoken text lines with their speaker labels when present.

    Parameters
    ----------
    vtt_content:
        Raw VTT content string.

    Returns
    -------
    str
        Plain-text transcript, one utterance per line.
    """
    lines = vtt_content.splitlines()
    text_lines: list[str] = []
    skip_next_blank = False

    for line in lines:
        stripped = line.strip()

        # Skip VTT header
        if stripped.startswith("WEBVTT"):
            continue

        # Skip timestamp lines like "00:00:01.000 --> 00:00:05.000"
        if re.match(r"^\d{2}:\d{2}:\d{2}[\.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[\.,]\d{3}", stripped):
            continue

        # Skip numeric cue identifiers
        if re.match(r"^\d+$", stripped):
            continue

        # Skip blank lines
        if not stripped:
            continue

        # Strip HTML tags (e.g. <v Speaker>text)
        cleaned = re.sub(r"<[^>]+>", "", stripped).strip()
        if cleaned:
            text_lines.append(cleaned)

    return "\n".join(text_lines)


# ---------------------------------------------------------------------------
# Async implementation
# ---------------------------------------------------------------------------


async def _run_platform_sync(workspace_id: str, platform_name: str) -> dict[str, Any]:
    """Core async logic for syncing recordings from a meeting platform.

    Returns
    -------
    dict
        Summary with ``workspace_id``, ``platform``, ``new_meetings``, and
        ``skipped_meetings`` counts.
    """
    from app.database import AsyncSessionLocal
    from app.services.platforms.factory import get_platform
    from app.workers.extraction_worker import extract_meeting_task

    workspace_uuid = uuid.UUID(workspace_id)

    async with AsyncSessionLocal() as session:
        # ------------------------------------------------------------------
        # 1. Load PlatformConfig
        # ------------------------------------------------------------------
        from app.models.platform_config import PlatformType
        try:
            platform_type = PlatformType(platform_name.lower())
        except ValueError:
            raise ValueError(f"Unknown platform: {platform_name}")

        result = await session.execute(
            select(PlatformConfig).where(
                PlatformConfig.workspace_id == workspace_uuid,
                PlatformConfig.platform == platform_type,
                PlatformConfig.is_active.is_(True),
            )
        )
        config: PlatformConfig | None = result.scalar_one_or_none()

        if config is None:
            logger.warning(
                "platform_sync.config_not_found",
                workspace_id=workspace_id,
                platform=platform_name,
            )
            return {
                "workspace_id": workspace_id,
                "platform": platform_name,
                "new_meetings": 0,
                "skipped_meetings": 0,
                "error": "PlatformConfig not found or inactive",
            }

        # ------------------------------------------------------------------
        # 2. Build the platform connector
        # ------------------------------------------------------------------
        try:
            platform = get_platform(platform_name, config)
        except Exception as exc:
            logger.error(
                "platform_sync.build_platform_failed",
                workspace_id=workspace_id,
                platform=platform_name,
                error=str(exc),
            )
            raise

        # ------------------------------------------------------------------
        # 3. Fetch recordings for the last 24 hours
        # ------------------------------------------------------------------
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(hours=24)

        recordings: list[PlatformRecording] = await platform.list_recordings(
            since=since, until=now
        )

        logger.info(
            "platform_sync.recordings_fetched",
            workspace_id=workspace_id,
            platform=platform_name,
            count=len(recordings),
        )

        new_meetings = 0
        skipped_meetings = 0

        for recording in recordings:
            # ------------------------------------------------------------------
            # 4a. Check if a Meeting with this recording already exists
            # ------------------------------------------------------------------
            existing_result = await session.execute(
                select(Meeting).where(
                    Meeting.workspace_id == workspace_uuid,
                    Meeting.transcript_ref == recording.id,
                )
            )
            existing: Meeting | None = existing_result.scalar_one_or_none()

            if existing is not None:
                skipped_meetings += 1
                logger.debug(
                    "platform_sync.recording_already_imported",
                    recording_id=recording.id,
                    meeting_id=str(existing.id),
                )
                continue

            # ------------------------------------------------------------------
            # 4b. Create a new Meeting row
            # ------------------------------------------------------------------
            meeting_id = uuid.uuid4()
            meeting = Meeting(
                id=meeting_id,
                workspace_id=workspace_uuid,
                tenant_id=config.tenant_id,
                title=recording.title,
                source_type="integration",
                occurred_at=recording.started_at,
                status="draft",
                # Store the recording ID so we can detect duplicates on future syncs
                transcript_ref=recording.id,
            )

            # ------------------------------------------------------------------
            # 4c. Download and parse transcript (if available)
            # ------------------------------------------------------------------
            try:
                raw_transcript = await platform.get_transcript(recording.meeting_id)
                if raw_transcript:
                    parsed_transcript = _parse_vtt(raw_transcript)
                    if parsed_transcript:
                        meeting.transcript_text = parsed_transcript
                        logger.debug(
                            "platform_sync.transcript_stored",
                            recording_id=recording.id,
                            chars=len(parsed_transcript),
                        )
            except Exception as exc:
                logger.warning(
                    "platform_sync.transcript_fetch_failed",
                    recording_id=recording.id,
                    error=str(exc),
                )

            # ------------------------------------------------------------------
            # 4d. Fetch attendees
            # ------------------------------------------------------------------
            try:
                attendees: list[PlatformAttendee] = await platform.get_attendees(
                    recording.meeting_id
                )
                if attendees:
                    # Store attendee names/emails as a flat list of strings
                    meeting.attendees = [
                        f"{a.name} <{a.email}>" if a.email else a.name
                        for a in attendees
                    ]
            except Exception as exc:
                logger.warning(
                    "platform_sync.attendees_fetch_failed",
                    recording_id=recording.id,
                    error=str(exc),
                )

            session.add(meeting)
            new_meetings += 1

        # ------------------------------------------------------------------
        # 5. Update last_sync_at and commit
        # ------------------------------------------------------------------
        config.last_sync_at = now
        await session.commit()

        logger.info(
            "platform_sync.complete",
            workspace_id=workspace_id,
            platform=platform_name,
            new_meetings=new_meetings,
            skipped_meetings=skipped_meetings,
        )

    # ------------------------------------------------------------------
    # Enqueue extraction for new meetings (outside the DB session)
    # ------------------------------------------------------------------
    # Re-query meeting IDs for new meetings so we can enqueue extraction
    # Note: We only enqueue extraction when a transcript is available.
    async with AsyncSessionLocal() as session:
        new_meeting_result = await session.execute(
            select(Meeting).where(
                Meeting.workspace_id == workspace_uuid,
                Meeting.source_type == "integration",
                Meeting.status == "draft",
                Meeting.transcript_text.isnot(None),
            )
        )
        new_meetings_with_transcripts = new_meeting_result.scalars().all()

        for meeting in new_meetings_with_transcripts:
            extract_meeting_task.delay(str(meeting.id))
            logger.info(
                "platform_sync.extraction_enqueued",
                meeting_id=str(meeting.id),
                platform=platform_name,
            )

    return {
        "workspace_id": workspace_id,
        "platform": platform_name,
        "new_meetings": new_meetings,
        "skipped_meetings": skipped_meetings,
    }


# ---------------------------------------------------------------------------
# Async sync-all implementation
# ---------------------------------------------------------------------------


async def _run_sync_all_platforms() -> dict[str, Any]:
    """Query all active PlatformConfigs and enqueue a sync task per config."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PlatformConfig).where(
                PlatformConfig.is_active.is_(True),
                PlatformConfig.sync_enabled.is_(True),
            )
        )
        configs = result.scalars().all()

    enqueued = 0
    for config in configs:
        sync_platform_recordings.delay(
            str(config.workspace_id), config.platform.value
        )
        enqueued += 1
        logger.info(
            "platform_sync.scheduled_sync_enqueued",
            workspace_id=str(config.workspace_id),
            platform=config.platform.value,
        )

    logger.info("platform_sync.all_enqueued", count=enqueued)
    return {"enqueued": enqueued}


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.sync_platform_recordings",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def sync_platform_recordings(
    self: Task, workspace_id: str, platform_name: str
) -> dict[str, Any]:
    """Celery task: sync recordings from a single meeting platform.

    Parameters
    ----------
    workspace_id:
        String UUID of the workspace to sync.
    platform_name:
        Platform identifier: ``"zoom"``, ``"teams"``, or ``"google_meet"``.
    """
    logger.info(
        "platform_sync.task_start",
        extra={"workspace_id": workspace_id, "platform": platform_name},
    )
    try:
        return asyncio.run(_run_platform_sync(workspace_id, platform_name))
    except Exception as exc:
        logger.exception(
            "platform_sync.task_error",
            extra={
                "workspace_id": workspace_id,
                "platform": platform_name,
                "attempt": self.request.retries + 1,
                "error": str(exc),
            },
        )
        # Exponential backoff: 120s, 240s, 480s
        countdown = 120 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@celery_app.task(
    name="tasks.sync_all_platforms",
    bind=True,
)
def sync_all_platforms(self: Task) -> dict[str, Any]:
    """Wrapper task that enqueues per-platform sync jobs for all active configs.

    Called by Celery Beat every hour (see beat_schedule in celery_app.py).
    """
    logger.info("platform_sync.sync_all_start")
    try:
        return asyncio.run(_run_sync_all_platforms())
    except Exception as exc:
        logger.exception(
            "platform_sync.sync_all_error",
            extra={"error": str(exc)},
        )
        raise


# ---------------------------------------------------------------------------
# Beat schedule registration
# ---------------------------------------------------------------------------
# Update the Celery Beat schedule to add the hourly sync job.
# This modifies the celery_app's beat_schedule in place so that Celery Beat
# picks it up when the worker starts.

celery_app.conf.beat_schedule.update(
    {
        "sync-all-platforms": {
            "task": "tasks.sync_all_platforms",
            "schedule": crontab(minute=0),  # top of every hour
        },
    }
)

# Add platform sync queue to task routes
celery_app.conf.task_routes.update(
    {
        "tasks.sync_platform_recordings": {"queue": "platform_sync"},
        "tasks.sync_all_platforms": {"queue": "platform_sync"},
    }
)
