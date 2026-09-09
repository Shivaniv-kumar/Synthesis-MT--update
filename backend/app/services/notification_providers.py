"""Notification provider abstractions.

Each channel (email, Slack, Teams) is implemented as a concrete subclass of
``NotificationProvider``.  The factory function ``get_provider`` returns the
right instance based on the channel string stored in ``NotificationRule.channel``.

Environment variables consumed:
    SENDGRID_API_KEY   — SendGrid HTTP API key (preferred for email)
    EMAIL_FROM         — Sender address (falls back to settings.email_from if defined)
    EMAIL_HOST         — SMTP host (fallback when SENDGRID_API_KEY is absent)
    EMAIL_PORT         — SMTP port (default 587)
    EMAIL_USER         — SMTP username
    EMAIL_PASSWORD     — SMTP password
"""

from __future__ import annotations

import os
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Defaults / helpers
# ---------------------------------------------------------------------------

_SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
_DEFAULT_FROM = os.environ.get("EMAIL_FROM", "noreply@meeting-tracker.example.com")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class NotificationProvider(ABC):
    """Common interface that every delivery channel must implement."""

    @abstractmethod
    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        """Send an email to a single recipient."""

    @abstractmethod
    async def send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        """POST a JSON payload to the given webhook URL."""


# ---------------------------------------------------------------------------
# Email provider  (SendGrid → SMTP fallback)
# ---------------------------------------------------------------------------


class EmailProvider(NotificationProvider):
    """Sends email via SendGrid when an API key is configured, otherwise via SMTP."""

    def __init__(self) -> None:
        self._sendgrid_key: str = os.environ.get("SENDGRID_API_KEY", "")
        self._smtp_host: str = os.environ.get("EMAIL_HOST", "")
        self._smtp_port: int = int(os.environ.get("EMAIL_PORT", "587"))
        self._smtp_user: str = os.environ.get("EMAIL_USER", "")
        self._smtp_password: str = os.environ.get("EMAIL_PASSWORD", "")
        self._from_address: str = _DEFAULT_FROM

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        if self._sendgrid_key:
            await self._send_via_sendgrid(to, subject, html_body, text_body)
        elif self._smtp_host:
            await self._send_via_smtp(to, subject, html_body, text_body)
        else:
            logger.warning(
                "email_provider.no_transport_configured",
                to=to,
                subject=subject,
            )
            raise RuntimeError(
                "No email transport configured. "
                "Set SENDGRID_API_KEY or EMAIL_HOST environment variables."
            )

    async def send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        # EmailProvider does not support webhooks — delegate to a generic POST.
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

    # ------------------------------------------------------------------
    # SendGrid transport
    # ------------------------------------------------------------------

    async def _send_via_sendgrid(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        payload: dict[str, Any] = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self._from_address},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text_body},
                {"type": "text/html", "value": html_body},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._sendgrid_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                _SENDGRID_SEND_URL,
                json=payload,
                headers=headers,
            )
        if response.status_code not in (200, 202):
            raise RuntimeError(
                f"SendGrid returned {response.status_code}: {response.text}"
            )
        logger.info(
            "email_provider.sendgrid_sent",
            to=to,
            subject=subject,
            status_code=response.status_code,
        )

    # ------------------------------------------------------------------
    # SMTP fallback transport  (runs in a thread to avoid blocking the loop)
    # ------------------------------------------------------------------

    async def _send_via_smtp(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        import asyncio

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._blocking_smtp_send,
            to,
            subject,
            html_body,
            text_body,
        )

    def _blocking_smtp_send(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from_address
        msg["To"] = to
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            if self._smtp_user:
                server.login(self._smtp_user, self._smtp_password)
            server.sendmail(self._from_address, to, msg.as_string())

        logger.info(
            "email_provider.smtp_sent",
            to=to,
            subject=subject,
            host=self._smtp_host,
            port=self._smtp_port,
        )


# ---------------------------------------------------------------------------
# Slack provider
# ---------------------------------------------------------------------------


class SlackProvider(NotificationProvider):
    """Sends messages to a Slack channel via an Incoming Webhook URL."""

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        raise NotImplementedError("SlackProvider does not support email delivery.")

    async def send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"Slack webhook returned {response.status_code}: {response.text}"
            )
        logger.info("slack_provider.webhook_sent", url=url[:60])

    # ------------------------------------------------------------------
    # Helpers for building Slack Block Kit payloads
    # ------------------------------------------------------------------

    @staticmethod
    def build_action_item_payload(
        task: str,
        owner: str,
        due_date: str,
        priority: str,
        tracker_url: str,
        heading: str = "Action item update",
    ) -> dict[str, Any]:
        """Build a Slack Block Kit payload for a single action item."""
        priority_emoji = {"High": ":red_circle:", "Medium": ":large_yellow_circle:", "Low": ":large_green_circle:"}.get(
            priority, ":white_circle:"
        )
        return {
            "text": f"{heading}: {task}",
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": heading, "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Task:*\n{task}"},
                        {"type": "mrkdwn", "text": f"*Owner:*\n{owner}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Due Date:*\n{due_date or 'Not set'}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Priority:*\n{priority_emoji} {priority}",
                        },
                    ],
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View in Synthesis",
                            },
                            "url": tracker_url,
                            "style": "primary",
                        }
                    ],
                },
            ],
        }

    @staticmethod
    def build_digest_payload(
        user_name: str,
        items: list[dict[str, Any]],
        tracker_url: str,
    ) -> dict[str, Any]:
        """Build a Slack digest payload listing multiple open action items."""
        lines = [f"• *{it['task']}* — {it.get('priority','Medium')} priority, due {it.get('due_date') or 'TBD'}" for it in items]
        body = "\n".join(lines)
        return {
            "text": f"Action item digest for {user_name}",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Your open action items, {user_name}",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": body or "_No open items._"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View in Synthesis",
                            },
                            "url": tracker_url,
                            "style": "primary",
                        }
                    ],
                },
            ],
        }


# ---------------------------------------------------------------------------
# Microsoft Teams provider
# ---------------------------------------------------------------------------


class TeamsProvider(NotificationProvider):
    """Sends Adaptive Card messages to a Microsoft Teams channel via Incoming Webhook."""

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        raise NotImplementedError("TeamsProvider does not support email delivery.")

    async def send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"Teams webhook returned {response.status_code}: {response.text}"
            )
        logger.info("teams_provider.webhook_sent", url=url[:60])

    # ------------------------------------------------------------------
    # Adaptive Card builders
    # ------------------------------------------------------------------

    @staticmethod
    def build_action_item_card(
        task: str,
        owner: str,
        due_date: str,
        priority: str,
        tracker_url: str,
        heading: str = "Action item update",
    ) -> dict[str, Any]:
        """Build a Teams Adaptive Card payload for a single action item."""
        priority_color = {"High": "Attention", "Medium": "Warning", "Low": "Good"}.get(
            priority, "Default"
        )
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
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Task", "value": task},
                                    {"title": "Owner", "value": owner},
                                    {"title": "Due Date", "value": due_date or "Not set"},
                                    {"title": "Priority", "value": priority},
                                ],
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Priority level: **{priority}**",
                                "color": priority_color,
                                "wrap": True,
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.OpenUrl",
                                "title": "View in Synthesis",
                                "url": tracker_url,
                            }
                        ],
                    },
                }
            ],
        }

    @staticmethod
    def build_digest_card(
        user_name: str,
        items: list[dict[str, Any]],
        tracker_url: str,
    ) -> dict[str, Any]:
        """Build a Teams Adaptive Card for a digest of open action items."""
        facts = [
            {
                "title": it["task"][:80],
                "value": f"{it.get('priority','Medium')} — due {it.get('due_date') or 'TBD'}",
            }
            for it in items
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
                                "text": f"Open action items for {user_name}",
                                "weight": "Bolder",
                                "size": "Medium",
                                "wrap": True,
                            },
                            {
                                "type": "FactSet",
                                "facts": facts or [{"title": "Status", "value": "No open items"}],
                            },
                        ],
                        "actions": [
                            {
                                "type": "Action.OpenUrl",
                                "title": "View in Synthesis",
                                "url": tracker_url,
                            }
                        ],
                    },
                }
            ],
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider(channel: str) -> NotificationProvider:
    """Return the concrete provider for the given channel name.

    Parameters
    ----------
    channel:
        One of ``"email"``, ``"slack"``, or ``"teams"``.

    Raises
    ------
    ValueError
        If an unknown channel string is supplied.
    """
    channel = channel.lower().strip()
    if channel == "email":
        return EmailProvider()
    if channel == "slack":
        return SlackProvider()
    if channel == "teams":
        return TeamsProvider()
    raise ValueError(f"Unknown notification channel: '{channel}'")
