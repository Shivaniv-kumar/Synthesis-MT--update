"""Transcript file parser: VTT, SRT, and plain-text formats.

Public API
----------
parse_transcript_file(content: bytes, filename: str) -> tuple[str, list[Segment]]
    Dispatch to the appropriate format-specific parser based on the file extension.
    Returns ``(plain_text, segments)``.

Supported formats
-----------------
* **.vtt** – WebVTT (Web Video Text Tracks) with optional ``<v SpeakerName>`` cue tags.
* **.srt** – SubRip Text; no speaker labels in this format.
* **.txt** – Plain UTF-8 text; returned as-is with an empty segment list.
"""

from __future__ import annotations

import re
import structlog

from app.services.transcription import Segment

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def parse_transcript_file(
    content: bytes,
    filename: str,
) -> tuple[str, list[Segment]]:
    """Parse a transcript file and return ``(plain_text, segments)``.

    Parameters
    ----------
    content:
        Raw file bytes (the file will be decoded as UTF-8 with lenient error handling).
    filename:
        Original filename; the extension determines which parser is used.

    Returns
    -------
    tuple[str, list[Segment]]
        ``(plain_text, segments)`` where *plain_text* is a clean, speaker-attributed
        string suitable for extraction, and *segments* is an ordered list of
        :class:`~app.services.transcription.Segment` objects (empty for plain-text files).

    Raises
    ------
    ValueError
        If the file extension is not supported.
    """
    text = content.decode("utf-8", errors="replace")

    lower_name = filename.lower()
    if lower_name.endswith(".vtt"):
        plain_text, segments = parse_vtt(text)
    elif lower_name.endswith(".srt"):
        plain_text, segments = parse_srt(text)
    elif lower_name.endswith(".txt"):
        plain_text, segments = parse_txt(text)
    else:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "(none)"
        raise ValueError(
            f"Unsupported transcript file extension '.{ext}'. "
            "Supported formats: .vtt, .srt, .txt"
        )

    logger.info(
        "file_parser.parsed",
        filename=filename,
        chars=len(plain_text),
        segments=len(segments),
    )
    return plain_text, segments


# ---------------------------------------------------------------------------
# VTT parser
# ---------------------------------------------------------------------------

# Regex components
_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+"
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})"
)
# Shorter form without hours: MM:SS.mmm --> MM:SS.mmm
_VTT_SHORT_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2})[.,](\d{3})"
)
# <v SpeakerName>  or  <v.role SpeakerName>
_VTT_VOICE_TAG_RE = re.compile(r"<v(?:\.[^>]*)?\s+([^>]+)>")
# Strip all remaining HTML-like tags from cue text
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def parse_vtt(content: str) -> tuple[str, list[Segment]]:
    """Parse a WebVTT file into plain text and a list of :class:`Segment` objects.

    The parser handles:
    * Standard ``HH:MM:SS.mmm --> HH:MM:SS.mmm`` timing lines.
    * Short-form ``MM:SS.mmm --> MM:SS.mmm`` timing lines.
    * ``<v SpeakerName>`` cue voice tags for speaker attribution.
    * ``NOTE`` and ``REGION`` blocks (skipped).
    * Cue identifiers (numeric or named) before timing lines.

    Parameters
    ----------
    content:
        Raw VTT file content as a string.

    Returns
    -------
    tuple[str, list[Segment]]
    """
    segments: list[Segment] = []
    text_lines: list[str] = []

    # Split into cue blocks separated by blank lines
    blocks = re.split(r"\n{2,}", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        # Skip header and metadata blocks
        first_line = lines[0].strip()
        if first_line.startswith("WEBVTT") or first_line.startswith("NOTE") or first_line.startswith("REGION"):
            continue

        # Find timing line within the block (may be preceded by an optional cue ID)
        timing_line_idx: int | None = None
        start_time: float = 0.0
        end_time: float = 0.0

        for idx, line in enumerate(lines):
            # Try full HH:MM:SS.mmm form first
            m = _VTT_TIMESTAMP_RE.search(line)
            if m:
                g = m.groups()
                start_time = _hms_to_seconds(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                end_time = _hms_to_seconds(int(g[4]), int(g[5]), int(g[6]), int(g[7]))
                timing_line_idx = idx
                break
            # Try short MM:SS.mmm form
            m2 = _VTT_SHORT_TIMESTAMP_RE.search(line)
            if m2:
                g2 = m2.groups()
                start_time = _ms_to_seconds(int(g2[0]), int(g2[1]), int(g2[2]))
                end_time = _ms_to_seconds(int(g2[3]), int(g2[4]), int(g2[5]))
                timing_line_idx = idx
                break

        if timing_line_idx is None:
            # Not a cue block (cue id only block or unrecognised)
            continue

        # Everything after the timing line is cue payload
        cue_lines = lines[timing_line_idx + 1:]
        if not cue_lines:
            continue

        cue_text_raw = " ".join(cue_lines).strip()

        # Extract speaker from <v Name> tag (first occurrence in cue)
        speaker: str | None = None
        voice_match = _VTT_VOICE_TAG_RE.search(cue_text_raw)
        if voice_match:
            speaker = voice_match.group(1).strip()

        # Strip all HTML-like tags to get clean text
        clean_text = _HTML_TAG_RE.sub("", cue_text_raw).strip()
        if not clean_text:
            continue

        segment = Segment(
            start_time=start_time,
            end_time=end_time,
            speaker=speaker,
            text=clean_text,
        )
        segments.append(segment)

        # Build plain-text line
        if speaker:
            text_lines.append(f"{speaker}: {clean_text}")
        else:
            text_lines.append(clean_text)

    return "\n".join(text_lines), segments


# ---------------------------------------------------------------------------
# SRT parser
# ---------------------------------------------------------------------------

_SRT_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s+-->\s+"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_srt(content: str) -> tuple[str, list[Segment]]:
    """Parse a SubRip (.srt) file into plain text and :class:`Segment` objects.

    SRT files do not carry speaker metadata; all segments will have
    ``speaker=None``.

    Format (one cue block)::

        1
        00:00:05,000 --> 00:00:10,000
        First line of subtitle text.
        Optional second line.

    Parameters
    ----------
    content:
        Raw SRT file content as a string.

    Returns
    -------
    tuple[str, list[Segment]]
    """
    segments: list[Segment] = []
    text_lines: list[str] = []

    # Split into cue blocks separated by one or more blank lines
    blocks = re.split(r"\n{2,}", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        # Locate timing line
        timing_line_idx: int | None = None
        start_time: float = 0.0
        end_time: float = 0.0

        for idx, line in enumerate(lines):
            m = _SRT_TIMESTAMP_RE.search(line)
            if m:
                g = m.groups()
                start_time = _hms_to_seconds(int(g[0]), int(g[1]), int(g[2]), int(g[3]))
                end_time = _hms_to_seconds(int(g[4]), int(g[5]), int(g[6]), int(g[7]))
                timing_line_idx = idx
                break

        if timing_line_idx is None:
            continue

        cue_lines = lines[timing_line_idx + 1:]
        if not cue_lines:
            continue

        # Strip basic HTML styling tags (bold, italic, underline) that some SRT
        # files include, e.g. <b>, <i>, <u>, <font ...>
        clean_lines = [_HTML_TAG_RE.sub("", ln).strip() for ln in cue_lines]
        clean_text = " ".join(ln for ln in clean_lines if ln)
        if not clean_text:
            continue

        segment = Segment(
            start_time=start_time,
            end_time=end_time,
            speaker=None,
            text=clean_text,
        )
        segments.append(segment)
        text_lines.append(clean_text)

    return "\n".join(text_lines), segments


# ---------------------------------------------------------------------------
# Plain-text parser
# ---------------------------------------------------------------------------


def parse_txt(content: str) -> tuple[str, list[Segment]]:
    """Return the plain text as-is with an empty segment list.

    Parameters
    ----------
    content:
        Raw text file content as a string.

    Returns
    -------
    tuple[str, list[Segment]]
        ``(content, [])`` — no timing or speaker information is available.
    """
    return content, []


# ---------------------------------------------------------------------------
# Internal time-conversion helpers
# ---------------------------------------------------------------------------


def _hms_to_seconds(hours: int, minutes: int, seconds: int, millis: int) -> float:
    """Convert hours/minutes/seconds/milliseconds to a float number of seconds."""
    return hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0


def _ms_to_seconds(minutes: int, seconds: int, millis: int) -> float:
    """Convert minutes/seconds/milliseconds to a float number of seconds."""
    return minutes * 60.0 + seconds + millis / 1000.0
