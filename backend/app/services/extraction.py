"""Extraction service — calls the Anthropic Claude API to pull action items
from a meeting transcript.

Design notes
------------
- Uses the async Anthropic SDK (AsyncAnthropic).
- Model is selected per-transcript based on heuristics (length, ambiguity signals)
  — see _select_model().
- Token counting is called before every send so the service can decide whether
  to chunk and to record accurate billing metadata.
- Chunking splits on speaker-turn boundaries (or double-newlines) and produces
  deterministic 60 k-token windows with a 2 k-token overlap.
- The extraction prompt is loaded once from disk at class construction time and
  cached; the {{TRANSCRIPT_TEXT}} placeholder is substituted per call.
- The JSON response is parsed and validated with Pydantic. On the first parse
  error the service retries once with an explicit "return only JSON" instruction.
- Items with confidence < 0.5 are flagged by ExtractionMeta.needs_review_count.
- Logs: model, token counts, latency, item count. No transcript content is logged.
"""

from __future__ import annotations

import json
import re
import string
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import difflib
import structlog
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError, field_validator

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROMPT_VERSION = "v1"
_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "extraction_v1.txt"

# Token thresholds
_CHUNK_TARGET_TOKENS = 60_000
_CHUNK_OVERLAP_TOKENS = 2_000
_MAX_TOKENS_BEFORE_CHUNKING = 80_000

# Rough character-to-token ratio used only as a fallback when count_tokens fails
_CHARS_PER_TOKEN_APPROX = 3.5

# Short transcript ceiling: use Haiku below this word count
_SHORT_TRANSCRIPT_WORDS = 500

# Signals that suggest the transcript is ambiguous and needs Opus
_AMBIGUITY_SIGNALS = (
    "unclear",
    "not sure",
    "maybe",
    "possibly",
    "we might",
    "could be",
    "i think",
    "perhaps",
    "tentatively",
    " tbd",
    "to be determined",
)

# Minimum ambiguity signal count to escalate to Opus
_AMBIGUITY_THRESHOLD = 4

# SequenceMatcher ratio above which two items are treated as duplicates
_DEDUP_RATIO = 0.85


# ---------------------------------------------------------------------------
# Shared Pydantic schema — Claude API output shape
# ---------------------------------------------------------------------------


class ExtractedItemRaw(BaseModel):
    """Validated shape of a single action item returned by Claude."""

    task: str
    owner: str
    priority: str
    due: str
    context: str
    confidence: float

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: str) -> str:
        normalized = v.strip().capitalize()
        allowed = {"High", "Medium", "Low"}
        if normalized in allowed:
            return normalized
        # Lenient mapping for common variants
        upper = v.strip().upper()
        mapping = {"HIGH": "High", "MEDIUM": "Medium", "MED": "Medium", "LOW": "Low"}
        if upper in mapping:
            return mapping[upper]
        raise ValueError(f"priority must be one of {allowed}, got {v!r}")

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


# ---------------------------------------------------------------------------
# ExtractionMeta dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExtractionMeta:
    """Telemetry and metadata returned alongside the extracted items."""

    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    item_count: int
    chunked: bool = False
    chunk_count: int = 1
    needs_review_count: int = 0
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ExtractionService
# ---------------------------------------------------------------------------


class ExtractionService:
    """Stateless service that extracts action items from meeting transcripts.

    Parameters
    ----------
    settings:
        Application settings.  Defaults to the global singleton from
        ``app.config.get_settings()``.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        self._prompt_template: str = _PROMPT_PATH.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        transcript_text: str,
        attendees: Optional[list[str]] = None,
        meeting_date: Optional[date] = None,
    ) -> tuple[list[ExtractedItemRaw], ExtractionMeta]:
        """Extract action items from *transcript_text*.

        Parameters
        ----------
        transcript_text:
            Raw meeting transcript.  Treated as untrusted data — never logged.
        attendees:
            Optional list of attendee names for owner-resolution hints.
        meeting_date:
            Optional date of the meeting for relative-date anchoring.

        Returns
        -------
        items:
            List of validated ``ExtractedItemRaw`` objects.
        meta:
            Telemetry dataclass for logging / billing.
        """
        t0 = time.monotonic()

        prompt = self._build_prompt(transcript_text, attendees, meeting_date)
        model = self._select_model(transcript_text)

        # Count tokens to decide whether chunking is needed
        input_tokens = await self._count_tokens(model, prompt)

        if input_tokens > _MAX_TOKENS_BEFORE_CHUNKING:
            return await self._extract_chunked(
                transcript_text, attendees, meeting_date, model, t0
            )

        # Single-pass extraction
        raw_content, usage = await self._send_message(model, prompt)
        items = self._parse_response(raw_content)

        # Retry once on parse failure with explicit instruction
        if items is None:
            retry_prompt = (
                prompt
                + "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
                "Return ONLY a valid JSON array — no other text, no code fences, "
                "no markdown.  Start with [ and end with ]."
            )
            raw_content, usage = await self._send_message(model, retry_prompt)
            items = self._parse_response(raw_content)

        if items is None:
            logger.error("extraction.parse_failed_after_retry", model=model)
            items = []

        latency_ms = (time.monotonic() - t0) * 1000
        needs_review_count = sum(1 for i in items if i.confidence < 0.5)

        meta = ExtractionMeta(
            model=model,
            prompt_version=PROMPT_VERSION,
            input_tokens=usage.get("input_tokens", input_tokens),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=round(latency_ms, 1),
            item_count=len(items),
            chunked=False,
            chunk_count=1,
            needs_review_count=needs_review_count,
        )

        logger.info(
            "extraction.complete",
            model=meta.model,
            prompt_version=meta.prompt_version,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            latency_ms=meta.latency_ms,
            item_count=meta.item_count,
            needs_review_count=meta.needs_review_count,
            chunked=meta.chunked,
        )

        return items, meta

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def _select_model(self, transcript_text: str) -> str:
        """Return the appropriate Claude model ID for this transcript.

        - Short, clean transcripts  → Haiku (fast, cheap)
        - Long or ambiguous texts   → Opus (highest quality)
        - Default                   → Sonnet (balanced)
        """
        word_count = len(transcript_text.split())

        if word_count < _SHORT_TRANSCRIPT_WORDS:
            return self._settings.haiku_model

        text_lower = transcript_text.lower()
        ambiguity_score = sum(1 for sig in _AMBIGUITY_SIGNALS if sig in text_lower)
        rough_token_estimate = len(transcript_text) / _CHARS_PER_TOKEN_APPROX

        if ambiguity_score >= _AMBIGUITY_THRESHOLD or rough_token_estimate > 60_000:
            return self._settings.opus_model

        return self._settings.extraction_model

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        transcript_text: str,
        attendees: Optional[list[str]],
        meeting_date: Optional[date],
    ) -> str:
        """Substitute template variables in the prompt.

        The prompt template uses Jinja2-style ``{% if %}`` conditional blocks and
        ``{{ var }}`` placeholders.  We handle substitution manually to avoid
        adding a Jinja2 dependency, and to keep prompt format transparent.
        """
        prompt = self._prompt_template

        # Attendees block
        if attendees:
            attendees_list = "\n".join(f"  - {name}" for name in attendees)
            prompt = _replace_conditional(prompt, "attendees", attendees_list)
        else:
            prompt = _strip_conditional(prompt, "attendees")

        # Meeting date block
        if meeting_date:
            # %-d is Linux strftime; on Windows use #d — use manual formatting
            day = meeting_date.day
            date_str = meeting_date.strftime(f"%A, %B {day}, %Y")
            prompt = _replace_conditional(prompt, "meeting_date", date_str)
        else:
            prompt = _strip_conditional(prompt, "meeting_date")

        # Transcript substitution — comes last so injected text cannot escape
        # the data section by crafting text that closes the template variable.
        prompt = prompt.replace("{{TRANSCRIPT_TEXT}}", transcript_text)

        return prompt

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    async def _count_tokens(self, model: str, prompt: str) -> int:
        """Call the Anthropic token-counting endpoint and return the input token count."""
        try:
            response = await self._client.messages.count_tokens(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.input_tokens
        except Exception as exc:
            # Fall back to rough character-based estimate so we still chunk if needed
            logger.warning(
                "extraction.count_tokens_failed",
                model=model,
                error=str(exc),
            )
            return int(len(prompt) / _CHARS_PER_TOKEN_APPROX)

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    async def _send_message(
        self,
        model: str,
        prompt: str,
    ) -> tuple[str, dict]:
        """Send a single message to Claude and return (content_text, usage_dict)."""
        response = await self._client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text if response.content else ""
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return content, usage

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> Optional[list[ExtractedItemRaw]]:
        """Strip code fences, parse JSON, validate with Pydantic.

        Returns ``None`` on any parse or validation error so the caller can
        decide whether to retry.
        """
        text = raw.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "extraction.json_decode_error",
                error=str(exc),
                # Log only the first 120 characters of the raw response for debug context;
                # never log full content (may contain PII / confidential text).
                raw_prefix=raw[:120],
            )
            return None

        if not isinstance(data, list):
            logger.warning(
                "extraction.response_not_a_list",
                type=type(data).__name__,
            )
            return None

        items: list[ExtractedItemRaw] = []
        for idx, raw_item in enumerate(data):
            try:
                items.append(ExtractedItemRaw.model_validate(raw_item))
            except (ValidationError, Exception) as exc:
                logger.warning(
                    "extraction.item_validation_error",
                    item_index=idx,
                    error=str(exc),
                )
                # Skip invalid items rather than failing the whole batch

        return items

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    async def _extract_chunked(
        self,
        transcript_text: str,
        attendees: Optional[list[str]],
        meeting_date: Optional[date],
        model: str,
        t0: float,
    ) -> tuple[list[ExtractedItemRaw], ExtractionMeta]:
        """Split the transcript into overlapping chunks and merge the results."""
        chunks = _chunk_transcript(transcript_text)
        logger.info(
            "extraction.chunking_start",
            model=model,
            chunk_count=len(chunks),
        )

        all_items: list[ExtractedItemRaw] = []
        total_input_tokens = 0
        total_output_tokens = 0

        for chunk_idx, chunk_text in enumerate(chunks):
            prompt = self._build_prompt(chunk_text, attendees, meeting_date)
            input_tokens = await self._count_tokens(model, prompt)
            raw_content, usage = await self._send_message(model, prompt)
            chunk_items = self._parse_response(raw_content)

            if chunk_items is None:
                # Single retry per chunk
                retry_prompt = (
                    prompt
                    + "\n\nIMPORTANT: Return ONLY a valid JSON array — no other text."
                )
                raw_content, usage = await self._send_message(model, retry_prompt)
                chunk_items = self._parse_response(raw_content)

            if chunk_items is None:
                logger.error(
                    "extraction.chunk_parse_failed",
                    chunk_index=chunk_idx,
                    model=model,
                )
                chunk_items = []

            all_items.extend(chunk_items)
            total_input_tokens += usage.get("input_tokens", input_tokens)
            total_output_tokens += usage.get("output_tokens", 0)

        # De-duplicate items that appear in overlapping windows
        all_items = _deduplicate_raw(all_items)

        latency_ms = (time.monotonic() - t0) * 1000
        needs_review_count = sum(1 for i in all_items if i.confidence < 0.5)

        meta = ExtractionMeta(
            model=model,
            prompt_version=PROMPT_VERSION,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            latency_ms=round(latency_ms, 1),
            item_count=len(all_items),
            chunked=True,
            chunk_count=len(chunks),
            needs_review_count=needs_review_count,
        )

        logger.info(
            "extraction.complete",
            model=meta.model,
            prompt_version=meta.prompt_version,
            input_tokens=meta.input_tokens,
            output_tokens=meta.output_tokens,
            latency_ms=meta.latency_ms,
            item_count=meta.item_count,
            needs_review_count=meta.needs_review_count,
            chunked=meta.chunked,
            chunk_count=meta.chunk_count,
        )

        return all_items, meta


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions, no I/O)
# ---------------------------------------------------------------------------


def _chunk_transcript(transcript_text: str) -> list[str]:
    """Split *transcript_text* into overlapping chunks of ~60 k tokens.

    Strategy (deterministic):
    1. Prefer to split on speaker-turn boundaries (lines matching ``NAME:``
       or ``[HH:MM] NAME:``).
    2. Fall back to double-newline paragraph breaks.
    3. Accumulate segments until target_chars is reached, then flush.

    Overlap is achieved by prepending the last ``overlap_chars`` characters of
    the previous chunk to the start of the next chunk.

    Returns a non-empty list of chunk strings.
    """
    overlap_chars = int(_CHUNK_OVERLAP_TOKENS * _CHARS_PER_TOKEN_APPROX)
    target_chars = int(_CHUNK_TARGET_TOKENS * _CHARS_PER_TOKEN_APPROX)

    segments = _split_into_segments(transcript_text)

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0
    overlap_tail = ""

    for segment in segments:
        seg_len = len(segment)

        if current_len + seg_len > target_chars and current_parts:
            # Flush current accumulation
            chunk_text = overlap_tail + "".join(current_parts)
            chunks.append(chunk_text)
            # Build next overlap tail from the raw parts (not including prev overlap)
            raw_body = "".join(current_parts)
            overlap_tail = (
                raw_body[-overlap_chars:] if len(raw_body) > overlap_chars else raw_body
            )
            current_parts = []
            current_len = 0

        current_parts.append(segment)
        current_len += seg_len

    # Flush final chunk
    if current_parts:
        chunk_text = overlap_tail + "".join(current_parts)
        chunks.append(chunk_text)

    return chunks if chunks else [transcript_text]


def _split_into_segments(text: str) -> list[str]:
    """Split *text* into logical segments for chunking.

    Priority:
    1. Speaker-turn lines (``SPEAKER:`` or ``[timestamp] SPEAKER:``)
    2. Double-newline paragraph breaks
    3. Entire text as one segment (fallback)
    """
    # Matches: optional "[HH:MM(:SS)] " prefix, then "Name:" (initial cap or all-caps)
    speaker_re = re.compile(
        r"(?m)^(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?[A-Z][A-Za-z .'-]{0,40}:",
    )

    lines = text.splitlines(keepends=True)
    segments: list[str] = []
    current: list[str] = []

    for line in lines:
        if speaker_re.match(line) and current:
            segments.append("".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        segments.append("".join(current))

    # If we found only 1 segment (no speaker turns), split on double newlines
    if len(segments) <= 1:
        parts = text.split("\n\n")
        segments = [p + "\n\n" for p in parts if p.strip()]

    return segments if segments else [text]


def _normalize_task(task: str) -> str:
    """Lowercase and strip punctuation for deduplication comparison."""
    return task.lower().translate(str.maketrans("", "", string.punctuation)).strip()


def _deduplicate_raw(items: list[ExtractedItemRaw]) -> list[ExtractedItemRaw]:
    """Remove near-duplicate items produced by overlapping chunks.

    Two items are considered duplicates if their normalized task strings have a
    SequenceMatcher ratio > _DEDUP_RATIO (0.85).  When duplicates exist, keep
    the item with the higher confidence score.
    """
    seen: list[ExtractedItemRaw] = []
    for item in items:
        norm = _normalize_task(item.task)
        duplicate_idx: Optional[int] = None
        for idx, existing in enumerate(seen):
            ratio = difflib.SequenceMatcher(
                None, norm, _normalize_task(existing.task)
            ).ratio()
            if ratio > _DEDUP_RATIO:
                duplicate_idx = idx
                break
        if duplicate_idx is None:
            seen.append(item)
        elif item.confidence > seen[duplicate_idx].confidence:
            # Replace the existing copy with the higher-confidence version
            seen[duplicate_idx] = item
    return seen


# ---------------------------------------------------------------------------
# Prompt template helpers
# ---------------------------------------------------------------------------


def _replace_conditional(template: str, block_name: str, value: str) -> str:
    """Replace a ``{% if <name> %} ... {% endif %}`` block with *value*.

    Removes the if/endif markers and substitutes the inner placeholder
    ``{{ <name>_list }}`` or ``{{ <name> }}`` with *value*.
    """
    start_tag = f"{{% if {block_name} %}}"
    end_tag = "{% endif %}"

    start_idx = template.find(start_tag)
    end_idx = template.find(end_tag, start_idx)
    if start_idx == -1 or end_idx == -1:
        return template

    inner = template[start_idx + len(start_tag): end_idx]
    # Replace whichever placeholder exists in the block
    inner = inner.replace(f"{{{{ {block_name}_list }}}}", value)
    inner = inner.replace(f"{{{{ {block_name} }}}}", value)

    return (
        template[:start_idx]
        + inner.strip("\n")
        + "\n"
        + template[end_idx + len(end_tag):]
    )


def _strip_conditional(template: str, block_name: str) -> str:
    """Remove a ``{% if <name> %} ... {% endif %}`` block entirely."""
    start_tag = f"{{% if {block_name} %}}"
    end_tag = "{% endif %}"

    start_idx = template.find(start_tag)
    end_idx = template.find(end_tag, start_idx)
    if start_idx == -1 or end_idx == -1:
        return template

    return template[:start_idx] + template[end_idx + len(end_tag):]
