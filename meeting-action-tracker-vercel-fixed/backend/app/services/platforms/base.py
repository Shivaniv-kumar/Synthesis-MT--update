"""Abstract base classes and shared dataclasses for meeting platform connectors.

All platform implementations (Zoom, Teams, Google Meet) must subclass
``MeetingPlatform`` and implement every abstract method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Shared data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class PlatformRecording:
    """Canonical representation of a cloud recording returned by any platform."""

    id: str
    """Platform-native recording identifier."""

    title: str
    """Meeting / recording title."""

    started_at: datetime
    """UTC datetime when the recording started."""

    ended_at: datetime
    """UTC datetime when the recording ended."""

    duration_seconds: float
    """Duration of the recording in seconds."""

    download_url: str
    """Authenticated URL to download the recording file."""

    host_email: str
    """Email address of the meeting host."""

    meeting_id: str
    """Platform-native meeting identifier (may differ from recording id)."""

    platform: str
    """Platform name: 'zoom' | 'teams' | 'google_meet'."""


@dataclass
class PlatformAttendee:
    """Canonical representation of a single meeting attendee."""

    name: str
    """Display name of the attendee."""

    email: str
    """Email address of the attendee."""

    join_time: Optional[datetime] = field(default=None)
    """UTC datetime when the attendee joined, or None if unavailable."""

    leave_time: Optional[datetime] = field(default=None)
    """UTC datetime when the attendee left, or None if unavailable."""


# ---------------------------------------------------------------------------
# Abstract platform interface
# ---------------------------------------------------------------------------


class MeetingPlatform(ABC):
    """Abstract base class for meeting platform connectors.

    Subclasses encapsulate all OAuth credential management and HTTP calls for a
    specific meeting provider.  The public interface is intentionally narrow —
    callers should only need to interact with the four core retrieval methods
    and ``refresh_credentials``.

    All methods are coroutines; callers must ``await`` them inside an active
    asyncio event loop.
    """

    @abstractmethod
    async def list_recordings(
        self,
        since: datetime,
        until: datetime,
    ) -> list[PlatformRecording]:
        """Return all cloud recordings whose start time falls within the window.

        Parameters
        ----------
        since:
            Inclusive lower bound (UTC).
        until:
            Inclusive upper bound (UTC).

        Returns
        -------
        list[PlatformRecording]
            Zero or more recordings ordered by ``started_at`` ascending.
        """

    @abstractmethod
    async def get_recording(self, recording_id: str) -> PlatformRecording:
        """Fetch a single recording by its platform-native identifier.

        Parameters
        ----------
        recording_id:
            Platform-native recording ID.

        Returns
        -------
        PlatformRecording
            The requested recording.

        Raises
        ------
        ValueError
            If the recording cannot be found.
        """

    @abstractmethod
    async def get_transcript(self, recording_id: str) -> Optional[str]:
        """Download and return the transcript for a recording, if available.

        Parameters
        ----------
        recording_id:
            Platform-native recording ID.  For some platforms (Teams, Google
            Meet) this is the meeting ID rather than the file ID.

        Returns
        -------
        Optional[str]
            Raw transcript text (VTT or plain text), or ``None`` if the
            platform has not generated a transcript yet.
        """

    @abstractmethod
    async def get_attendees(self, meeting_id: str) -> list[PlatformAttendee]:
        """Return the participant / attendance list for a meeting.

        Parameters
        ----------
        meeting_id:
            Platform-native meeting identifier.

        Returns
        -------
        list[PlatformAttendee]
            Zero or more attendees.
        """

    @abstractmethod
    async def refresh_credentials(self) -> None:
        """Exchange the stored refresh token for a new access token.

        Implementations should update ``self.access_token`` (and any other
        relevant attributes) in place.  This method is called automatically
        when a 401 response is received from the platform API; callers should
        not normally need to invoke it directly.
        """
