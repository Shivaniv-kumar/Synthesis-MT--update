"""Google Meet meeting platform connector.

Implements ``MeetingPlatform`` using the Google Meet REST API v2 and the
Google OAuth 2.0 token endpoint.  All HTTP calls are made with
``httpx.AsyncClient``.

Google Meet API docs: https://developers.google.com/meet/api/reference/rest
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

_MEET_API_BASE = "https://meet.googleapis.com/v2"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleMeetPlatform(MeetingPlatform):
    """Google Meet conference recording and transcript connector.

    Parameters
    ----------
    access_token:
        Current OAuth 2.0 Bearer token with the required Google Meet scopes.
    refresh_token:
        Refresh token used to obtain a new access token when the current one
        expires.
    client_id:
        Google OAuth 2.0 client ID.
    client_secret:
        Google OAuth 2.0 client secret.
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
            logger.info("google_meet.token_expired_refreshing", url=url)
            await self.refresh_credentials()
            return await self._get(client, url, params, _retry=False)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        """Parse an ISO-8601 datetime string from Google APIs into UTC."""
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
        """List Google Meet conference records within the given time window.

        Uses the ``/v2/conferenceRecords`` endpoint and filters results by
        ``startTime``.  Pagination is handled via ``nextPageToken``.
        """
        recordings: list[PlatformRecording] = []

        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        until_iso = until.strftime("%Y-%m-%dT%H:%M:%SZ")

        async with httpx.AsyncClient(timeout=30) as client:
            url: str = f"{_MEET_API_BASE}/conferenceRecords"
            params: dict = {
                "filter": f'startTime>="{since_iso}" AND startTime<="{until_iso}"',
                "pageSize": 100,
            }

            while True:
                data = await self._get(client, url, params)
                params_next: dict = {"pageSize": 100}

                for record in data.get("conferenceRecords", []):
                    start_dt = self._parse_dt(record.get("startTime"))
                    end_dt = self._parse_dt(record.get("endTime"))
                    duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())

                    # Google Meet uses the space name as meeting identifier
                    space_name = record.get("space", "")
                    record_name = record.get("name", "")  # e.g. "conferenceRecords/xyz"
                    record_id = record_name.split("/")[-1] if "/" in record_name else record_name

                    recordings.append(
                        PlatformRecording(
                            id=record_id,
                            title=record.get("title", "Google Meet"),
                            started_at=start_dt,
                            ended_at=end_dt,
                            duration_seconds=duration_seconds,
                            # Google Meet recordings are stored in Drive; no direct URL from this API
                            download_url=record.get("name", ""),
                            host_email="",  # not directly available from conferenceRecords
                            meeting_id=space_name or record_id,
                            platform="google_meet",
                        )
                    )

                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break
                params_next["pageToken"] = next_page_token
                params = params_next

        logger.info("google_meet.list_recordings", count=len(recordings))
        return recordings

    async def get_recording(self, recording_id: str) -> PlatformRecording:
        """Fetch a specific conference record by its resource ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{_MEET_API_BASE}/conferenceRecords/{recording_id}"
            data = await self._get(client, url)

            start_dt = self._parse_dt(data.get("startTime"))
            end_dt = self._parse_dt(data.get("endTime"))
            duration_seconds = max(0.0, (end_dt - start_dt).total_seconds())

            record_name = data.get("name", recording_id)
            record_id = record_name.split("/")[-1] if "/" in record_name else record_name

            return PlatformRecording(
                id=record_id,
                title=data.get("title", "Google Meet"),
                started_at=start_dt,
                ended_at=end_dt,
                duration_seconds=duration_seconds,
                download_url=data.get("name", ""),
                host_email="",
                meeting_id=data.get("space", record_id),
                platform="google_meet",
            )

    async def get_transcript(self, recording_id: str) -> Optional[str]:
        """Download and concatenate all transcript entries for a conference record.

        Flow:
        1. List all transcripts for the conference record.
        2. For each transcript, list all transcript entries.
        3. Concatenate entries in order to form a plain-text transcript.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: list transcripts
            transcripts_url = (
                f"{_MEET_API_BASE}/conferenceRecords/{recording_id}/transcripts"
            )
            try:
                transcripts_data = await self._get(client, transcripts_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 403):
                    logger.warning(
                        "google_meet.transcript_unavailable",
                        recording_id=recording_id,
                        status=exc.response.status_code,
                    )
                    return None
                raise

            transcripts = transcripts_data.get("transcripts", [])
            if not transcripts:
                logger.debug("google_meet.no_transcript", recording_id=recording_id)
                return None

            all_text_parts: list[str] = []

            for transcript in transcripts:
                transcript_name = transcript.get("name", "")
                transcript_id = (
                    transcript_name.split("/")[-1]
                    if "/" in transcript_name
                    else transcript_name
                )
                if not transcript_id:
                    continue

                # Step 2: list entries for this transcript
                entries_url: str | None = (
                    f"{_MEET_API_BASE}/conferenceRecords/{recording_id}"
                    f"/transcripts/{transcript_id}/entries"
                )
                entry_params: dict = {"pageSize": 100}

                while entries_url:
                    try:
                        entries_data = await self._get(
                            client, entries_url, entry_params or None
                        )
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (404, 403):
                            break
                        raise

                    entry_params = {"pageSize": 100}

                    for entry in entries_data.get("transcriptEntries", []):
                        participant_name = (
                            entry.get("participant", {})
                            .get("signedinUser", {})
                            .get("displayName", "Speaker")
                        )
                        text = entry.get("text", "")
                        if text:
                            all_text_parts.append(f"{participant_name}: {text}")

                    next_page_token = entries_data.get("nextPageToken")
                    if not next_page_token:
                        entries_url = None
                    else:
                        entry_params["pageToken"] = next_page_token

            if not all_text_parts:
                return None

            transcript_text = "\n".join(all_text_parts)
            logger.info(
                "google_meet.transcript_assembled",
                recording_id=recording_id,
                segments=len(all_text_parts),
                chars=len(transcript_text),
            )
            return transcript_text

    async def get_attendees(self, meeting_id: str) -> list[PlatformAttendee]:
        """Return participants from a Google Meet conference record.

        Uses ``/v2/conferenceRecords/{recordingId}/participants``.  Pagination
        via ``nextPageToken`` is handled.
        """
        attendees: list[PlatformAttendee] = []

        async with httpx.AsyncClient(timeout=30) as client:
            url: str | None = (
                f"{_MEET_API_BASE}/conferenceRecords/{meeting_id}/participants"
            )
            params: dict = {"pageSize": 100}

            while url:
                try:
                    data = await self._get(client, url, params or None)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (404, 403):
                        logger.warning(
                            "google_meet.participants_unavailable",
                            meeting_id=meeting_id,
                            status=exc.response.status_code,
                        )
                        return attendees
                    raise

                params = {"pageSize": 100}

                for participant in data.get("participants", []):
                    signed_in = participant.get("signedinUser", {})
                    anon = participant.get("anonymousUser", {})
                    phone = participant.get("phoneUser", {})

                    if signed_in:
                        name = signed_in.get("displayName", "Unknown")
                        # email is not directly returned; use displayName as email placeholder
                        email = signed_in.get("email", signed_in.get("displayName", ""))
                    elif anon:
                        name = anon.get("displayName", "Anonymous")
                        email = ""
                    elif phone:
                        name = phone.get("displayName", "Phone User")
                        email = ""
                    else:
                        name = "Unknown"
                        email = ""

                    # Earliest join / latest leave come from participantSessions
                    sessions = participant.get("participantSessions", [])
                    join_time: Optional[datetime] = None
                    leave_time: Optional[datetime] = None
                    for session in sessions:
                        sj = self._parse_dt(session.get("startTime"))
                        sl = self._parse_dt(session.get("endTime")) if session.get("endTime") else None
                        if join_time is None or sj < join_time:
                            join_time = sj
                        if sl and (leave_time is None or sl > leave_time):
                            leave_time = sl

                    attendees.append(
                        PlatformAttendee(
                            name=name,
                            email=email,
                            join_time=join_time,
                            leave_time=leave_time,
                        )
                    )

                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    url = None
                else:
                    params["pageToken"] = next_page_token

        logger.info(
            "google_meet.attendees_fetched", meeting_id=meeting_id, count=len(attendees)
        )
        return attendees

    async def refresh_credentials(self) -> None:
        """Exchange the current refresh token for a new Google access token.

        Posts to the Google OAuth 2.0 token endpoint with ``grant_type=refresh_token``.
        Updates ``self.access_token`` in place.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            token_data = response.json()

        self.access_token = token_data["access_token"]
        # Google does not always rotate the refresh token
        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]

        logger.info("google_meet.credentials_refreshed")
