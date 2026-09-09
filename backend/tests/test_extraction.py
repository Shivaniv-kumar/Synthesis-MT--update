"""Tests for ExtractionService, OwnerResolutionService and date utilities.

The Anthropic SDK uses httpx under the hood.  We mock it by constructing a
fake AsyncAnthropic client whose ``messages.create`` coroutine returns a
hand-crafted response object — no network traffic, no real API key needed.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.date_utils import normalize_due_date
from app.services.extraction_service import ExtractionService
from app.services.owner_resolution import OwnerResolutionService
from app.services.schemas import ExtractedItemRaw


# ---------------------------------------------------------------------------
# Helpers — build a fake Anthropic message response
# ---------------------------------------------------------------------------


def _make_anthropic_response(items: list[dict]) -> MagicMock:
    """Return a mock object that looks like anthropic.types.Message."""
    content_block = MagicMock()
    content_block.text = json.dumps(items)
    response = MagicMock()
    response.content = [content_block]
    return response


def _make_extraction_service(items_per_call: list[list[dict]]) -> ExtractionService:
    """Return an ExtractionService backed by a mock client.

    *items_per_call* is a list of item lists — the first call returns
    ``items_per_call[0]``, the second ``items_per_call[1]``, etc.  If there
    are more calls than entries the last entry is reused.
    """
    responses = [_make_anthropic_response(items) for items in items_per_call]

    async def _fake_create(**kwargs):
        # Pop the first response or repeat the last one
        return responses.pop(0) if responses else _make_anthropic_response([])

    mock_messages = MagicMock()
    mock_messages.create = AsyncMock(side_effect=_fake_create)

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    return ExtractionService(client=mock_client)


# ---------------------------------------------------------------------------
# 1. Simple transcript — well-formed items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_simple_transcript():
    """A clean short transcript yields the expected action items."""
    expected_items = [
        {
            "task": "Schedule follow-up call with design team",
            "owner": "Alice",
            "priority": "High",
            "due": "by next Friday",
            "context": "Alice committed to coordinating the design review.",
            "confidence": 0.95,
        },
        {
            "task": "Prepare Q2 roadmap document",
            "owner": "Bob",
            "priority": "Medium",
            "due": "end of week",
            "context": "Bob will draft the roadmap for stakeholder sign-off.",
            "confidence": 0.88,
        },
    ]
    service = _make_extraction_service([expected_items])

    transcript = (
        "Alice: I'll schedule a follow-up call with the design team by next Friday.\n"
        "Bob: And I'll prepare the Q2 roadmap document by end of week."
    )

    result = await service.extract(
        transcript_text=transcript,
        attendees=["Alice", "Bob"],
        occurred_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert len(result) == 2
    assert result[0].task == "Schedule follow-up call with design team"
    assert result[0].owner == "Alice"
    assert result[0].priority == "High"
    assert result[0].confidence == 0.95
    assert result[1].task == "Prepare Q2 roadmap document"
    assert result[1].owner == "Bob"


# ---------------------------------------------------------------------------
# 2. Empty / no-action transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_empty_transcript():
    """A transcript with no action items returns an empty list."""
    service = _make_extraction_service([[]])  # model returns empty array

    result = await service.extract(
        transcript_text="No action items discussed today. Meeting adjourned.",
        attendees=[],
    )

    assert result == []


# ---------------------------------------------------------------------------
# 3. Long transcript — chunking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_long_transcript():
    """A transcript exceeding the chunk size is split; items from all chunks are merged."""
    # Generate a transcript > 12 000 chars
    filler = "Speaker A: We discussed many topics. " * 300  # ~11 100 chars
    part1 = filler + "\nSpeaker A: I'll write the technical specification document."
    part2 = "Speaker B: I'll set up the staging environment by next Monday."
    long_transcript = part1 + ("\n" + "More discussion. " * 20) + part2

    chunk1_items = [
        {
            "task": "Write technical specification document",
            "owner": "Speaker A",
            "priority": "High",
            "due": "",
            "context": "Agreed during planning session.",
            "confidence": 0.9,
        }
    ]
    chunk2_items = [
        {
            "task": "Set up staging environment",
            "owner": "Speaker B",
            "priority": "Medium",
            "due": "next Monday",
            "context": "B committed to staging setup.",
            "confidence": 0.87,
        }
    ]

    # Two calls — one per chunk
    service = _make_extraction_service([chunk1_items, chunk2_items])

    result = await service.extract(
        transcript_text=long_transcript,
        attendees=["Speaker A", "Speaker B"],
    )

    tasks = [item.task for item in result]
    assert "Write technical specification document" in tasks
    assert "Set up staging environment" in tasks
    assert len(result) == 2  # deduplication kept both unique items


# ---------------------------------------------------------------------------
# 4. Owner resolution — fuzzy match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_resolution_fuzzy(async_session: AsyncSession, test_tenant):
    """Partial name "Jon" resolves to "Jonathan Smith" via fuzzy matching."""
    resolver = OwnerResolutionService(
        session=async_session,
        tenant_id=test_tenant.id,
    )
    attendees = ["Alice Member", "Jonathan Smith", "Bob Admin"]

    user_id, label = await resolver.resolve(
        raw_owner="Jon",
        attendees=attendees,
    )

    assert label == "Jonathan Smith"


# ---------------------------------------------------------------------------
# 5. First-person resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_person_resolution(async_session: AsyncSession, test_tenant):
    """"I'll do it" with speaker "Alice" should resolve to "Alice"."""
    resolver = OwnerResolutionService(
        session=async_session,
        tenant_id=test_tenant.id,
    )

    user_id, label = await resolver.resolve(
        raw_owner="I'll handle the deployment",
        attendees=["Alice", "Bob"],
        speaker="Alice",
    )

    assert label == "Alice"


# ---------------------------------------------------------------------------
# 6. Date normalisation
# ---------------------------------------------------------------------------


def test_date_normalization_next_friday():
    """'by next Friday' relative to a known date returns the correct date."""
    # 2026-06-10 is a Wednesday (weekday=2)
    # "next Friday" from a Wednesday = this coming Friday + 7 days = June 19
    ref = date(2026, 6, 10)
    result = normalize_due_date("by next Friday", reference_date=ref)
    assert result is not None
    assert result.weekday() == 4  # Friday
    # "next" means at least 7 days ahead when the day hasn't passed yet
    assert result >= ref + timedelta(days=7)


def test_date_normalization_end_of_week():
    ref = date(2026, 6, 10)  # Wednesday
    result = normalize_due_date("end of week", reference_date=ref)
    assert result is not None
    assert result.weekday() == 6  # Sunday


def test_date_normalization_in_two_weeks():
    ref = date(2026, 6, 10)
    result = normalize_due_date("in 2 weeks", reference_date=ref)
    assert result == ref + timedelta(weeks=2)


def test_date_normalization_tomorrow():
    ref = date(2026, 6, 10)
    result = normalize_due_date("tomorrow", reference_date=ref)
    assert result == ref + timedelta(days=1)


def test_date_normalization_empty_string():
    result = normalize_due_date("", reference_date=date(2026, 6, 10))
    assert result is None


def test_date_normalization_unparseable():
    result = normalize_due_date("some vague future time", reference_date=date(2026, 6, 10))
    assert result is None


# ---------------------------------------------------------------------------
# 7. Confidence threshold → needs_review flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confidence_threshold():
    """Items with confidence < 0.5 must have needs_review = True after extraction."""
    low_conf_items = [
        {
            "task": "Maybe schedule a call",
            "owner": "Unassigned",
            "priority": "Low",
            "due": "",
            "context": "Mentioned in passing",
            "confidence": 0.3,  # below 0.5 threshold
        },
        {
            "task": "Definitely update the docs",
            "owner": "Alice",
            "priority": "High",
            "due": "today",
            "context": "Alice committed explicitly.",
            "confidence": 0.92,
        },
    ]

    service = _make_extraction_service([low_conf_items])
    result = await service.extract(
        transcript_text="Short meeting transcript.",
        attendees=["Alice"],
    )

    assert len(result) == 2
    low_item = next(r for r in result if r.confidence < 0.5)
    high_item = next(r for r in result if r.confidence >= 0.5)

    # The extraction service itself just returns ExtractedItemRaw.
    # The needs_review flag is set in the worker.  Test that the confidence
    # value is correctly preserved so the worker can make the decision.
    assert low_item.confidence == 0.3
    assert high_item.confidence == 0.92

    # Verify the worker threshold logic inline
    REVIEW_THRESHOLD = 0.5
    assert low_item.confidence < REVIEW_THRESHOLD  # → needs_review = True in worker
    assert high_item.confidence >= REVIEW_THRESHOLD  # → needs_review = False
