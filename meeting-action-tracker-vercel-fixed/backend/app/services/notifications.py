"""High-level notification orchestration service.

NotificationService is the single entry point called by both the API layer
(e.g. when an item is assigned) and the Celery beat tasks (due-soon, overdue,
digest).

Design principles
-----------------
* All public methods are ``async`` and accept an ``AsyncSession`` — they never
  open their own session, making them easy to call from within an existing
  transaction or from an async Celery bridge (``asyncio.run``).
* Exceptions are **never** propagated to the caller.  Every failure is caught,
  logged via structlog, and recorded as a ``NotificationLog`` row with
  ``status="failed"``.
* HTML email bodies are rendered from Jinja2 templates stored under
  ``app/templates/``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.action_item import ActionItem
from app.models.notification import NotificationLog, NotificationRule
from app.models.user import User
from app.services.notification_providers import (
    EmailProvider,
    SlackProvider,
    TeamsProvider,
    get_provider,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# ---------------------------------------------------------------------------
# Tracker base URL  (used to build deep-links in emails / webhooks)
# ---------------------------------------------------------------------------

_TRACKER_BASE_URL: str = os.environ.get(
    "FRONTEND_URL", "http://localhost:5173"
)


def _tracker_item_url(item_id: uuid.UUID) -> str:
    return f"{_TRACKER_BASE_URL}/items/{item_id}"


def _tracker_url() -> str:
    return _TRACKER_BASE_URL


# ---------------------------------------------------------------------------
# NotificationService
# ---------------------------------------------------------------------------


class NotificationService:
    """Orchestrates all notification delivery for the Meeting Action Tracker."""

    # ------------------------------------------------------------------
    # Public: assignment notification
    # ------------------------------------------------------------------

    async def notify_assignment(
        self,
        item: ActionItem,
        assignee: User,
        assigner: User,
        rules: list[NotificationRule],
        db: AsyncSession,
    ) -> None:
        """Notify ``assignee`` that they have been given a new action item.

        Iterates over every active ``assignment`` rule and fires the
        appropriate channel.  Each attempt is logged to ``NotificationLog``.
        """
        assignment_rules = [r for r in rules if r.rule_type == "assignment" and r.is_active]
        if not assignment_rules:
            return

        subject, html_body, text_body = self._format_assignment_email(item, assignee, assigner)

        for rule in assignment_rules:
            await self._send_for_rule(
                rule=rule,
                user=assignee,
                item=item,
                db=db,
                # Email kwargs
                email_to=assignee.email,
                email_subject=subject,
                email_html=html_body,
                email_text=text_body,
                # Webhook kwargs
                webhook_payload_fn=lambda r=rule: self._build_assignment_webhook_payload(
                    item, assignee, assigner, rule=r
                ),
            )

    # ------------------------------------------------------------------
    # Public: due-soon notification
    # ------------------------------------------------------------------

    async def notify_due_soon(
        self,
        items: list[ActionItem],
        user: User,
        rules: list[NotificationRule],
        db: AsyncSession,
    ) -> None:
        """Send a consolidated "items due soon" notification for a single user.

        ``items`` should already be filtered to those due within the configured
        window (typically 2 days).  If the list is empty no message is sent.
        """
        if not items:
            return

        due_soon_rules = [r for r in rules if r.rule_type == "due_soon" and r.is_active]
        if not due_soon_rules:
            return

        subject = f"Reminder: {len(items)} action item(s) due soon"
        html_body, text_body = self._render_due_soon_bodies(user, items)

        for rule in due_soon_rules:
            await self._send_for_rule(
                rule=rule,
                user=user,
                item=None,
                db=db,
                email_to=user.email,
                email_subject=subject,
                email_html=html_body,
                email_text=text_body,
                webhook_payload_fn=lambda r=rule: self._build_list_webhook_payload(
                    heading=f"Action items due soon for {user.display_name or user.email}",
                    items=items,
                    tracker_url=_tracker_url(),
                    rule=r,
                ),
            )

    # ------------------------------------------------------------------
    # Public: overdue notification
    # ------------------------------------------------------------------

    async def notify_overdue(
        self,
        items: list[ActionItem],
        user: User,
        rules: list[NotificationRule],
        db: AsyncSession,
    ) -> None:
        """Notify ``user`` about their overdue action items (past due_date, not Done).

        If the list is empty no message is sent.
        """
        if not items:
            return

        overdue_rules = [r for r in rules if r.rule_type == "overdue" and r.is_active]
        if not overdue_rules:
            return

        subject = f"Overdue: {len(items)} action item(s) need your attention"
        html_body, text_body = self._render_overdue_bodies(user, items)

        for rule in overdue_rules:
            await self._send_for_rule(
                rule=rule,
                user=user,
                item=None,
                db=db,
                email_to=user.email,
                email_subject=subject,
                email_html=html_body,
                email_text=text_body,
                webhook_payload_fn=lambda r=rule: self._build_list_webhook_payload(
                    heading=f"Overdue action items for {user.display_name or user.email}",
                    items=items,
                    tracker_url=_tracker_url(),
                    rule=r,
                ),
            )

    # ------------------------------------------------------------------
    # Public: weekly digest
    # ------------------------------------------------------------------

    async def send_digest(
        self,
        user: User,
        workspace_id: uuid.UUID,
        rules: list[NotificationRule],
        db: AsyncSession,
    ) -> None:
        """Fetch all open items owned by ``user`` and send a digest message.

        Skips silently when the user has no open items.
        """
        digest_rules = [r for r in rules if r.rule_type == "digest" and r.is_active]
        if not digest_rules:
            return

        # Fetch open items owned by this user in this workspace
        result = await db.execute(
            select(ActionItem).where(
                ActionItem.workspace_id == workspace_id,
                ActionItem.owner_user_id == user.id,
                ActionItem.status != "Done",
            )
        )
        items: list[ActionItem] = list(result.scalars().all())

        if not items:
            logger.debug(
                "notifications.digest_skipped_no_items",
                user_id=str(user.id),
                workspace_id=str(workspace_id),
            )
            return

        subject, html_body, text_body = self._format_digest_email(user, items)

        for rule in digest_rules:
            await self._send_for_rule(
                rule=rule,
                user=user,
                item=None,
                db=db,
                email_to=user.email,
                email_subject=subject,
                email_html=html_body,
                email_text=text_body,
                webhook_payload_fn=lambda r=rule: self._build_digest_webhook_payload(
                    user=user, items=items, rule=r
                ),
            )

    # ------------------------------------------------------------------
    # Private: email formatters
    # ------------------------------------------------------------------

    def _format_assignment_email(
        self,
        item: ActionItem,
        assignee: User,
        assigner: User,
    ) -> tuple[str, str, str]:
        """Return (subject, html_body, text_body) for an assignment notification."""
        subject = f"You've been assigned: {item.task[:80]}"

        due_date_str = str(item.due_date) if item.due_date else "Not set"
        tracker_link = _tracker_item_url(item.id)

        try:
            template = _jinja_env.get_template("email_assignment.html")
            html_body = template.render(
                assignee_name=assignee.display_name or assignee.email,
                assigner_name=assigner.display_name or assigner.email,
                task=item.task,
                priority=item.priority,
                due_date=due_date_str,
                context=item.context,
                tracker_link=tracker_link,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("notifications.template_render_failed", error=str(exc))
            html_body = (
                f"<p>Hi {assignee.display_name or assignee.email},</p>"
                f"<p>You have been assigned a new action item:</p>"
                f"<p><strong>{item.task}</strong></p>"
                f"<p>Priority: {item.priority} | Due: {due_date_str}</p>"
                f"<p><a href='{tracker_link}'>View in Meeting Action Tracker</a></p>"
            )

        text_body = (
            f"Hi {assignee.display_name or assignee.email},\n\n"
            f"You have been assigned a new action item by "
            f"{assigner.display_name or assigner.email}:\n\n"
            f"  Task:     {item.task}\n"
            f"  Priority: {item.priority}\n"
            f"  Due:      {due_date_str}\n\n"
            f"View it here: {tracker_link}\n"
        )

        return subject, html_body, text_body

    def _format_digest_email(
        self,
        user: User,
        items: list[ActionItem],
    ) -> tuple[str, str, str]:
        """Return (subject, html_body, text_body) for a weekly digest email."""
        subject = f"Your open action items — {len(items)} item(s)"
        tracker_link = _tracker_url()

        # Build serialisable item dicts for the template
        item_dicts = [
            {
                "task": it.task,
                "priority": it.priority,
                "due_date": str(it.due_date) if it.due_date else "—",
                "status": it.status,
                "link": _tracker_item_url(it.id),
            }
            for it in items
        ]

        try:
            template = _jinja_env.get_template("email_digest.html")
            html_body = template.render(
                user_name=user.display_name or user.email,
                items=item_dicts,
                tracker_link=tracker_link,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("notifications.digest_template_render_failed", error=str(exc))
            rows = "\n".join(
                f"  • {it['task']} [{it['priority']}] — due {it['due_date']} ({it['status']})"
                for it in item_dicts
            )
            html_body = (
                f"<p>Hi {user.display_name or user.email},</p>"
                f"<p>Here are your open action items:</p><ul>"
                + "".join(
                    f"<li><strong>{it['task']}</strong> — {it['priority']}, "
                    f"due {it['due_date']}, {it['status']}</li>"
                    for it in item_dicts
                )
                + f"</ul><p><a href='{tracker_link}'>View all in Meeting Action Tracker</a></p>"
            )
            text_body = (
                f"Hi {user.display_name or user.email},\n\n"
                f"Your open action items:\n\n{rows}\n\n"
                f"View all: {tracker_link}\n"
            )
            return subject, html_body, text_body

        text_body = (
            f"Hi {user.display_name or user.email},\n\n"
            "Your open action items:\n\n"
            + "\n".join(
                f"  • {it['task']} [{it['priority']}] — due {it['due_date']} ({it['status']})"
                for it in item_dicts
            )
            + f"\n\nView all: {tracker_link}\n"
        )

        return subject, html_body, text_body

    # ------------------------------------------------------------------
    # Private: rendered bodies for due-soon and overdue  (no custom template)
    # ------------------------------------------------------------------

    def _render_due_soon_bodies(
        self, user: User, items: list[ActionItem]
    ) -> tuple[str, str]:
        """Return (html_body, text_body) for a due-soon notification."""
        tracker_link = _tracker_url()
        name = user.display_name or user.email
        rows_html = "".join(
            f"<tr>"
            f"<td style='padding:6px 10px'>{it.task}</td>"
            f"<td style='padding:6px 10px'>{it.priority}</td>"
            f"<td style='padding:6px 10px'>{it.due_date or '—'}</td>"
            f"<td style='padding:6px 10px'>{it.status}</td>"
            f"</tr>"
            for it in items
        )
        html_body = (
            f"<p>Hi {name},</p>"
            f"<p>The following action items are due within the next 2 days:</p>"
            f"<table border='1' cellpadding='0' cellspacing='0' style='border-collapse:collapse'>"
            f"<thead><tr>"
            f"<th style='padding:6px 10px'>Task</th>"
            f"<th style='padding:6px 10px'>Priority</th>"
            f"<th style='padding:6px 10px'>Due Date</th>"
            f"<th style='padding:6px 10px'>Status</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
            f"<p><a href='{tracker_link}'>View in Meeting Action Tracker</a></p>"
        )
        text_rows = "\n".join(
            f"  • {it.task} [{it.priority}] — due {it.due_date or 'TBD'} ({it.status})"
            for it in items
        )
        text_body = (
            f"Hi {name},\n\n"
            f"The following action items are due within the next 2 days:\n\n"
            f"{text_rows}\n\nView them: {tracker_link}\n"
        )
        return html_body, text_body

    def _render_overdue_bodies(
        self, user: User, items: list[ActionItem]
    ) -> tuple[str, str]:
        """Return (html_body, text_body) for an overdue notification."""
        tracker_link = _tracker_url()
        name = user.display_name or user.email
        rows_html = "".join(
            f"<tr>"
            f"<td style='padding:6px 10px'>{it.task}</td>"
            f"<td style='padding:6px 10px'>{it.priority}</td>"
            f"<td style='padding:6px 10px'>{it.due_date or '—'}</td>"
            f"<td style='padding:6px 10px'>{it.status}</td>"
            f"</tr>"
            for it in items
        )
        html_body = (
            f"<p>Hi {name},</p>"
            f"<p>The following action items are <strong>overdue</strong>:</p>"
            f"<table border='1' cellpadding='0' cellspacing='0' style='border-collapse:collapse'>"
            f"<thead><tr>"
            f"<th style='padding:6px 10px'>Task</th>"
            f"<th style='padding:6px 10px'>Priority</th>"
            f"<th style='padding:6px 10px'>Due Date</th>"
            f"<th style='padding:6px 10px'>Status</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table>"
            f"<p><a href='{tracker_link}'>View in Meeting Action Tracker</a></p>"
        )
        text_rows = "\n".join(
            f"  • {it.task} [{it.priority}] — was due {it.due_date or 'TBD'} ({it.status})"
            for it in items
        )
        text_body = (
            f"Hi {name},\n\n"
            f"The following action items are overdue:\n\n"
            f"{text_rows}\n\nPlease take action: {tracker_link}\n"
        )
        return html_body, text_body

    # ------------------------------------------------------------------
    # Private: webhook payload builders
    # ------------------------------------------------------------------

    def _build_assignment_webhook_payload(
        self,
        item: ActionItem,
        assignee: User,
        assigner: User,
        rule: NotificationRule,
    ) -> dict[str, Any]:
        due_date_str = str(item.due_date) if item.due_date else "Not set"
        tracker_link = _tracker_item_url(item.id)
        heading = f"New action item assigned by {assigner.display_name or assigner.email}"

        if rule.channel == "slack":
            return SlackProvider.build_action_item_payload(
                task=item.task,
                owner=assignee.display_name or assignee.email,
                due_date=due_date_str,
                priority=item.priority,
                tracker_url=tracker_link,
                heading=heading,
            )
        # Teams
        return TeamsProvider.build_action_item_card(
            task=item.task,
            owner=assignee.display_name or assignee.email,
            due_date=due_date_str,
            priority=item.priority,
            tracker_url=tracker_link,
            heading=heading,
        )

    def _build_list_webhook_payload(
        self,
        heading: str,
        items: list[ActionItem],
        tracker_url: str,
        rule: NotificationRule,
    ) -> dict[str, Any]:
        item_dicts = [
            {
                "task": it.task,
                "priority": it.priority,
                "due_date": str(it.due_date) if it.due_date else None,
            }
            for it in items
        ]
        if rule.channel == "slack":
            # Build a simple text-based Slack message for lists
            lines = [
                f"• *{it['task']}* — {it['priority']}, due {it.get('due_date') or 'TBD'}"
                for it in item_dicts
            ]
            return {
                "text": heading,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": heading, "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "View in Meeting Action Tracker"},
                                "url": tracker_url,
                                "style": "primary",
                            }
                        ],
                    },
                ],
            }
        # Teams Adaptive Card
        facts = [
            {
                "title": it["task"][:80],
                "value": f"{it['priority']} — due {it.get('due_date') or 'TBD'}",
            }
            for it in item_dicts
        ]
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": heading,
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {"type": "FactSet", "facts": facts or [{"title": "Status", "value": "No items"}]},
                        ],
                        "actions": [
                            {"type": "Action.OpenUrl", "title": "View in Meeting Action Tracker", "url": tracker_url}
                        ],
                    },
                }
            ],
        }

    def _build_digest_webhook_payload(
        self,
        user: User,
        items: list[ActionItem],
        rule: NotificationRule,
    ) -> dict[str, Any]:
        item_dicts = [
            {
                "task": it.task,
                "priority": it.priority,
                "due_date": str(it.due_date) if it.due_date else None,
            }
            for it in items
        ]
        if rule.channel == "slack":
            return SlackProvider.build_digest_payload(
                user_name=user.display_name or user.email,
                items=item_dicts,
                tracker_url=_tracker_url(),
            )
        return TeamsProvider.build_digest_card(
            user_name=user.display_name or user.email,
            items=item_dicts,
            tracker_url=_tracker_url(),
        )

    # ------------------------------------------------------------------
    # Private: unified send + log helper
    # ------------------------------------------------------------------

    async def _send_for_rule(
        self,
        rule: NotificationRule,
        user: User,
        item: Optional[ActionItem],
        db: AsyncSession,
        email_to: str,
        email_subject: str,
        email_html: str,
        email_text: str,
        webhook_payload_fn: Any,
    ) -> None:
        """Attempt delivery for a single rule and write a NotificationLog row."""
        sent_at = datetime.now(tz=timezone.utc)
        error_detail: Optional[str] = None
        status = "sent"

        try:
            provider = get_provider(rule.channel)

            if rule.channel == "email":
                await provider.send_email(
                    to=email_to,
                    subject=email_subject,
                    html_body=email_html,
                    text_body=email_text,
                )
            else:
                webhook_url: str = (rule.config or {}).get("webhook_url", "")
                if not webhook_url:
                    raise ValueError(
                        f"No webhook_url configured for rule {rule.id} (channel={rule.channel})"
                    )
                payload = webhook_payload_fn()
                await provider.send_webhook(url=webhook_url, payload=payload)

        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error_detail = f"{type(exc).__name__}: {exc}"
            logger.error(
                "notifications.send_failed",
                rule_id=str(rule.id),
                channel=rule.channel,
                rule_type=rule.rule_type,
                user_id=str(user.id),
                error=error_detail,
            )

        # Always write a log row, even on failure
        log = NotificationLog(
            id=uuid.uuid4(),
            user_id=user.id,
            item_id=item.id if item else None,
            rule_type=rule.rule_type,
            channel=rule.channel,
            status=status,
            sent_at=sent_at,
            error=error_detail,
        )
        db.add(log)
        try:
            await db.flush([log])
        except Exception as flush_exc:  # noqa: BLE001
            logger.error(
                "notifications.log_flush_failed",
                error=str(flush_exc),
                user_id=str(user.id),
            )
