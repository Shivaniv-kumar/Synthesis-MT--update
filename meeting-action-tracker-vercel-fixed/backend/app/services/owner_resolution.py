"""Owner resolution — map the raw owner string extracted by Claude to a known
attendee name and, optionally, to a user email address.

This module provides two public entry points:

``resolve_owner(owner_label, attendees, speaker_map)``
    Full resolution pipeline.  Returns ``(matched_name, matched_email_or_none)``.

``fuzzy_match_name(name, candidates, threshold)``
    Pure fuzzy-match helper using difflib.SequenceMatcher.

The OwnerResolutionService class provides a higher-level async interface that
additionally performs database lookups against the User table.
"""

from __future__ import annotations

import difflib
import re
import string
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# First-person tokens that indicate the speaker committed to the action
# ---------------------------------------------------------------------------

_FIRST_PERSON_TOKENS: frozenset[str] = frozenset(
    {
        "i",
        "me",
        "ill",          # "I'll" after punctuation stripping
        "i will",
        "id",           # "I'd" after stripping
        "i can",
        "i can handle",
        "ill handle",
        "ill take",
        "i will take",
        "ill do",
        "i will do",
        "my",
        "mine",
    }
)


# ---------------------------------------------------------------------------
# Public pure helpers
# ---------------------------------------------------------------------------


def fuzzy_match_name(
    name: str,
    candidates: list[str],
    threshold: float = 0.8,
) -> Optional[str]:
    """Return the best-matching candidate for *name*, or ``None``.

    Comparison uses ``difflib.SequenceMatcher`` on normalized (lowercased,
    punctuation-stripped) strings.  Only candidates with a ratio >= *threshold*
    are returned.  Ties are broken by list order (first-in wins).

    Parameters
    ----------
    name:
        The raw owner label to look up.
    candidates:
        Authoritative list of known names (e.g. attendee display names).
    threshold:
        Minimum SequenceMatcher ratio to accept (default 0.8).
    """
    if not name or not candidates:
        return None

    name_norm = _normalize(name)
    best_match: Optional[str] = None
    best_ratio = 0.0

    for candidate in candidates:
        candidate_norm = _normalize(candidate)
        ratio = difflib.SequenceMatcher(None, name_norm, candidate_norm).ratio()
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best_match = candidate

    return best_match


def resolve_owner(
    owner_label: str,
    attendees: Optional[list[str]] = None,
    speaker_map: Optional[dict[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve *owner_label* to ``(matched_name, matched_email_or_none)``.

    Resolution order
    ----------------
    1. First-person pronoun detection → look up the active speaker in
       *speaker_map* (key ``"__current_speaker__"`` or ``"current_speaker"``).
    2. Exact case-insensitive match against *attendees*.
    3. Fuzzy match against *attendees* (threshold 0.8).
    4. Fuzzy match against *speaker_map* keys, then resolve via attendees.
    5. Return ``(None, None)`` if nothing resolves.

    Parameters
    ----------
    owner_label:
        The raw owner string from the extraction output (e.g. ``"Alice"``,
        ``"I'll"``, ``"Bob Smith"``).
    attendees:
        List of attendee display names for the meeting.
    speaker_map:
        Mapping of speaker label → display name (and optionally email), e.g.
        ``{"Alice": "Alice Johnson <alice@example.com>"}`` or simply
        ``{"Alice": "Alice Johnson"}``.  A special key
        ``"__current_speaker__"`` may be set to the speaker label active at
        the time of a first-person commitment.
    """
    attendees = attendees or []
    speaker_map = speaker_map or {}

    # ------------------------------------------------------------------ #
    # 1. First-person resolution
    # ------------------------------------------------------------------ #
    if _is_first_person(owner_label):
        speaker_entry = (
            speaker_map.get("__current_speaker__")
            or speaker_map.get("current_speaker")
        )
        if speaker_entry:
            name, email = _split_name_email(speaker_entry)
            if attendees:
                canonical = fuzzy_match_name(name, attendees)
                if canonical:
                    return canonical, email
            return name, email
        return None, None

    # ------------------------------------------------------------------ #
    # 2. Exact match (case-insensitive)
    # ------------------------------------------------------------------ #
    owner_lower = owner_label.strip().lower()
    for attendee in attendees:
        if attendee.strip().lower() == owner_lower:
            email = _email_from_speaker_map(attendee, speaker_map)
            return attendee, email

    # ------------------------------------------------------------------ #
    # 3. Fuzzy match against attendees
    # ------------------------------------------------------------------ #
    matched = fuzzy_match_name(owner_label, attendees)
    if matched:
        email = _email_from_speaker_map(matched, speaker_map)
        return matched, email

    # ------------------------------------------------------------------ #
    # 4. Fuzzy match against speaker_map keys
    # ------------------------------------------------------------------ #
    if speaker_map:
        speaker_keys = [
            k for k in speaker_map if k not in ("__current_speaker__", "current_speaker")
        ]
        matched_key = fuzzy_match_name(owner_label, speaker_keys)
        if matched_key:
            entry = speaker_map[matched_key]
            name, email = _split_name_email(entry)
            if attendees:
                canonical = fuzzy_match_name(name, attendees)
                if canonical:
                    return canonical, email
            return name, email

    return None, None


# ---------------------------------------------------------------------------
# OwnerResolutionService — async DB-backed resolution
# ---------------------------------------------------------------------------


class OwnerResolutionService:
    """Resolve a raw owner string to a User record or attendee label.

    Resolution order:
    1. Detect first-person ("I'll do it") → resolve to *speaker* if provided.
    2. Exact match against attendee list (case-insensitive).
    3. Fuzzy match against attendee list (difflib, cutoff 0.8).
    4. Exact match against User.display_name in the tenant (DB lookup).
    5. Fuzzy match against User.display_name in the tenant.
    6. Return None user_id + raw label if nothing matches.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def resolve(
        self,
        raw_owner: str,
        attendees: list[str],
        speaker: Optional[str] = None,
    ) -> tuple[Optional[UUID], str]:
        """Return ``(user_id_or_None, display_label)`` for *raw_owner*.

        Parameters
        ----------
        raw_owner:
            The value in the ``owner`` field returned by the extraction model.
        attendees:
            List of attendee names from the meeting record.
        speaker:
            The speaker label at the point where the action item was found
            (used to resolve first-person pronouns).
        """
        if not raw_owner or raw_owner.strip().lower() in ("unassigned", ""):
            return None, raw_owner or "Unassigned"

        stripped = raw_owner.strip()

        # 1. First-person detection
        if _is_first_person(stripped) and speaker:
            resolved_label = speaker
            user_id = await self._lookup_user_by_name(resolved_label)
            return user_id, resolved_label

        # 2. Exact match against attendees
        for attendee in attendees:
            if attendee.strip().lower() == stripped.lower():
                user_id = await self._lookup_user_by_name(attendee)
                return user_id, attendee

        # 3. Fuzzy match against attendees
        matched = fuzzy_match_name(stripped, attendees, threshold=0.8)
        if matched:
            user_id = await self._lookup_user_by_name(matched)
            return user_id, matched

        # 4 & 5. Database lookup by display_name
        users = await self._fetch_tenant_users()
        user_names = [u.display_name for u in users]

        # 4. Exact match (case-insensitive)
        for u in users:
            if u.display_name.strip().lower() == stripped.lower():
                return u.id, u.display_name

        # 5. Fuzzy match
        matched_name = fuzzy_match_name(stripped, user_names, threshold=0.8)
        if matched_name:
            for u in users:
                if u.display_name == matched_name:
                    return u.id, u.display_name

        # 6. Fallback — keep the raw label
        return None, stripped

    async def _lookup_user_by_name(self, name: str) -> Optional[UUID]:
        result = await self._session.execute(
            select(User).where(
                User.tenant_id == self._tenant_id,
                User.display_name.ilike(name),
                User.is_active.is_(True),
            )
        )
        user = result.scalars().first()
        return user.id if user else None

    async def _fetch_tenant_users(self) -> list[User]:
        result = await self._session.execute(
            select(User).where(
                User.tenant_id == self._tenant_id,
                User.is_active.is_(True),
            )
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """Lowercase and strip punctuation for comparison."""
    return s.lower().translate(str.maketrans("", "", string.punctuation)).strip()


def _is_first_person(label: str) -> bool:
    """Return True if *label* is a first-person pronoun or contraction."""
    return _normalize(label) in _FIRST_PERSON_TOKENS


def _split_name_email(entry: str) -> tuple[str, Optional[str]]:
    """Parse ``"Full Name <email@example.com>"`` or plain ``"Full Name"`` entries.

    Returns (name, email_or_none).
    """
    match = re.match(r"^(.+?)\s*<([^>]+)>\s*$", entry.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return entry.strip(), None


def _email_from_speaker_map(
    name: str,
    speaker_map: dict[str, str],
) -> Optional[str]:
    """Search *speaker_map* values for a matching name and return any email found."""
    for entry in speaker_map.values():
        entry_name, entry_email = _split_name_email(entry)
        if _normalize(entry_name) == _normalize(name) and entry_email:
            return entry_email
    return None
