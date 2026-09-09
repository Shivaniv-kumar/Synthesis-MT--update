"""Data retention and GDPR erasure / export service.

RetentionService implements three operations:

1. ``apply_retention_policy`` — scheduled daily job that deletes or
   anonymises data that has aged past the workspace retention thresholds.

2. ``process_erasure_request`` — GDPR right-to-erasure: anonymises all
   personal data for a given data subject and removes their workspace
   memberships and notification history.

3. ``export_user_data`` — GDPR right-of-access: collects all personal data
   we hold about a user and returns it as a Python dict suitable for JSON
   serialisation.

All public methods accept an ``AsyncSession`` from the caller so they
participate in the caller's transaction scope.  Call ``db.commit()`` after
``process_erasure_request`` — or let the FastAPI ``get_db`` dependency handle
it at the end of the request.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gdpr import DataRetentionConfig, GDPRAuditEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default retention thresholds (used when no DataRetentionConfig row exists)
# ---------------------------------------------------------------------------

_DEFAULT_TRANSCRIPT_DAYS = 365
_DEFAULT_ITEM_DAYS = 730
_DEFAULT_AUDIO_DAYS = 90


# ---------------------------------------------------------------------------
# RetentionService
# ---------------------------------------------------------------------------


class RetentionService:
    """Centralised implementation of data-retention and GDPR operations."""

    # ------------------------------------------------------------------
    # apply_retention_policy
    # ------------------------------------------------------------------

    async def apply_retention_policy(
        self,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, int]:
        """Apply the configured retention policy for a workspace.

        Steps
        -----
        1. Load ``DataRetentionConfig`` for the workspace (or use defaults).
        2. Clear ``transcript_text`` and delete the S3 transcript object on
           meetings older than ``transcript_retention_days``.
        3. Delete ``ActionItem`` rows on meetings older than
           ``item_retention_days``.
        4. Delete ``Meeting`` rows that are older than the maximum of the two
           thresholds *and* have no remaining action items.
        5. For deleted meetings that had audio (``transcript_ref`` ending with
           ``/audio/``), delete the S3 audio object.
        6. Write a ``GDPRAuditEvent`` recording the run.

        Returns
        -------
        dict with keys ``transcripts_cleared``, ``items_deleted``,
        ``meetings_deleted``.
        """
        # Avoid circular import at module level
        from app.models.action_item import ActionItem
        from app.models.meeting import Meeting
        from app.services.storage import get_storage_service

        storage = get_storage_service()

        # ------------------------------------------------------------------
        # 1. Load retention config (or use defaults)
        # ------------------------------------------------------------------
        config_result = await db.execute(
            select(DataRetentionConfig).where(
                DataRetentionConfig.workspace_id == workspace_id,
                DataRetentionConfig.tenant_id == tenant_id,
            )
        )
        config: Optional[DataRetentionConfig] = config_result.scalar_one_or_none()

        transcript_days = (
            config.transcript_retention_days if config else _DEFAULT_TRANSCRIPT_DAYS
        )
        item_days = config.item_retention_days if config else _DEFAULT_ITEM_DAYS
        audio_days = config.audio_retention_days if config else _DEFAULT_AUDIO_DAYS

        now = datetime.now(tz=timezone.utc)
        transcript_cutoff = now - timedelta(days=transcript_days)
        item_cutoff = now - timedelta(days=item_days)
        audio_cutoff = now - timedelta(days=audio_days)
        meeting_cutoff = now - timedelta(days=max(transcript_days, item_days))

        transcripts_cleared = 0
        items_deleted = 0
        meetings_deleted = 0

        # ------------------------------------------------------------------
        # 2. Clear transcript_text and delete S3 transcript objects
        # ------------------------------------------------------------------
        old_transcript_result = await db.execute(
            select(Meeting).where(
                Meeting.workspace_id == workspace_id,
                Meeting.tenant_id == tenant_id,
                Meeting.created_at < transcript_cutoff,
                Meeting.transcript_text.is_not(None),
            )
        )
        old_transcript_meetings: list[Meeting] = list(
            old_transcript_result.scalars().all()
        )

        for meeting in old_transcript_meetings:
            # Delete S3 transcript object if there was a transcript_ref pointing
            # to a transcript file (not an embedded short text)
            if meeting.transcript_ref and "/transcript" in meeting.transcript_ref:
                try:
                    await storage.delete_file(meeting.transcript_ref)
                    logger.info(
                        "retention.s3_transcript_deleted",
                        meeting_id=str(meeting.id),
                        key=meeting.transcript_ref,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "retention.s3_transcript_delete_failed",
                        meeting_id=str(meeting.id),
                        key=meeting.transcript_ref,
                        error=str(exc),
                    )

            meeting.transcript_text = None
            meeting.transcript_ref = None
            transcripts_cleared += 1

        await db.flush()

        # ------------------------------------------------------------------
        # 3. Delete action items on old meetings
        # ------------------------------------------------------------------
        old_items_result = await db.execute(
            select(ActionItem).where(
                ActionItem.workspace_id == workspace_id,
                ActionItem.tenant_id == tenant_id,
            ).join(Meeting, ActionItem.meeting_id == Meeting.id).where(
                Meeting.created_at < item_cutoff,
            )
        )
        old_items: list[ActionItem] = list(old_items_result.scalars().all())
        for item in old_items:
            await db.delete(item)
        items_deleted = len(old_items)
        await db.flush()

        # ------------------------------------------------------------------
        # 4. Delete meeting records older than max(transcript_days, item_days)
        # ------------------------------------------------------------------
        old_meetings_result = await db.execute(
            select(Meeting).where(
                Meeting.workspace_id == workspace_id,
                Meeting.tenant_id == tenant_id,
                Meeting.created_at < meeting_cutoff,
            )
        )
        old_meetings: list[Meeting] = list(old_meetings_result.scalars().all())

        for meeting in old_meetings:
            # 5. Delete S3 audio object if the meeting had audio
            if meeting.transcript_ref and "/audio/" in meeting.transcript_ref:
                try:
                    await storage.delete_file(meeting.transcript_ref)
                    logger.info(
                        "retention.s3_audio_deleted",
                        meeting_id=str(meeting.id),
                        key=meeting.transcript_ref,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "retention.s3_audio_delete_failed",
                        meeting_id=str(meeting.id),
                        key=meeting.transcript_ref,
                        error=str(exc),
                    )

            await db.delete(meeting)

        meetings_deleted = len(old_meetings)
        await db.flush()

        # ------------------------------------------------------------------
        # 6. Write GDPR audit event for the retention run
        # ------------------------------------------------------------------
        # Use a synthetic "system" subject UUID (nil UUID) for system-triggered
        # events that are not tied to a specific data subject.
        system_subject = uuid.UUID(int=0)

        event = GDPRAuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_type="access",
            actor_user_id=None,  # system-triggered
            subject_user_id=system_subject,
            details={
                "workspace_id": str(workspace_id),
                "retention_applied": transcripts_cleared + items_deleted + meetings_deleted,
                "transcripts_cleared": transcripts_cleared,
                "items_deleted": items_deleted,
                "meetings_deleted": meetings_deleted,
                "transcript_cutoff_days": transcript_days,
                "item_cutoff_days": item_days,
                "audio_cutoff_days": audio_days,
            },
        )
        db.add(event)
        await db.flush()

        logger.info(
            "retention.policy_applied",
            workspace_id=str(workspace_id),
            tenant_id=str(tenant_id),
            transcripts_cleared=transcripts_cleared,
            items_deleted=items_deleted,
            meetings_deleted=meetings_deleted,
        )

        return {
            "transcripts_cleared": transcripts_cleared,
            "items_deleted": items_deleted,
            "meetings_deleted": meetings_deleted,
        }

    # ------------------------------------------------------------------
    # process_erasure_request
    # ------------------------------------------------------------------

    async def process_erasure_request(
        self,
        subject_user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> None:
        """GDPR right-to-erasure: anonymise and purge all data for a user.

        Steps
        -----
        a. Anonymise ``User`` record — replace PII fields with erased placeholders.
        b. Anonymise ``ActionItem`` ownership where ``owner_user_id = subject``.
        c. Delete ``WorkspaceMember`` rows for the user.
        d. Delete ``NotificationLog`` rows for the user.
        e. Write ``GDPRAuditEvent`` of type ``erasure``.
        f. Commit.

        The caller is responsible for committing the session if not using the
        FastAPI ``get_db`` dependency which auto-commits on success.
        """
        from app.models.action_item import ActionItem
        from app.models.user import User
        from app.models.workspace import WorkspaceMember

        # ------------------------------------------------------------------
        # a. Anonymise User record
        # ------------------------------------------------------------------
        user_result = await db.execute(
            select(User).where(
                User.id == subject_user_id,
                User.tenant_id == tenant_id,
            )
        )
        user: Optional[User] = user_result.scalar_one_or_none()

        if user is not None:
            user.email = "[deleted@erased.local]"
            user.display_name = "[Deleted User]"
            user.sso_subject = None
            user.hashed_password = None
            user.is_active = False
            await db.flush()

            logger.info(
                "gdpr.erasure.user_anonymised",
                subject_user_id=str(subject_user_id),
                tenant_id=str(tenant_id),
            )
        else:
            logger.warning(
                "gdpr.erasure.user_not_found",
                subject_user_id=str(subject_user_id),
                tenant_id=str(tenant_id),
            )

        # ------------------------------------------------------------------
        # b. Anonymise ActionItem ownership
        # ------------------------------------------------------------------
        items_result = await db.execute(
            select(ActionItem).where(
                ActionItem.owner_user_id == subject_user_id,
                ActionItem.tenant_id == tenant_id,
            )
        )
        owned_items: list[ActionItem] = list(items_result.scalars().all())
        for item in owned_items:
            item.owner_user_id = None
            item.owner_label = "[Deleted User]"

        if owned_items:
            await db.flush()
            logger.info(
                "gdpr.erasure.items_anonymised",
                subject_user_id=str(subject_user_id),
                count=len(owned_items),
            )

        # ------------------------------------------------------------------
        # c. Delete WorkspaceMember rows
        # ------------------------------------------------------------------
        members_result = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == subject_user_id,
            )
        )
        memberships: list[WorkspaceMember] = list(members_result.scalars().all())
        for membership in memberships:
            await db.delete(membership)

        if memberships:
            await db.flush()
            logger.info(
                "gdpr.erasure.memberships_deleted",
                subject_user_id=str(subject_user_id),
                count=len(memberships),
            )

        # ------------------------------------------------------------------
        # d. Delete NotificationLog rows (conditional import to avoid circular)
        # ------------------------------------------------------------------
        try:
            from app.models.notification import NotificationLog  # noqa: PLC0415

            notif_result = await db.execute(
                select(NotificationLog).where(
                    NotificationLog.user_id == subject_user_id,
                )
            )
            notif_logs: list[NotificationLog] = list(notif_result.scalars().all())
            for log_entry in notif_logs:
                await db.delete(log_entry)

            if notif_logs:
                await db.flush()
                logger.info(
                    "gdpr.erasure.notification_logs_deleted",
                    subject_user_id=str(subject_user_id),
                    count=len(notif_logs),
                )
        except ImportError:
            logger.warning(
                "gdpr.erasure.notification_model_unavailable",
                subject_user_id=str(subject_user_id),
            )

        # ------------------------------------------------------------------
        # e. Write GDPRAuditEvent
        # ------------------------------------------------------------------
        event = GDPRAuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_type="erasure",
            actor_user_id=actor_user_id,
            subject_user_id=subject_user_id,
            details={
                "items_anonymised": len(owned_items),
                "memberships_deleted": len(memberships),
            },
        )
        db.add(event)
        await db.flush()

        logger.info(
            "gdpr.erasure.complete",
            subject_user_id=str(subject_user_id),
            actor_user_id=str(actor_user_id),
        )

        # ------------------------------------------------------------------
        # f. Commit
        # ------------------------------------------------------------------
        await db.commit()

    # ------------------------------------------------------------------
    # export_user_data
    # ------------------------------------------------------------------

    async def export_user_data(
        self,
        subject_user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """GDPR right-of-access: collect all personal data held about a user.

        Returns a dict that can be serialised directly to JSON.  The returned
        dict excludes ``hashed_password``.

        A ``GDPRAuditEvent`` of type ``export`` is written within the same
        session (flushed but not committed — the caller or ``get_db`` commits).
        """
        from app.models.action_item import ActionItem
        from app.models.audit_log import AuditLog
        from app.models.user import User

        export: dict[str, Any] = {}

        # ------------------------------------------------------------------
        # User record
        # ------------------------------------------------------------------
        user_result = await db.execute(
            select(User).where(
                User.id == subject_user_id,
                User.tenant_id == tenant_id,
            )
        )
        user: Optional[User] = user_result.scalar_one_or_none()

        if user is not None:
            export["user"] = {
                "id": str(user.id),
                "tenant_id": str(user.tenant_id),
                "email": user.email,
                "display_name": user.display_name,
                "sso_subject": user.sso_subject,
                "role": getattr(user, "role", None),
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if hasattr(user, "created_at") and user.created_at else None,
                "updated_at": user.updated_at.isoformat() if hasattr(user, "updated_at") and user.updated_at else None,
            }
        else:
            export["user"] = None

        # ------------------------------------------------------------------
        # ActionItems created by or owned by the user
        # ------------------------------------------------------------------
        items_result = await db.execute(
            select(ActionItem).where(
                ActionItem.tenant_id == tenant_id,
            ).where(
                (ActionItem.owner_user_id == subject_user_id)
                | (ActionItem.created_by == subject_user_id)
            )
        )
        action_items: list[ActionItem] = list(items_result.scalars().all())

        export["action_items"] = [
            {
                "id": str(item.id),
                "meeting_id": str(item.meeting_id),
                "task": item.task,
                "owner_label": item.owner_label,
                "owner_user_id": str(item.owner_user_id) if item.owner_user_id else None,
                "priority": item.priority,
                "due_date": item.due_date.isoformat() if item.due_date else None,
                "due_text": item.due_text,
                "status": item.status,
                "context": item.context,
                "confidence": item.confidence,
                "created_by": str(item.created_by) if item.created_by else None,
                "created_at": item.created_at.isoformat() if hasattr(item, "created_at") and item.created_at else None,
            }
            for item in action_items
        ]

        # ------------------------------------------------------------------
        # AuditLog entries where actor_user_id = subject
        # ------------------------------------------------------------------
        audit_result = await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.actor_user_id == subject_user_id,
            ).order_by(AuditLog.created_at.desc())
        )
        audit_entries: list[AuditLog] = list(audit_result.scalars().all())

        export["audit_log"] = [
            {
                "id": str(entry.id),
                "entity_type": entry.entity_type,
                "entity_id": str(entry.entity_id),
                "action": entry.action,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
            }
            for entry in audit_entries
        ]

        # ------------------------------------------------------------------
        # NotificationLog entries for the user
        # ------------------------------------------------------------------
        try:
            from app.models.notification import NotificationLog  # noqa: PLC0415

            notif_result = await db.execute(
                select(NotificationLog).where(
                    NotificationLog.user_id == subject_user_id,
                ).order_by(NotificationLog.sent_at.desc())
            )
            notif_entries: list[NotificationLog] = list(notif_result.scalars().all())

            export["notification_log"] = [
                {
                    "id": str(entry.id),
                    "item_id": str(entry.item_id) if entry.item_id else None,
                    "rule_type": entry.rule_type,
                    "channel": entry.channel,
                    "status": entry.status,
                    "sent_at": entry.sent_at.isoformat() if entry.sent_at else None,
                    "error": entry.error,
                }
                for entry in notif_entries
            ]
        except ImportError:
            export["notification_log"] = []
            logger.warning(
                "gdpr.export.notification_model_unavailable",
                subject_user_id=str(subject_user_id),
            )

        # ------------------------------------------------------------------
        # Write GDPRAuditEvent for the export itself
        # ------------------------------------------------------------------
        event = GDPRAuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_type="export",
            actor_user_id=subject_user_id,  # the requesting user
            subject_user_id=subject_user_id,
            details={
                "action_items_count": len(action_items),
                "audit_entries_count": len(audit_entries),
            },
        )
        db.add(event)
        await db.flush()

        logger.info(
            "gdpr.export.complete",
            subject_user_id=str(subject_user_id),
            tenant_id=str(tenant_id),
            action_items=len(action_items),
            audit_entries=len(audit_entries),
        )

        return export
