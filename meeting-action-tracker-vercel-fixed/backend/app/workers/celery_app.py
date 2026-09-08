"""Celery application configuration for the Meeting Action Tracker.

Broker and result backend are both Redis (configured via settings.REDIS_URL).
Task routing sends work to dedicated queues so each queue can be scaled
independently:

  - extraction     — LLM action-item extraction (CPU + network bound)
  - transcription  — Audio-to-text conversion (GPU / external API bound)
  - notification   — Email/Slack/Teams dispatch (network bound)
  - sync           — Platform and PM-integration background sync (network bound)
  - compliance     — Scheduled GDPR retention jobs (I/O bound)
  - default        — Catch-all for unrouted tasks

The beat_schedule section is the **single source of truth** for all scheduled
tasks.  Individual agents must NOT add their own beat schedules — add entries
here instead.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Create the Celery app
# ---------------------------------------------------------------------------

celery_app = Celery(
    "meeting_action_tracker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.extraction_worker",
        "app.workers.transcription_worker",
        "app.workers.notification_worker",
        "app.workers.platform_sync_worker",
        "app.workers.integration_sync_worker",
        "app.workers.compliance_worker",
    ],
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Result expiry — keep results for 24 h
    result_expires=86_400,

    # Task routing — explicit queues per concern
    task_routes={
        # Extraction — name matches @celery_app.task(name="tasks.extract_meeting")
        "tasks.extract_meeting": {"queue": "extraction"},
        # Transcription
        "app.workers.transcription_worker.transcribe_audio": {"queue": "transcription"},
        # Notifications
        "app.workers.notification_worker.send_assignment_notification": {"queue": "notification"},
        "app.workers.notification_worker.send_due_soon_notifications": {"queue": "notification"},
        "app.workers.notification_worker.send_overdue_notifications": {"queue": "notification"},
        "app.workers.notification_worker.send_weekly_digest": {"queue": "notification"},
        # Platform sync
        "app.workers.platform_sync_worker.sync_platform_recordings": {"queue": "sync"},
        # PM-integration sync
        "app.workers.integration_sync_worker.push_item_to_platform": {"queue": "sync"},
        "app.workers.integration_sync_worker.sync_integration_statuses": {"queue": "sync"},
        # Compliance / retention
        "app.workers.compliance_worker.run_retention_job": {"queue": "compliance"},
        "app.workers.compliance_worker.purge_expired_audio": {"queue": "compliance"},
    },

    # Default queue (catch-all for unrouted tasks)
    task_default_queue="default",

    # Retry behaviour — honour per-task settings; these are global defaults
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Worker settings
    worker_prefetch_multiplier=1,  # fair dispatch for long-running tasks

    # ------------------------------------------------------------------
    # Beat schedule — SINGLE SOURCE OF TRUTH for all scheduled tasks.
    # Agent-specific beat schedules must be consolidated here.
    # ------------------------------------------------------------------
    beat_schedule={
        # ---- Notifications ----
        # Check for due-soon items every morning at 08:00 UTC
        "due-soon-notifications-daily": {
            "task": "app.workers.notification_worker.send_due_soon_notifications",
            "schedule": crontab(hour=8, minute=0),
        },
        # Check for overdue items every morning at 08:30 UTC
        "overdue-notifications-daily": {
            "task": "app.workers.notification_worker.send_overdue_notifications",
            "schedule": crontab(hour=8, minute=30),
        },
        # Weekly digest every Monday at 09:00 UTC
        "weekly-digest-monday": {
            "task": "app.workers.notification_worker.send_weekly_digest",
            "schedule": crontab(hour=9, minute=0, day_of_week="monday"),
        },

        # ---- Platform sync ----
        # Poll connected meeting platforms every 15 minutes for new recordings
        "platform-sync-every-15-min": {
            "task": "app.workers.platform_sync_worker.sync_platform_recordings",
            "schedule": crontab(minute="*/15"),
        },

        # ---- PM-integration sync ----
        # Pull status updates from Jira/Asana/Linear/ClickUp every 30 minutes
        "integration-status-sync-every-30-min": {
            "task": "app.workers.integration_sync_worker.sync_integration_statuses",
            "schedule": crontab(minute="*/30"),
        },

        # ---- Compliance / GDPR retention ----
        # Run the data-retention sweep every night at 03:00 UTC
        "retention-job-nightly": {
            "task": "app.workers.compliance_worker.run_retention_job",
            "schedule": crontab(hour=3, minute=0),
        },
        # Purge expired audio files every night at 03:30 UTC
        "purge-expired-audio-nightly": {
            "task": "app.workers.compliance_worker.purge_expired_audio",
            "schedule": crontab(hour=3, minute=30),
        },
    },
)
