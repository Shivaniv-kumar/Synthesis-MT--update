"""Notifications API routes.

All endpoints require Admin role and operate on the current workspace derived
from the authenticated user's TenantContext.

Routes
------
GET    /notifications/rules              — list active rules for the workspace
POST   /notifications/rules              — create a new rule
PATCH  /notifications/rules/{rule_id}   — toggle is_active or update config
DELETE /notifications/rules/{rule_id}   — delete a rule
GET    /notifications/log               — last 100 log entries for the workspace
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_tenant_context, require_role
from app.config import get_settings
from app.database import get_db
from app.models.action_item import ActionItem
from app.models.knowledge_entry import KnowledgeEntry
from app.models.meeting import Meeting
from app.models.notification import NotificationLog, NotificationRule
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Admin-only dependency — reused across all endpoints
_admin = require_role(["Admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class NotificationRuleOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    rule_type: str
    channel: str
    is_active: bool
    config: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CreateNotificationRuleRequest(BaseModel):
    rule_type: str = Field(..., description="assignment | due_soon | overdue | digest")
    channel: str = Field(..., description="email | slack | teams")
    config: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Channel/rule-specific config. "
            "For slack/teams channels, must include 'webhook_url'. "
            "For due_soon, optionally include 'days_before_due'. "
            "For digest, optionally include 'digest_day'."
        ),
    )


class PatchNotificationRuleRequest(BaseModel):
    is_active: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class NotificationLogOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    item_id: Optional[uuid.UUID] = None
    rule_type: str
    channel: str
    status: str
    sent_at: datetime
    error: Optional[str] = None
    # Denormalised task text — populated when item_id is not None
    item_task: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_RULE_TYPES = {"assignment", "due_soon", "overdue", "digest", "sharing_summary"}
_VALID_CHANNELS = {"email", "slack", "teams"}
_WEBHOOK_CHANNELS = {"slack", "teams"}


def _validate_rule_type(rule_type: str) -> None:
    if rule_type not in _VALID_RULE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"rule_type must be one of: {sorted(_VALID_RULE_TYPES)}",
        )


def _validate_channel(channel: str) -> None:
    if channel not in _VALID_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"channel must be one of: {sorted(_VALID_CHANNELS)}",
        )


def _validate_webhook_url(channel: str, config: Optional[dict[str, Any]]) -> None:
    """Ensure webhook channels have a webhook_url in config."""
    if channel in _WEBHOOK_CHANNELS:
        if not config or not config.get("webhook_url"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"channel '{channel}' requires config.webhook_url",
            )


# ---------------------------------------------------------------------------
# GET /rules
# ---------------------------------------------------------------------------


@router.get(
    "/rules",
    response_model=list[NotificationRuleOut],
    summary="List notification rules for the current workspace",
)
async def list_rules(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(_admin),
) -> list[NotificationRuleOut]:
    """Return all notification rules for the authenticated user's workspace."""
    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.workspace_id == ctx.workspace_id,
            NotificationRule.tenant_id == ctx.tenant_id,
        )
    )
    rules: list[NotificationRule] = list(result.scalars().all())
    return [NotificationRuleOut.model_validate(r) for r in rules]


# ---------------------------------------------------------------------------
# POST /rules
# ---------------------------------------------------------------------------


@router.post(
    "/rules",
    response_model=NotificationRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification rule",
)
async def create_rule(
    body: CreateNotificationRuleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(_admin),
) -> NotificationRuleOut:
    """Create a new notification rule for the current workspace."""
    _validate_rule_type(body.rule_type)
    _validate_channel(body.channel)
    _validate_webhook_url(body.channel, body.config)

    rule = NotificationRule(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id,
        rule_type=body.rule_type,
        channel=body.channel,
        is_active=True,
        config=body.config,
    )
    db.add(rule)
    await db.flush([rule])
    await db.refresh(rule)

    logger.info(
        "notifications.rule_created",
        rule_id=str(rule.id),
        workspace_id=str(ctx.workspace_id),
        rule_type=rule.rule_type,
        channel=rule.channel,
    )
    return NotificationRuleOut.model_validate(rule)


# ---------------------------------------------------------------------------
# PATCH /rules/{rule_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/rules/{rule_id}",
    response_model=NotificationRuleOut,
    summary="Update a notification rule",
)
async def update_rule(
    rule_id: uuid.UUID,
    body: PatchNotificationRuleRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(_admin),
) -> NotificationRuleOut:
    """Toggle ``is_active`` or update the ``config`` JSON of an existing rule."""
    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.workspace_id == ctx.workspace_id,
            NotificationRule.tenant_id == ctx.tenant_id,
        )
    )
    rule: NotificationRule | None = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification rule not found",
        )

    if body.is_active is not None:
        rule.is_active = body.is_active

    if body.config is not None:
        # Validate webhook_url presence when updating config for webhook channels
        _validate_webhook_url(rule.channel, body.config)
        # Merge rather than replace — callers can pass partial updates
        existing_config: dict[str, Any] = rule.config or {}
        existing_config.update(body.config)
        rule.config = existing_config

    await db.flush([rule])
    await db.refresh(rule)

    logger.info(
        "notifications.rule_updated",
        rule_id=str(rule_id),
        workspace_id=str(ctx.workspace_id),
        is_active=rule.is_active,
    )
    return NotificationRuleOut.model_validate(rule)


# ---------------------------------------------------------------------------
# DELETE /rules/{rule_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a notification rule",
)
async def delete_rule(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(_admin),
) -> None:
    """Permanently delete a notification rule."""
    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.workspace_id == ctx.workspace_id,
            NotificationRule.tenant_id == ctx.tenant_id,
        )
    )
    rule: NotificationRule | None = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification rule not found",
        )

    await db.delete(rule)
    await db.flush()

    logger.info(
        "notifications.rule_deleted",
        rule_id=str(rule_id),
        workspace_id=str(ctx.workspace_id),
    )


# ---------------------------------------------------------------------------
# POST /rules/{rule_id}/test  — fire a test notification immediately (Admin)
# ---------------------------------------------------------------------------


class TestRuleResponse(BaseModel):
    success: bool
    message: str


@router.post(
    "/rules/{rule_id}/test",
    response_model=TestRuleResponse,
    summary="Send a test notification for a rule",
)
async def test_rule(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(_admin),
) -> TestRuleResponse:
    """Fire a single test notification for the given rule.

    Email rules deliver to the requesting admin's email address.
    Slack / Teams rules POST a test message to the configured webhook URL.
    """
    from app.services.notification_providers import EmailProvider, SlackProvider, TeamsProvider

    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.workspace_id == ctx.workspace_id,
            NotificationRule.tenant_id == ctx.tenant_id,
        )
    )
    rule: NotificationRule | None = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification rule not found")

    rule_label = {
        "assignment": "Item Assigned",
        "due_soon": "Due Soon Alert",
        "overdue": "Overdue Alert",
        "digest": "Weekly Digest",
        "sharing_summary": "Sharing Summary",
    }.get(rule.rule_type, rule.rule_type)

    try:
        if rule.channel == "email":
            provider = EmailProvider()
            html = (
                "<div style='font-family:sans-serif;max-width:480px'>"
                "<h2 style='color:#4f46e5'>✅ Test Notification</h2>"
                f"<p>This is a test for your <strong>{rule_label}</strong> email rule.</p>"
                "<p>If you received this, your email notifications are configured correctly.</p>"
                "<p style='color:#6b7280;font-size:12px'>Synthesis</p>"
                "</div>"
            )
            text = f"Test notification for rule: {rule_label}. If you received this, email notifications are working."
            await provider.send_email(
                to=admin_user.email,
                subject=f"[Test] Synthesis — {rule_label}",
                html_body=html,
                text_body=text,
            )
            msg = f"Test email sent to {admin_user.email}"

        elif rule.channel == "slack":
            webhook_url = (rule.config or {}).get("webhook_url", "")
            if not webhook_url:
                raise ValueError("No webhook_url configured for this rule.")
            provider = SlackProvider()
            payload = {
                "text": f"[Test] Synthesis — {rule_label}",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "✅ Test Notification", "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"This is a test for your *{rule_label}* rule.\n"
                                "If you see this, your Slack notifications are configured correctly."
                            ),
                        },
                    },
                ],
            }
            await provider.send_webhook(str(webhook_url), payload)
            msg = "Test message sent to Slack webhook."

        elif rule.channel == "teams":
            webhook_url = (rule.config or {}).get("webhook_url", "")
            if not webhook_url:
                raise ValueError("No webhook_url configured for this rule.")
            provider = TeamsProvider()
            payload = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "text": "✅ Test Notification",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"This is a test for your **{rule_label}** rule. If you see this, Teams notifications are working.",
                                    "wrap": True,
                                },
                            ],
                        },
                    }
                ],
            }
            await provider.send_webhook(str(webhook_url), payload)
            msg = "Test message sent to Teams webhook."

        else:
            raise ValueError(f"Unknown channel: {rule.channel}")

        log = NotificationLog(
            id=uuid.uuid4(),
            user_id=admin_user.id,
            rule_type=rule.rule_type,
            channel=rule.channel,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(log)
        await db.flush([log])

        logger.info(
            "notifications.test_sent",
            rule_id=str(rule_id),
            channel=rule.channel,
            admin_user=admin_user.email,
        )
        return TestRuleResponse(success=True, message=msg)

    except Exception as exc:
        log = NotificationLog(
            id=uuid.uuid4(),
            user_id=admin_user.id,
            rule_type=rule.rule_type,
            channel=rule.channel,
            status="failed",
            sent_at=datetime.utcnow(),
            error=str(exc)[:500],
        )
        db.add(log)
        await db.flush([log])
        logger.warning("notifications.test_failed", rule_id=str(rule_id), error=str(exc))
        return TestRuleResponse(success=False, message=str(exc))


# ---------------------------------------------------------------------------
# POST /rules/{rule_id}/run  — fire with real action-item data immediately
# ---------------------------------------------------------------------------


_TRANSCRIPT_LIMIT = 6000  # chars before truncation for summary prompt

_SUMMARY_PROMPT = """\
You are a meeting summariser. Using the data below, write a concise, \
well-structured meeting summary that can be shared with the team via Slack or email.

Structure your response with these sections (use **bold** headers):
**Overview** — 2-3 sentences on what the meeting covered and key outcomes.
**Key Decisions** — bullet list of decisions made (skip if none).
**Action Items** — bullet list: [Priority] Task — Owner, Due.
**Open Questions / Blockers** — bullet list (skip if none).

Keep it under 350 words. Use plain markdown (bullets, bold). No code fences.

<MEETING_DATA>
{context}
</MEETING_DATA>"""


async def _build_sharing_summary(
    rule: NotificationRule,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[str, str]:
    """Generate an AI-powered transcript summary for sharing_summary rules."""
    config = rule.config or {}

    # 1. Resolve target meeting
    if config.get("meeting_id"):
        r = await db.execute(
            select(Meeting).where(
                Meeting.id == uuid.UUID(str(config["meeting_id"])),
                Meeting.tenant_id == tenant_id,
            )
        )
        meeting: Meeting | None = r.scalar_one_or_none()
    else:
        r = await db.execute(
            select(Meeting)
            .where(
                Meeting.tenant_id == tenant_id,
                Meeting.transcript_text.isnot(None),
            )
            .order_by(Meeting.created_at.desc())
            .limit(1)
        )
        meeting = r.scalars().first()

    if not meeting:
        subject = "Synthesis — Meeting Summary"
        body = "No meeting transcript found to summarise. Upload a transcript first."
        return subject, body

    date_str = meeting.occurred_at.strftime("%Y-%m-%d") if meeting.occurred_at else "Unknown date"
    meeting_title = meeting.title or "Untitled Meeting"

    # 2. Fetch action items for this meeting (deduplicated by task text)
    item_r = await db.execute(
        select(ActionItem)
        .where(ActionItem.meeting_id == meeting.id, ActionItem.tenant_id == tenant_id)
        .order_by(ActionItem.priority.asc(), ActionItem.task.asc())
        .limit(40)
    )
    all_items = list(item_r.scalars().all())
    seen_tasks: set[str] = set()
    items = []
    for item in all_items:
        key = item.task.strip().lower()
        if key not in seen_tasks:
            seen_tasks.add(key)
            items.append(item)

    # 3. Fetch knowledge entries for this meeting
    know_r = await db.execute(
        select(KnowledgeEntry)
        .where(KnowledgeEntry.meeting_id == meeting.id, KnowledgeEntry.tenant_id == tenant_id)
        .limit(30)
    )
    knowledge = list(know_r.scalars().all())

    # 4. Build LLM context block
    transcript = meeting.transcript_text or ""
    if len(transcript) > _TRANSCRIPT_LIMIT:
        transcript = transcript[:_TRANSCRIPT_LIMIT] + "\n[... transcript truncated ...]"

    ctx_lines: list[str] = [
        f"Meeting: {meeting_title}",
        f"Date: {date_str}",
        f"Attendees: {', '.join(meeting.attendees) if meeting.attendees else 'Not recorded'}",
        "",
        "Transcript:",
        transcript,
    ]

    if items:
        ctx_lines += ["", "Extracted Action Items:"]
        for item in items:
            ctx_lines.append(
                f"• [{item.priority}] {item.task} "
                f"(Owner: {item.owner_label or 'Unassigned'}, Due: {item.due_text or 'No due date'}, Status: {item.status})"
            )

    if knowledge:
        ctx_lines += ["", "Extracted Knowledge:"]
        for k in knowledge:
            ctx_lines.append(f"• [{k.category}] {k.content}")

    context = "\n".join(ctx_lines)

    # 5. Generate summary via Claude
    settings = get_settings()
    scope_note = f" · {config['project_name']}" if config.get("project_name") else ""
    subject = f"Synthesis — Meeting Summary · {meeting_title}{scope_note}"

    if settings.anthropic_api_key:
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=900,
                messages=[{"role": "user", "content": _SUMMARY_PROMPT.format(context=context)}],
            )
            body = response.content[0].text if response.content else ""
        except Exception as exc:
            logger.warning("notifications.sharing_summary_llm_failed", error=str(exc))
            body = ""
    else:
        body = ""

    # Fallback: structured summary if LLM unavailable or returned empty
    if not body:
        lines = [f"**{meeting_title}** — {date_str}", ""]
        if items:
            lines.append(f"**Action Items ({len(items)})**")
            for item in items:
                lines.append(
                    f"• [{item.priority}] {item.task}  "
                    f"Owner: {item.owner_label or 'Unassigned'} | Due: {item.due_text or 'No due date'}"
                )
        else:
            lines.append("No action items extracted from this meeting.")
        body = "\n".join(lines)

    return subject, body


async def _build_run_content(
    rule: NotificationRule,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[str, str]:
    """Fetch content for this rule and return (subject, markdown_body).

    Sharing summary rules generate an AI transcript summary; all other rule
    types produce an action-item list filtered by scope and rule type.
    """
    if rule.rule_type == "sharing_summary":
        return await _build_sharing_summary(rule, tenant_id, db)

    config = rule.config or {}
    today = date.today()

    filters: list[Any] = [
        ActionItem.tenant_id == tenant_id,
        ActionItem.status.in_(["Open", "In progress"]),
    ]

    if config.get("user_id"):
        try:
            filters.append(ActionItem.owner_user_id == uuid.UUID(str(config["user_id"])))
        except Exception:
            pass

    if config.get("meeting_id"):
        try:
            filters.append(ActionItem.meeting_id == uuid.UUID(str(config["meeting_id"])))
        except Exception:
            pass

    if config.get("project_id"):
        try:
            filters.append(ActionItem.project_id == uuid.UUID(str(config["project_id"])))
        except Exception:
            pass

    if rule.rule_type == "due_soon":
        filters += [
            ActionItem.due_date.isnot(None),
            ActionItem.due_date >= today,
            ActionItem.due_date <= today + timedelta(days=2),
        ]
    elif rule.rule_type == "overdue":
        filters += [
            ActionItem.due_date.isnot(None),
            ActionItem.due_date < today,
        ]

    rows = await db.execute(
        select(ActionItem)
        .where(*filters)
        .order_by(ActionItem.due_date.asc().nulls_last())
        .limit(50)
    )
    items = list(rows.scalars().all())

    _labels: dict[str, str] = {
        "assignment": "Action Items Summary",
        "due_soon": "Due Soon Alert",
        "overdue": "Overdue Items",
        "digest": "Weekly Digest",
    }
    rule_label = _labels.get(rule.rule_type, rule.rule_type)

    scope_parts: list[str] = []
    if config.get("user_display_name"):
        scope_parts.append(str(config["user_display_name"]))
    if config.get("meeting_title"):
        scope_parts.append(str(config["meeting_title"]))
    if config.get("project_name"):
        scope_parts.append(str(config["project_name"]))
    scope_note = " · " + " · ".join(scope_parts) if scope_parts else ""

    subject = f"Synthesis — {rule_label}{scope_note}"

    if not items:
        body = f"No open action items to report for *{rule_label}*{scope_note}."
    else:
        lines = [f"*{rule_label}*{scope_note} — {len(items)} item(s)\n"]
        for item in items:
            owner = item.owner_label or "Unassigned"
            due = item.due_text or "No due date"
            lines.append(f"• [{item.priority}] {item.task}")
            lines.append(f"  Owner: {owner}  |  Due: {due}  |  Status: {item.status}")
        body = "\n".join(lines)

    return subject, body


@router.post(
    "/rules/{rule_id}/run",
    response_model=TestRuleResponse,
    summary="Run a notification rule immediately with real data",
)
async def run_rule(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(_admin),
) -> TestRuleResponse:
    """Send an immediate notification with real action-item data for this rule.

    Unlike /test (which sends a placeholder), this sends the actual content the
    rule would deliver on its schedule — useful for on-demand triggers.
    Email rules deliver to the requesting admin's address.
    """
    from app.services.notification_providers import EmailProvider, SlackProvider, TeamsProvider

    result = await db.execute(
        select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.workspace_id == ctx.workspace_id,
            NotificationRule.tenant_id == ctx.tenant_id,
        )
    )
    rule: NotificationRule | None = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification rule not found")

    try:
        subject, body = await _build_run_content(rule, ctx.tenant_id, db)

        if rule.channel == "email":
            provider = EmailProvider()
            html = (
                "<div style='font-family:sans-serif;max-width:600px;color:#334155;font-size:14px'>"
                + body.replace("\n", "<br>")
                + "<br><br><span style='color:#94a3b8;font-size:11px'>Synthesis</span>"
                + "</div>"
            )
            await provider.send_email(
                to=admin_user.email,
                subject=subject,
                html_body=html,
                text_body=body,
            )
            msg = f"Notification sent to {admin_user.email}"

        elif rule.channel == "slack":
            webhook_url = (rule.config or {}).get("webhook_url", "")
            if not webhook_url:
                raise ValueError("No webhook_url configured for this rule.")
            provider = SlackProvider()
            payload = {
                "text": subject,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": subject, "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": body[:3000]},
                    },
                ],
            }
            await provider.send_webhook(str(webhook_url), payload)
            msg = "Notification sent to Slack."

        elif rule.channel == "teams":
            webhook_url = (rule.config or {}).get("webhook_url", "")
            if not webhook_url:
                raise ValueError("No webhook_url configured for this rule.")
            provider = TeamsProvider()
            payload = {
                "type": "message",
                "attachments": [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": {
                            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                            "type": "AdaptiveCard",
                            "version": "1.4",
                            "body": [
                                {
                                    "type": "TextBlock",
                                    "text": subject,
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Accent",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": body[:3000],
                                    "wrap": True,
                                    "spacing": "Medium",
                                },
                            ],
                        },
                    }
                ],
            }
            await provider.send_webhook(str(webhook_url), payload)
            msg = "Notification sent to Teams."

        else:
            raise ValueError(f"Unknown channel: {rule.channel}")

        log = NotificationLog(
            id=uuid.uuid4(),
            user_id=admin_user.id,
            rule_type=rule.rule_type,
            channel=rule.channel,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(log)
        await db.flush([log])

        logger.info("notifications.run_sent", rule_id=str(rule_id), channel=rule.channel)
        return TestRuleResponse(success=True, message=msg)

    except Exception as exc:
        log = NotificationLog(
            id=uuid.uuid4(),
            user_id=admin_user.id,
            rule_type=rule.rule_type,
            channel=rule.channel,
            status="failed",
            sent_at=datetime.utcnow(),
            error=str(exc)[:500],
        )
        db.add(log)
        await db.flush([log])
        logger.warning("notifications.run_failed", rule_id=str(rule_id), error=str(exc))
        return TestRuleResponse(success=False, message=str(exc))


# ---------------------------------------------------------------------------
# GET /log
# ---------------------------------------------------------------------------


@router.get(
    "/log",
    response_model=list[NotificationLogOut],
    summary="Get the last 100 notification log entries for the workspace",
)
async def get_log(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
    _admin_user: User = Depends(_admin),
) -> list[NotificationLogOut]:
    """Return the 100 most-recent NotificationLog entries for users in this workspace.

    The query joins on ActionItem when ``item_id`` is present so the response
    includes the human-readable ``item_task`` field.
    """
    # We identify "in-workspace" logs by matching user_id against the set of
    # users who are members of this workspace (via WorkspaceMember).
    from app.models.workspace import WorkspaceMember

    # Step 1: collect member user_ids for this workspace
    members_result = await db.execute(
        select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == ctx.workspace_id,
        )
    )
    member_user_ids: list[uuid.UUID] = [row[0] for row in members_result.all()]

    if not member_user_ids:
        return []

    # Step 2: fetch logs for those users, most-recent first, capped at 100
    logs_result = await db.execute(
        select(NotificationLog)
        .where(NotificationLog.user_id.in_(member_user_ids))
        .order_by(NotificationLog.sent_at.desc())
        .limit(100)
    )
    logs: list[NotificationLog] = list(logs_result.scalars().all())

    if not logs:
        return []

    # Step 3: enrich with item task text where item_id is present
    item_ids = [log.item_id for log in logs if log.item_id is not None]
    item_tasks: dict[uuid.UUID, str] = {}
    if item_ids:
        items_result = await db.execute(
            select(ActionItem.id, ActionItem.task).where(
                ActionItem.id.in_(item_ids)
            )
        )
        item_tasks = {row[0]: row[1] for row in items_result.all()}

    out: list[NotificationLogOut] = []
    for log in logs:
        entry = NotificationLogOut(
            id=log.id,
            user_id=log.user_id,
            item_id=log.item_id,
            rule_type=log.rule_type,
            channel=log.channel,
            status=log.status,
            sent_at=log.sent_at,
            error=log.error,
            item_task=item_tasks.get(log.item_id) if log.item_id else None,
        )
        out.append(entry)

    return out
