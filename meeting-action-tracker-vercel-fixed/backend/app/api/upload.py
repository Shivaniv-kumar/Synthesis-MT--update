"""Upload API routes — audio file and transcript-file ingestion.

Routes
------
POST /upload/audio
    Accept an audio file (mp3/mp4/wav/m4a/webm/ogg), upload to S3, create a
    Meeting record, and enqueue transcription via Celery.

POST /upload/transcript-file
    Accept a structured transcript (.vtt/.srt/.txt), parse it in-process,
    create a Meeting record with the transcript text inline, and immediately
    enqueue extraction.

GET /upload/status/{meeting_id}
    Return the current status and action-item count for a meeting, tenant-scoped.
"""

from __future__ import annotations

import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_current_user, get_tenant_context
from app.config import get_settings
from app.database import get_db
from app.models import ActionItem, ExtractionRun, Meeting, User
from app.services.file_parser import parse_transcript_file
from app.services.storage import S3StorageService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/upload", tags=["upload"])


# ---------------------------------------------------------------------------
# Inline extraction fallback (used when Celery is unavailable or in dev mode)
# ---------------------------------------------------------------------------

async def _run_extraction_background(meeting_id: str) -> None:
    """Thin wrapper kept for import compatibility; delegates to the worker."""
    from app.workers.extraction_worker import run_extraction_inline  # noqa: PLC0415
    await run_extraction_inline(meeting_id)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUDIO_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
_TRANSCRIPT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

_ALLOWED_AUDIO_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "audio/mpeg",        # mp3
        "audio/mp4",         # m4a, mp4 audio
        "video/mp4",         # mp4 container (may contain audio track)
        "audio/wav",         # wav
        "audio/x-wav",       # wav (alternate MIME)
        "audio/webm",        # webm
        "video/webm",        # webm (browser MediaRecorder default)
        "audio/ogg",         # ogg
        "audio/x-m4a",       # m4a (alternate MIME)
        "application/octet-stream",  # generic binary — accepted, extension checked separately
    }
)

_ALLOWED_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {"mp3", "mp4", "wav", "m4a", "webm", "ogg"}
)

_ALLOWED_TRANSCRIPT_EXTENSIONS: frozenset[str] = frozenset({"txt", "vtt", "srt"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extension(filename: str) -> str:
    """Return the lowercase file extension without the leading dot."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _validate_audio_file(upload: UploadFile) -> None:
    """Raise 422 if the file is not a supported audio type."""
    filename = upload.filename or ""
    ext = _extension(filename)

    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unsupported audio file extension '.{ext}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_AUDIO_EXTENSIONS))}"
            ),
        )

    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in _ALLOWED_AUDIO_CONTENT_TYPES:
        # Extension already validated; just warn — some browsers send
        # application/octet-stream for audio files.
        logger.warning(
            "upload.audio.unexpected_content_type",
            content_type=content_type,
            filename=filename,
        )


def _validate_transcript_file(upload: UploadFile) -> None:
    """Raise 422 if the file is not a supported transcript type."""
    filename = upload.filename or ""
    ext = _extension(filename)

    if ext not in _ALLOWED_TRANSCRIPT_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unsupported transcript file extension '.{ext}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_TRANSCRIPT_EXTENSIONS))}"
            ),
        )


async def _read_bounded(upload: UploadFile, max_bytes: int, label: str) -> bytes:
    """Read upload bytes, enforcing a size limit.

    Parameters
    ----------
    upload:
        FastAPI ``UploadFile`` object.
    max_bytes:
        Maximum number of bytes to accept.
    label:
        Human-readable label for error messages (e.g. ``"audio"``).

    Returns
    -------
    bytes
        Raw file content.

    Raises
    ------
    HTTPException 413
        If the file exceeds *max_bytes*.
    """
    # Read one extra byte beyond the limit so we can detect oversize files
    chunk = await upload.read(max_bytes + 1)
    if len(chunk) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"{label.capitalize()} file exceeds maximum allowed size of "
                f"{max_bytes // (1024 * 1024)} MB."
            ),
        )
    return chunk


async def _get_meeting_or_404(
    meeting_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    workspace_id: uuid.UUID | None = None,
    allow_tenant_admin_fallback: bool = False,
) -> Meeting:
    """Load a meeting; raise 404 if absent or owned by a different tenant."""
    conditions = [Meeting.id == meeting_id, Meeting.tenant_id == tenant_id]
    if workspace_id is not None:
        conditions.append(Meeting.workspace_id == workspace_id)

    result = await db.execute(select(Meeting).where(*conditions))
    meeting: Optional[Meeting] = result.scalar_one_or_none()
    if meeting is None and allow_tenant_admin_fallback:
        fallback_result = await db.execute(
            select(Meeting).where(
                Meeting.id == meeting_id,
                Meeting.tenant_id == tenant_id,
            )
        )
        meeting = fallback_result.scalar_one_or_none()
    if meeting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found",
        )
    return meeting


# ---------------------------------------------------------------------------
# POST /upload/audio
# ---------------------------------------------------------------------------


@router.post(
    "/audio",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an audio file for transcription",
    response_description="Meeting created and transcription enqueued.",
)
async def upload_audio(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Accept an audio file upload, store it in S3, and enqueue transcription.

    The returned ``meeting_id`` can be polled via ``GET /upload/status/{meeting_id}``
    to track progress.

    Supported formats: mp3, mp4, wav, m4a, webm, ogg.  Maximum file size: 500 MB.
    """
    filename = file.filename or "audio_upload"

    # 1. Validate file type
    _validate_audio_file(file)

    # 2. Read file bytes (bounded)
    file_bytes = await _read_bounded(file, _AUDIO_MAX_BYTES, "audio")

    logger.info(
        "upload.audio.received",
        filename=filename,
        bytes=len(file_bytes),
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
    )

    # 3. Create Meeting record (status="draft" initially)
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        created_by=ctx.user_id,
        title=filename,
        source_type="audio",
        status="draft",
    )
    db.add(meeting)
    await db.flush([meeting])

    meeting_id_str = str(meeting.id)

    # 4. Upload audio to S3
    storage = S3StorageService()
    try:
        s3_key = await storage.upload_audio(
            file_bytes=file_bytes,
            filename=filename,
            tenant_id=str(ctx.tenant_id),
            meeting_id=meeting_id_str,
        )
    except Exception as exc:
        logger.error(
            "upload.audio.s3_error",
            meeting_id=meeting_id_str,
            error=str(exc),
        )
        # Clean up the draft meeting so it doesn't orphan
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload audio file to storage. Please try again.",
        ) from exc

    # 5. Store S3 key for audio in transcript_ref (interim; will be replaced by transcript key)
    meeting.transcript_ref = s3_key
    await db.flush([meeting])

    # 6. Commit before enqueueing so the worker can load the meeting
    await db.commit()

    # 7. Enqueue transcription Celery task
    try:
        from app.workers.transcription_worker import transcribe_audio_task  # noqa: PLC0415

        transcribe_audio_task.delay(meeting_id_str, s3_key)

        logger.info(
            "upload.audio.transcription_enqueued",
            meeting_id=meeting_id_str,
            s3_key=s3_key,
            tenant_id=str(ctx.tenant_id),
        )
    except Exception as exc:  # noqa: BLE001
        # Celery not available (dev/test) — log and continue; caller can retry
        logger.warning(
            "upload.audio.celery_unavailable",
            meeting_id=meeting_id_str,
            error=str(exc),
        )

    return {
        "meeting_id": meeting_id_str,
        "status": "transcribing",
        "message": "Transcription started. Poll /upload/status/{meeting_id} for updates.",
    }


# ---------------------------------------------------------------------------
# POST /upload/transcript-file
# ---------------------------------------------------------------------------


@router.post(
    "/transcript-file",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a structured transcript file (.vtt, .srt, .txt)",
    response_description="Meeting created and extraction enqueued.",
)
async def upload_transcript_file(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    project_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Accept a transcript file, parse it, and enqueue action-item extraction.

    Supported formats: .txt, .vtt, .srt.  Maximum file size: 10 MB.

    The transcript text is stored inline in the database (no S3 upload needed
    for text-only files).  The returned ``meeting_id`` can be polled via
    ``GET /upload/status/{meeting_id}``.
    """
    filename = file.filename or "transcript_upload.txt"

    # 1. Validate file type
    _validate_transcript_file(file)

    # 2. Read file bytes (bounded)
    file_bytes = await _read_bounded(file, _TRANSCRIPT_MAX_BYTES, "transcript")

    logger.info(
        "upload.transcript_file.received",
        filename=filename,
        bytes=len(file_bytes),
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
    )

    # 3. Parse transcript file into plain text + segments
    try:
        plain_text, segments = parse_transcript_file(file_bytes, filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error(
            "upload.transcript_file.parse_error",
            filename=filename,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse the transcript file. Please ensure it is valid.",
        ) from exc

    if not plain_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The uploaded transcript file appears to be empty.",
        )

    # Validate project_id if provided: must belong to the same tenant+workspace
    resolved_project_id: Optional[uuid.UUID] = None
    if project_id:
        try:
            resolved_project_id = uuid.UUID(project_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid project_id format.",
            )
        from app.models.project import Project as _Project
        proj_result = await db.execute(
            select(_Project).where(
                _Project.id == resolved_project_id,
                _Project.tenant_id == ctx.tenant_id,
                _Project.workspace_id == ctx.workspace_id,
            )
        )
        if proj_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project not found.",
            )

    # 4. Create Meeting record with inline transcript text
    meeting = Meeting(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        created_by=ctx.user_id,
        title=filename,
        source_type="file",
        status="extracting",
        transcript_text=plain_text,
        project_id=resolved_project_id,
    )
    db.add(meeting)
    await db.flush([meeting])

    meeting_id_str = str(meeting.id)

    # 5. Commit before enqueueing
    await db.commit()

    logger.info(
        "upload.transcript_file.created",
        meeting_id=meeting_id_str,
        chars=len(plain_text),
        segments=len(segments),
        tenant_id=str(ctx.tenant_id),
    )

    # 6. Enqueue extraction task
    settings = get_settings()
    celery_enqueued = False

    if settings.environment != "development":
        # In staging/production: attempt Celery first
        try:
            from app.workers.extraction_worker import extract_meeting_task  # noqa: PLC0415
            extract_meeting_task.delay(meeting_id_str)
            celery_enqueued = True
            logger.info(
                "upload.transcript_file.extraction_enqueued",
                meeting_id=meeting_id_str,
                tenant_id=str(ctx.tenant_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "upload.transcript_file.celery_unavailable",
                meeting_id=meeting_id_str,
                error=str(exc),
            )

    if not celery_enqueued:
        # Dev mode or Celery unavailable — run extraction in the API process
        # after the response is sent so the client isn't blocked.
        background_tasks.add_task(_run_extraction_background, meeting_id_str)
        logger.info(
            "upload.transcript_file.inline_extraction_scheduled",
            meeting_id=meeting_id_str,
        )

    return {
        "meeting_id": meeting_id_str,
        "status": "extracting",
        "message": "Transcript parsed. Extraction is in progress.",
    }


# ---------------------------------------------------------------------------
# GET /upload/status/{meeting_id}
# ---------------------------------------------------------------------------


@router.get(
    "/status/{meeting_id}",
    summary="Poll the status of a meeting (transcription / extraction progress)",
)
async def get_upload_status(
    meeting_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the current status of a meeting and the count of extracted action items.

    The response is tenant-scoped: a 404 is returned rather than 403 if the
    meeting belongs to a different tenant (to avoid enumeration attacks).

    Response shape::

        {
            "meeting_id": "<uuid>",
            "status": "draft" | "extracting" | "extracted" | "reviewed",
            "items_extracted": <int>
        }
    """
    meeting = await _get_meeting_or_404(
        meeting_id,
        ctx.tenant_id,
        db,
        workspace_id=ctx.workspace_id,
        allow_tenant_admin_fallback=current_user.role == "Admin",
    )

    count_result = await db.execute(
        select(func.count()).where(ActionItem.meeting_id == meeting.id)
    )
    items_extracted: int = count_result.scalar_one()

    # When extraction has failed the worker sets status back to "draft" and
    # writes an ExtractionRun with a non-null error.  Surface that error so the
    # client can show a meaningful message immediately instead of waiting for the
    # 5-minute polling timeout.
    extraction_error: Optional[str] = None
    if meeting.status == "draft":
        err_result = await db.execute(
            select(ExtractionRun.error)
            .where(
                ExtractionRun.meeting_id == meeting.id,
                ExtractionRun.error.isnot(None),
            )
            .order_by(ExtractionRun.created_at.desc())
            .limit(1)
        )
        extraction_error = err_result.scalar_one_or_none()

    return {
        "meeting_id": str(meeting.id),
        "status": meeting.status,
        "items_extracted": items_extracted,
        "error": extraction_error,
    }
