"""Platform connector factory.

Decrypts stored OAuth credentials and instantiates the appropriate
``MeetingPlatform`` subclass based on the platform name.

Usage::

    from app.services.platforms.factory import get_platform

    platform = get_platform("zoom", platform_config)
    recordings = await platform.list_recordings(since, until)
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.platforms.base import MeetingPlatform
from app.utils.encryption import decrypt_credentials

logger = logging.getLogger(__name__)


def get_platform(platform_name: str, config: Any) -> MeetingPlatform:
    """Instantiate and return the correct ``MeetingPlatform`` for *platform_name*.

    Decrypts ``config.credentials_encrypted`` using the application secret key
    and passes the resulting credentials to the platform-specific constructor.

    Parameters
    ----------
    platform_name:
        One of ``"zoom"``, ``"teams"``, or ``"google_meet"``.
    config:
        A ``PlatformConfig`` ORM instance (or any object) with a
        ``credentials_encrypted`` attribute containing a Fernet-encrypted JSON
        blob of OAuth credentials.

    Returns
    -------
    MeetingPlatform
        A fully initialised platform connector ready to make API calls.

    Raises
    ------
    ValueError
        If *platform_name* is not one of the supported values, or if required
        credentials keys are missing from the decrypted blob.
    """
    credentials: dict = decrypt_credentials(config.credentials_encrypted)
    platform_name_lower = platform_name.lower()

    if platform_name_lower == "zoom":
        from app.services.platforms.zoom import ZoomPlatform

        _require_keys(credentials, ["access_token", "refresh_token", "client_id", "client_secret"], "zoom")
        return ZoomPlatform(
            access_token=credentials["access_token"],
            refresh_token=credentials["refresh_token"],
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
        )

    if platform_name_lower == "teams":
        from app.services.platforms.teams import TeamsPlatform

        _require_keys(
            credentials,
            ["access_token", "refresh_token", "tenant_azure_id", "client_id", "client_secret"],
            "teams",
        )
        return TeamsPlatform(
            access_token=credentials["access_token"],
            refresh_token=credentials["refresh_token"],
            tenant_azure_id=credentials["tenant_azure_id"],
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
        )

    if platform_name_lower == "google_meet":
        from app.services.platforms.google_meet import GoogleMeetPlatform

        _require_keys(
            credentials,
            ["access_token", "refresh_token", "client_id", "client_secret"],
            "google_meet",
        )
        return GoogleMeetPlatform(
            access_token=credentials["access_token"],
            refresh_token=credentials["refresh_token"],
            client_id=credentials["client_id"],
            client_secret=credentials["client_secret"],
        )

    raise ValueError(
        f"Unknown platform '{platform_name}'. "
        "Supported values: 'zoom', 'teams', 'google_meet'."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_keys(credentials: dict, required: list[str], platform: str) -> None:
    """Raise ``ValueError`` if any required key is absent from *credentials*."""
    missing = [k for k in required if k not in credentials]
    if missing:
        raise ValueError(
            f"Missing required credential keys for platform '{platform}': {missing}"
        )
