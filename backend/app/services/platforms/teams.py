"""Microsoft Teams meeting platform connector.

Implements ``MeetingPlatform`` using the Microsoft Graph API.  All HTTP calls
are made with ``httpx.AsyncClient``.  OAuth token refresh uses the
tenant-specific Microsoft identity platform endpoint.

Graph API docs: https://learn.microsoft.com/en-us/graph/api/resources/onlinemeeting
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.services.platforms.base import (
    MeetingPlatform,
    PlatformAttendee,
    PlatformRecording,
)

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MS_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


class TeamsPlatform(MeetingPlatform):
    """Microsoft Teams online meeting and transcript connector.

    Parameters
    ----------
    access_token:
        Current Microsoft Graph OAuth 2.0 Bearer token.
    refresh_token:
        Refresh token used to obtain a new access token when the current one
        expires.
    tenant_azure_id:
        Azure AD tenant (directory) ID — used in the token refresh endpoint.
    client_id:
        Azure AD application (client) ID.
    client_secret:
        Azure AD application client secret.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        tenant_azure_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.tenant_azure_id = tenant_azure_id
        self.client_id = client_id
        self.client_secret = client_secret

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict | None = None,
        *,
        _retry: bool = True,
    ) -> dict:
        """Perform an authenticated GET request, refreshing the token on 401."""
        response = await client.get(url, headers=self._auth_headers(), params=params)
        if response.status_code == 401 and _retry:
            logger.info("teams.token_expired_refreshing", url=url)
            await self.refresh_credentials()
            return await self._get(client, url, params, _retry=False)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        """Parse an ISO-8601 datetime string from Graph API into UTC."""
        if not value:
            return datetime.now(tz=timezone.utc)
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    # ------------------------------------------------------------------
    # MeetingPlatform interface
    # ------------------------------------------------------------------

    async def list_recordings(
        self,
        since: datetime,
        until: datetime,
    ) -> list[PlatformRecording]:
        """List online meetings within the given date/time window.

        The Graph ``/me/onlineMeetings`` endpoint does not directly expose
        cloud recording files, so we return a ``PlatformRecording`` built from
        the meeting metadata.  The ``download_url`` is set to the ``joinUrl``
        which can be used to look up the recording later.

        Pagination is handled via the ``@odata.nextLink`` property.
        """
        recordings: list[PlatformRecording] = []

        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        until_iso = until.strftime("%Y-%m-%dT%H:%M:%SZ")

        async with httpx.AsyncClient(timeout=30) as client:
            url: str | None = f"{_GRAPH_BASE}/me/onlineMeetings"
            params: dict = {
                "$filter": (
                    f"startDateTime ge {since_iso} and startDateTime le {until_iso}"
                ),
                "$top": 50,
            }

            while url:
                data = await self._get(client, url, params)
                params = {}  # params are encoded in nextLink on subsequent pages

                for meeting in data.get("value", []):
                    start_dt = self._parse_dt(meeting.get("startDateTime"))
                    end_dt = self._parse_dt(meeting.get("endDateTime"))
                    duration_seconds = max(
                        0.0, (end_dt - start_dt).total_seconds()
                    )

                    organizer = meeting.get("participants", {}).get("organizer", {})
                    host_email = (
                        organizer.get("upn", "")
                        or organizer.get("identity", {})
                        .get("user", {})
                        .get("displayName", "")
                    )

                    recordings.append(
                        PlatformRecording(
                            id=meeting.get("id", ""),
                            title=meeting.get("subject", "Teams Meeting"),
                            started_at=start_dt,
                            ended_at=end_dt,
                            duration_seconds=duration_seconds,
                            download_url=meeting.get("joinUrl", ""),
                            host_email=host_email,
                            meeting_id=meeting.get("id", ""),
                            platform="teams",
                        )
                    )

                url = data.get("@odata.nextLink")

        logger.info("teams.list_recordings", count=len(recordings))
        return recordings

    async def get_recording(self, recording_id: str) -> PlatformRecording:
        """Fetch metadata for a specific online meeting by its Graph meeting ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{_GRAPH_BASE}/me/onlineMeetings/{recording_id}"
            data = await self._get(client, url)

            start_dt = self._parse_dt(data.get("startDateTime"))
            end_dt = self._parse_dt(data.get("endDateTime"))
            duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())

            organizer = data.get("participants", {}).get("organizer", {})
            host_email = (
                organizer.get("upn", "")
                or organizer.get("identity", {}).get("user", {}).get("displayName", "")
            )

            return PlatformRecording(
                id=data.get("id", recording_id),
                title=data.get("subject", "Teams Meeting"),
                started_at=start_dt,
                ended_at=end_dt,
                duration_seconds=duration_seconds,
                download_url=data.get("joinUrl", ""),
                host_email=host_email,
                meeting_id=data.get("id", recording_id),
                platform="teams",
            )

    async def get_transcript(self, recording_id: str) -> Optional[str]:
        """Download the VTT transcript for a Teams meeting, if one is available.

        Flow:
        1. List all transcripts for the meeting.
        2. Select the first available transcript.
        3. Download VTT content via the ``/content?$format=text/vtt`` endpoint.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: list transcripts
            transcripts_url = (
                f"{_GRAPH_BASE}/me/onlineMeetings/{recording_id}/transcripts"
            )
            try:
                transcripts_data = await self._get(client, transcripts_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 403):
                    logger.warning(
                        "teams.transcript_unavailable",
                        meeting_id=recording_id,
                        status=exc.response.status_code,
                    )
                    return None
                raise

            transcripts = transcripts_data.get("value", [])
            if not transcripts:
                logger.debug("teams.no_transcript", meeting_id=recording_id)
                return None

            # Step 2: use the first transcript
            transcript_id = transcripts[0].get("id", "")
            if not transcript_id:
                return None

            # Step 3: download VTT content
            content_url = (
                f"{_GRAPH_BASE}/me/onlineMeetings/{recording_id}"
                f"/transcripts/{transcript_id}/content"
            )
            content_response = await client.get(
                content_url,
                headers=self._auth_headers(),
                params={"$format": "text/vtt"},
                follow_redirects=True,
            )
            if content_response.status_code == 401:
                await self.refresh_credentials()
                content_response = await client.get(
                    content_url,
                    headers=self._auth_headers(),
                    params={"$format": "text/vtt"},
                    follow_redirects=True,
                )
            content_response.raise_for_status()

            transcript_text = content_response.text
            logger.info(
                "teams.transcript_downloaded",
                meeting_id=recording_id,
                transcript_id=transcript_id,
                chars=len(transcript_text),
            )
            return transcript_text

    async def get_attendees(self, meeting_id: str) -> list[PlatformAttendee]:
        """Return attendees from the Teams meeting attendance reports.

        Fetches attendance report records and maps them to ``PlatformAttendee``.
        Pagination via ``@odata.nextLink`` is handled.
        """
        attendees: list[PlatformAttendee] = []

        async with httpx.AsyncClient(timeout=30) as client:
            # Get attendance reports for the meeting
            reports_url = (
                f"{_GRAPH_BASE}/me/onlineMeetings/{meeting_id}/attendanceReports"
            )
            try:
                reports_data = await self._get(client, reports_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 403):
                    logger.warning(
                        "teams.attendance_unavailable",
                        meeting_id=meeting_id,
                        status=exc.response.status_code,
                    )
                    return attendees
                raise

            reports = reports_data.get("value", [])
            if not reports:
                return attendees

            # Use the most recent (first) attendance report
            report_id = reports[0].get("id", "")
            if not report_id:
                return attendees

            # Fetch attendee records from the report
            records_url: str | None = (
                f"{_GRAPH_BASE}/me/onlineMeetings/{meeting_id}"
                f"/attendanceReports/{report_id}/attendanceRecords"
            )
            params: dict = {}

            while records_url:
                records_data = await self._get(client, records_url, params or None)
                params = {}

                for record in records_data.get("value", []):
                    identity = record.get("identity", {})
                    attendees.append(
                        PlatformAttendee(
                            name=identity.get("displayName", "Unknown"),
                            email=identity.get("tenantId", "") or identity.get("id", ""),
                            join_time=self._parse_dt(record.get("attendanceIntervals", [{}])[0].get("joinDateTime"))
                            if record.get("attendanceIntervals")
                            else None,
                            leave_time=self._parse_dt(record.get("attendanceIntervals", [{}])[0].get("leaveDateTime"))
                            if record.get("attendanceIntervals")
                            else None,
                        )
                    )

                records_url = records_data.get("@odata.nextLink")

        logger.info(
            "teams.attendees_fetched", meeting_id=meeting_id, count=len(attendees)
        )
        return attendees

    async def refresh_credentials(self) -> None:
        """Exchange the current refresh token for a new Microsoft Graph access token.

        Uses the tenant-specific token endpoint with the ``refresh_token`` grant
        type.  Updates ``self.access_token`` and ``self.refresh_token`` in place.
        """
        token_url = _MS_TOKEN_URL.format(tenant_id=self.tenant_azure_id)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": (
                        "https://graph.microsoft.com/OnlineMeetings.Read "
                        "https://graph.microsoft.com/OnlineMeetingTranscript.Read.All "
                        "offline_access"
                    ),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

        self.access_token = token_data["access_token"]
        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]

        logger.info("teams.credentials_refreshed")
