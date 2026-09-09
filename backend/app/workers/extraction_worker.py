"""Celery task: extract action items from a meeting transcript.

The task is a synchronous Celery task that bridges into async code via
``asyncio.run``.  This keeps the Celery worker process free of an always-on
event loop while still allowing us to use SQLAlchemy's async session and the
async Anthropic client.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from celery import Task
from sqlalchemy import select

from app.models import ActionItem, ExtractionRun, Meeting
from app.models.knowledge_entry import KnowledgeEntry
from app.services.date_utils import normalize_due_date
from app.services.extraction_service import ExtractionService
from app.services.knowledge_service import KnowledgeExtractionService
from app.services.owner_resolution import OwnerResolutionService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Confidence below which an item is flagged for human review
_REVIEW_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Inline fallback — used by FastAPI BackgroundTasks when Celery is unavailable
# ---------------------------------------------------------------------------

async def run_extraction_inline(meeting_id: str) -> None:
    """Run extraction in the API process (dev / Celery-unavailable fallback).

    Called via FastAPI ``BackgroundTasks`` so the HTTP response is sent first
    and the client can start polling while extraction runs.
    """
    try:
        await _run_extraction(meeting_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "extraction_worker.inline_error",
            extra={"meeting_id": meeting_id, "error": str(exc)},
        )
        await _mark_meeting_error(meeting_id, str(exc))


# ---------------------------------------------------------------------------
# Async implementation — called from the synchronous Celery task below
# ---------------------------------------------------------------------------


async def _run_extraction(meeting_id: str) -> dict[str, Any]:
    """Core async logic for extraction.

    Returns a summary dict with ``meeting_id``, ``items_inserted``, and
    ``items_skipped`` counts (for logging / result storage).
    """
    from app.database import AsyncSessionLocal  # local import avoids circular deps at module load

    meeting_uuid = uuid.UUID(meeting_id)

    async with AsyncSessionLocal() as session:
        # ------------------------------------------------------------------
        # 1. Load meeting
        # ------------------------------------------------------------------
        result = await session.execute(
            select(Meeting).where(Meeting.id == meeting_uuid)
        )
        meeting: Meeting | None = result.scalars().first()

        if meeting is None:
            logger.error("extraction_worker.meeting_not_found", extra={"meeting_id": meeting_id})
            raise ValueError(f"Meeting {meeting_id} not found")

        # ------------------------------------------------------------------
        # 2. Mark as extracting
        # ------------------------------------------------------------------
        meeting.status = "extracting"
        await session.flush()

        # ------------------------------------------------------------------
        # 3. Retrieve transcript text
        # transcript_text column holds the parsed/formatted transcript when the
        # audio transcription or file-parse pipeline has run.  For meetings
        # created via the legacy paste / create-meeting endpoint the text was
        # stored in transcript_ref directly, so we fall back to that.
        # ------------------------------------------------------------------
        transcript_text: str = meeting.transcript_text or meeting.transcript_ref or ""
        if not transcript_text:
            logger.warning(
                "extraction_worker.empty_transcript", extra={"meeting_id": meeting_id}
            )
            meeting.status = "extracted"
            await session.commit()
            return {"meeting_id": meeting_id, "items_inserted": 0, "items_skipped": 0}

        attendees: list[str] = meeting.attendees or []
        occurred_at: datetime | None = meeting.occurred_at

        # ------------------------------------------------------------------
        # 4. Call extraction service (track latency for ExtractionRun)
        # ------------------------------------------------------------------
        service = ExtractionService()
        import time as _time
        _t0 = _time.monotonic()
        raw_items = await service.extract(
            transcript_text=transcript_text,
            attendees=attendees,
            occurred_at=occurred_at,
        )
        _latency_ms = int((_time.monotonic() - _t0) * 1000)

        # ------------------------------------------------------------------
        # 5. Resolve owners
        # ------------------------------------------------------------------
        resolver = OwnerResolutionService(session=session, tenant_id=meeting.tenant_id)

        # ------------------------------------------------------------------
        # 6. Dedup against existing open items in the same workspace
        # ------------------------------------------------------------------
        existing_result = await session.execute(
            select(ActionItem.task).where(
                ActionItem.workspace_id == meeting.workspace_id,
                ActionItem.status != "Done",
            )
        )
        existing_tasks: set[str] = {row[0].lower().strip() for row in existing_result.all()}

        # ------------------------------------------------------------------
        # 7. Build ActionItem ORM objects
        # ------------------------------------------------------------------
        now = datetime.now(tz=timezone.utc)
        items_to_insert: list[ActionItem] = []
        items_skipped = 0

        for raw in raw_items:
            task_key = raw.task.lower().strip()
            if task_key in existing_tasks:
                items_skipped += 1
                logger.debug(
                    "extraction_worker.item_deduped",
                    extra={"task": raw.task[:80], "meeting_id": meeting_id},
                )
                continue

            owner_user_id, owner_label = await resolver.resolve(
                raw_owner=raw.owner,
                attendees=attendees,
            )

            ref_date = occurred_at.date() if occurred_at else None
            due_date = normalize_due_date(raw.due, reference_date=ref_date)

            # Normalise priority — default to Medium if the model returns garbage
            priority = raw.priority if raw.priority in ("High", "Medium", "Low") else "Medium"

            action_item = ActionItem(
                id=uuid.uuid4(),
                meeting_id=meeting_uuid,
                tenant_id=meeting.tenant_id,
                workspace_id=meeting.workspace_id,
                task=raw.task,
                owner_user_id=owner_user_id,
                owner_label=owner_label,
                priority=priority,
                due_date=due_date,
                due_text=raw.due,
                status="Open",
                context=raw.context,
                confidence=raw.confidence,
                needs_review=raw.confidence < _REVIEW_THRESHOLD,
                created_at=now,
                updated_at=now,
            )
            items_to_insert.append(action_item)
            existing_tasks.add(task_key)  # prevent duplicates within same batch

        # ------------------------------------------------------------------
        # 8. Bulk insert action items
        # ------------------------------------------------------------------
        session.add_all(items_to_insert)

        # ------------------------------------------------------------------
        # 9. Update meeting status
        # ------------------------------------------------------------------
        meeting.status = "extracted"

        # ------------------------------------------------------------------
        # 9b. Knowledge extraction — runs after action items, non-blocking.
        #     A failure here logs a warning but does NOT prevent action items
        #     or meeting status from being committed.
        # ------------------------------------------------------------------
        knowledge_entries: list[KnowledgeEntry] = []
        try:
            knowledge_service = KnowledgeExtractionService()
            knowledge_items = await knowledge_service.extract(
                transcript_text=transcript_text,
                attendees=attendees,
                occurred_at=occurred_at,
            )
            for ki in knowledge_items:
                knowledge_entries.append(
                    KnowledgeEntry(
                        id=uuid.uuid4(),
                        meeting_id=meeting_uuid,
                        project_id=getattr(meeting, "project_id", None),
                        tenant_id=meeting.tenant_id,
                        workspace_id=meeting.workspace_id,
                        category=ki.category,
                        content=ki.content,
                        source_quote=ki.source_quote or None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if knowledge_entries:
                session.add_all(knowledge_entries)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "extraction_worker.knowledge_extraction_failed",
                extra={"meeting_id": meeting_id, "error": str(exc)},
            )

        # ------------------------------------------------------------------
        # 10. H7: Record the extraction run for auditing and cost tracking
        # ------------------------------------------------------------------
        from app.config import get_settings as _get_settings
        _settings = _get_settings()
        extraction_run = ExtractionRun(
            id=uuid.uuid4(),
            meeting_id=meeting_uuid,
            model=_settings.extraction_model,
            prompt_version="v1",
            input_tokens=0,   # detailed token tracking requires service changes
            output_tokens=0,
            latency_ms=_latency_ms,
            item_count=len(items_to_insert),
            error=None,
        )
        session.add(extraction_run)

        await session.commit()

        logger.info(
            "extraction_worker.complete",
            extra={
                "meeting_id": meeting_id,
                "items_inserted": len(items_to_insert),
                "items_skipped": items_skipped,
                "knowledge_entries": len(knowledge_entries),
                "latency_ms": _latency_ms,
            },
        )
        return {
            "meeting_id": meeting_id,
            "items_inserted": len(items_to_insert),
            "items_skipped": items_skipped,
            "knowledge_entries": len(knowledge_entries),
        }


async def _mark_meeting_error(meeting_id: str, error_message: str) -> None:
    """Set meeting status back to 'draft' and record an error ExtractionRun (best-effort).

    H8: Always create an ExtractionRun record so failures are visible in the audit trail.
    """
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

                # H8: Record the failed run
                from app.config import get_settings as _get_settings
                _settings = _get_settings()
                error_run = ExtractionRun(
                    id=uuid.uuid4(),
                    meeting_id=meeting_uuid,
                    model=_settings.extraction_model,
                    prompt_version="v1",
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                    item_count=0,
                    error=error_message[:1000],  # truncate to column length
                )
                session.add(error_run)
                await session.commit()
    except Exception as inner_exc:
        logger.error(
            "extraction_worker.status_rollback_failed",
            extra={"meeting_id": meeting_id, "error": str(inner_exc)},
        )


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.extract_meeting",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # seconds (overridden by exponential backoff below)
)
def extract_meeting_task(self: Task, meeting_id: str) -> dict[str, Any]:
    """Celery entry-point: extract action items from a meeting.

    Parameters
    ----------
    meeting_id:
        String UUID of the meeting to process.
    """
    logger.info("extraction_worker.start", extra={"meeting_id": meeting_id})
    try:
        return asyncio.run(_run_extraction(meeting_id))
    except Exception as exc:
        logger.exception(
            "extraction_worker.error",
            extra={"meeting_id": meeting_id, "attempt": self.request.retries + 1},
        )
        # H8: Revert meeting status to draft and record the error run
        asyncio.run(_mark_meeting_error(meeting_id, str(exc)))

        # Exponential backoff: 60s, 120s, 240s
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
