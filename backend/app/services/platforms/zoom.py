"""Zoom meeting platform connector.

Implements ``MeetingPlatform`` using the Zoom REST API v2.  All HTTP calls are
made with ``httpx.AsyncClient``.  OAuth token refresh is handled automatically:
any endpoint that receives a 401 will refresh credentials once and retry.

Zoom API docs: https://developers.zoom.us/docs/api/
"""

from __future__ import annotations

import base64
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

_ZOOM_API_BASE = "https://api.zoom.us/v2"
_ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"


class ZoomPlatform(MeetingPlatform):
    """Zoom cloud recording and transcript connector.

    Parameters
    ----------
    access_token:
        Current OAuth 2.0 Bearer token.
    refresh_token:
        Refresh token used to obtain a new access token when the current one
        expires.
    client_id:
        Zoom OAuth app client ID.
    client_secret:
        Zoom OAuth app client secret.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

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
            logger.info("zoom.token_expired_refreshing", url=url)
            await self.refresh_credentials()
            return await self._get(client, url, params, _retry=False)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        """Parse an ISO-8601 datetime string from Zoom into an aware UTC datetime."""
        if not value:
            return datetime.now(tz=timezone.utc)
        # Zoom returns dates like "2024-01-15T10:30:00Z"
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
        """List cloud recordings for the authenticated user within a date window.

        Only MP4 recording files are included (file_type=MP4).  Zoom returns up
        to ``page_size=300`` results per request; pagination is handled via
        ``next_page_token``.
        """
        recordings: list[PlatformRecording] = []

        async with httpx.AsyncClient(timeout=30) as client:
            params: dict = {
                "from": since.strftime("%Y-%m-%d"),
                "to": until.strftime("%Y-%m-%d"),
                "page_size": 300,
            }
            url = f"{_ZOOM_API_BASE}/users/me/recordings"

            while True:
                data = await self._get(client, url, params)
                meetings = data.get("meetings", [])

                for meeting in meetings:
                    recording_files = meeting.get("recording_files", [])
                    # Find the primary MP4 file
                    mp4_files = [
                        f for f in recording_files
                        if f.get("file_type", "").upper() == "MP4"
                        and f.get("status") == "completed"
                    ]
                    if not mp4_files:
                        continue

                    # Use the first (largest) MP4 as the canonical recording
                    mp4 = mp4_files[0]
                    started_at = self._parse_dt(meeting.get("start_time"))
                    ended_at = self._parse_dt(mp4.get("recording_end"))
                    duration_seconds = float(meeting.get("duration", 0) * 60)

                    recordings.append(
                        PlatformRecording(
                            id=mp4.get("id", ""),
                            title=meeting.get("topic", "Zoom Meeting"),
                            started_at=started_at,
                            ended_at=ended_at,
                            duration_seconds=duration_seconds,
                            download_url=mp4.get("download_url", ""),
                            host_email=meeting.get("host_email", ""),
                            meeting_id=str(meeting.get("uuid", meeting.get("id", ""))),
                            platform="zoom",
                        )
                    )

                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break
                params["next_page_token"] = next_page_token

        logger.info("zoom.list_recordings", count=len(recordings))
        return recordings

    async def get_recording(self, recording_id: str) -> PlatformRecording:
        """Fetch metadata for a specific recording file.

        Zoom does not expose a direct recording-file endpoint, so we query the
        meeting recordings and find the matching file by ID.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            # recording_id is a file UUID; we need to search via the meeting
            # For a direct lookup we use the meeting recordings endpoint with
            # the recording_id treated as the meeting ID (common in webhook payloads)
            url = f"{_ZOOM_API_BASE}/meetings/{recording_id}/recordings"
            data = await self._get(client, url)

            recording_files = data.get("recording_files", [])
            mp4_files = [
                f for f in recording_files
                if f.get("file_type", "").upper() == "MP4"
                and f.get("status") == "completed"
            ]
            if not mp4_files:
                raise ValueError(f"No MP4 recording found for ID {recording_id}")

            mp4 = mp4_files[0]
            started_at = self._parse_dt(data.get("start_time"))
            ended_at = self._parse_dt(mp4.get("recording_end"))
            duration_seconds = float(data.get("duration", 0) * 60)

            return PlatformRecording(
                id=mp4.get("id", recording_id),
                title=data.get("topic", "Zoom Meeting"),
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=duration_seconds,
                download_url=mp4.get("download_url", ""),
                host_email=data.get("host_email", ""),
                meeting_id=str(data.get("uuid", data.get("id", recording_id))),
                platform="zoom",
            )

    async def get_transcript(self, recording_id: str) -> Optional[str]:
        """Download the VTT transcript for a meeting, if one was generated.

        Queries the meeting recordings endpoint to find a VTT file, then
        downloads its content.

        Parameters
        ----------
        recording_id:
            The Zoom meeting UUID or ID (not the recording file ID).
        """
        async with httpx.AsyncClient(timeout=60) as client:
            url = f"{_ZOOM_API_BASE}/meetings/{recording_id}/recordings"
            try:
                data = await self._get(client, url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("zoom.meeting_not_found", meeting_id=recording_id)
                    return None
                raise

            recording_files = data.get("recording_files", [])
            # Look for a VTT transcript file
            vtt_files = [
                f for f in recording_files
                if f.get("file_type", "").upper() in ("TRANSCRIPT", "VTT")
                or f.get("file_extension", "").lower() == "vtt"
            ]
            if not vtt_files:
                logger.debug("zoom.no_transcript", meeting_id=recording_id)
                return None

            vtt_url = vtt_files[0].get("download_url", "")
            if not vtt_url:
                return None

            # Download the VTT content using the bearer token
            vtt_response = await client.get(
                vtt_url,
                headers=self._auth_headers(),
                follow_redirects=True,
            )
            if vtt_response.status_code == 401:
                await self.refresh_credentials()
                vtt_response = await client.get(
                    vtt_url,
                    headers=self._auth_headers(),
                    follow_redirects=True,
                )
            vtt_response.raise_for_status()

            transcript_text = vtt_response.text
            logger.info(
                "zoom.transcript_downloaded",
                meeting_id=recording_id,
                chars=len(transcript_text),
            )
            return transcript_text

    async def get_attendees(self, meeting_id: str) -> list[PlatformAttendee]:
        """Return participants from the Zoom meeting participants report.

        Zoom paginates this endpoint with ``next_page_token``; all pages are
        fetched and merged.
        """
        attendees: list[PlatformAttendee] = []

        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{_ZOOM_API_BASE}/report/meetings/{meeting_id}/participants"
            params: dict = {"page_size": 300}

            while True:
                try:
                    data = await self._get(client, url, params)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        logger.warning("zoom.participants_not_found", meeting_id=meeting_id)
                        return attendees
                    raise

                for participant in data.get("participants", []):
                    attendees.append(
                        PlatformAttendee(
                            name=participant.get("name", "Unknown"),
                            email=participant.get("user_email", ""),
                            join_time=self._parse_dt(participant.get("join_time"))
                            if participant.get("join_time")
                            else None,
                            leave_time=self._parse_dt(participant.get("leave_time"))
                            if participant.get("leave_time")
                            else None,
                        )
                    )

                next_page_token = data.get("next_page_token")
                if not next_page_token:
                    break
                params["next_page_token"] = next_page_token

        logger.info("zoom.attendees_fetched", meeting_id=meeting_id, count=len(attendees))
        return attendees

    async def refresh_credentials(self) -> None:
        """Exchange the current refresh token for a new access token.

        Uses HTTP Basic authentication with the Zoom client credentials.
        Updates ``self.access_token`` and ``self.refresh_token`` in place.
        """
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _ZOOM_OAUTH_URL,
                params={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
            token_data = response.json()

        self.access_token = token_data["access_token"]
        # Zoom rotates the refresh token on each use
        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]

        logger.info("zoom.credentials_refreshed")
