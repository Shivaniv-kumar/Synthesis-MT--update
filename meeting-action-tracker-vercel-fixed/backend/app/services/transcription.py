"""Pluggable transcription provider system.

Three providers are supported out of the box:

* **WhisperProvider** – OpenAI Whisper API via the official ``openai`` SDK.
* **DeepgramProvider** – Deepgram nova-2 with diarization via httpx.
* **AssemblyAIProvider** – AssemblyAI with speaker labels via httpx (polling).

Select the active provider with the ``TRANSCRIPTION_PROVIDER`` environment
variable: ``"whisper"`` (default), ``"deepgram"``, or ``"assemblyai"``.

The ``OPENAI_API_KEY`` environment variable is used **only** by
``WhisperProvider`` for audio transcription; it is never used for action-item
extraction (which is handled by Anthropic Claude via ``ExtractionService``).
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """A single timed segment of a transcript, optionally attributed to a speaker."""

    start_time: float
    end_time: float
    speaker: Optional[str]
    text: str


@dataclass
class TranscriptionResult:
    """The full result returned by any transcription provider."""

    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str = "en"
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class TranscriptionProvider(ABC):
    """Common interface for all transcription back-ends."""

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str = "en",
    ) -> TranscriptionResult:
        """Transcribe *audio_bytes* and return a :class:`TranscriptionResult`.

        Parameters
        ----------
        audio_bytes:
            Raw audio content (any format accepted by the underlying API).
        filename:
            Original filename; used for MIME-type hints sent to the API.
        language:
            BCP-47 language hint (e.g. ``"en"``, ``"es"``).
        """


# ---------------------------------------------------------------------------
# WhisperProvider
# ---------------------------------------------------------------------------


class WhisperProvider(TranscriptionProvider):
    """OpenAI Whisper API transcription provider.

    Uses ``openai.audio.transcriptions.create`` with ``response_format="verbose_json"``
    to obtain word/segment-level timestamps and language detection.

    The ``OPENAI_API_KEY`` environment variable must be set.
    """

    def __init__(self) -> None:
        import openai  # local import so the optional dependency is not required globally

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning("transcription.whisper.no_api_key")

        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str = "en",
    ) -> TranscriptionResult:
        """Call the Whisper-1 API and map the verbose JSON response."""
        import openai

        logger.info("transcription.whisper.start", filename=filename, language=language)

        try:
            response = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=(filename, audio_bytes),
                response_format="verbose_json",
                language=language if language != "auto" else None,
                timestamp_granularities=["segment"],
            )
        except openai.OpenAIError as exc:
            logger.error("transcription.whisper.api_error", error=str(exc))
            raise

        # ``response`` is a ``Transcription`` object with ``.text``, ``.segments``,
        # ``.language``, and ``.duration`` when verbose_json is requested.
        segments: list[Segment] = []
        raw_segments = getattr(response, "segments", None) or []
        for seg in raw_segments:
            segments.append(
                Segment(
                    start_time=float(getattr(seg, "start", 0.0)),
                    end_time=float(getattr(seg, "end", 0.0)),
                    speaker=None,  # Whisper does not diarize
                    text=(getattr(seg, "text", "") or "").strip(),
                )
            )

        result = TranscriptionResult(
            text=(response.text or "").strip(),
            segments=segments,
            language=getattr(response, "language", language) or language,
            duration_seconds=float(getattr(response, "duration", 0.0) or 0.0),
        )

        logger.info(
            "transcription.whisper.complete",
            filename=filename,
            segments=len(segments),
            duration=result.duration_seconds,
        )
        return result


# ---------------------------------------------------------------------------
# DeepgramProvider
# ---------------------------------------------------------------------------


class DeepgramProvider(TranscriptionProvider):
    """Deepgram nova-2 transcription provider with speaker diarization.

    Sends audio directly to the Deepgram streaming/batch listen endpoint and
    maps the diarized utterances to :class:`Segment` objects.

    The ``DEEPGRAM_API_KEY`` environment variable must be set.
    """

    _ENDPOINT = "https://api.deepgram.com/v1/listen"
    _PARAMS = {
        "model": "nova-2",
        "smart_format": "true",
        "diarize": "true",
        "punctuate": "true",
        "utterances": "true",
    }

    def __init__(self) -> None:
        self._api_key = os.environ.get("DEEPGRAM_API_KEY", "")
        if not self._api_key:
            logger.warning("transcription.deepgram.no_api_key")

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str = "en",
    ) -> TranscriptionResult:
        """POST audio to Deepgram and parse the diarized utterances."""
        logger.info("transcription.deepgram.start", filename=filename, language=language)

        params = dict(self._PARAMS)
        if language and language != "auto":
            params["language"] = language

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": _mime_for_filename(filename),
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                response = await client.post(
                    self._ENDPOINT,
                    params=params,
                    headers=headers,
                    content=audio_bytes,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "transcription.deepgram.http_error",
                    status=exc.response.status_code,
                    body=exc.response.text[:500],
                )
                raise
            except httpx.RequestError as exc:
                logger.error("transcription.deepgram.request_error", error=str(exc))
                raise

        data = response.json()
        channels = data.get("results", {}).get("channels", [])
        alternatives = channels[0].get("alternatives", []) if channels else []
        full_text = alternatives[0].get("transcript", "") if alternatives else ""

        # Use utterances when diarize=true is requested — they include speaker IDs
        utterances = data.get("results", {}).get("utterances", [])
        segments: list[Segment] = []
        for utt in utterances:
            speaker_raw = utt.get("speaker")
            speaker = f"Speaker {speaker_raw}" if speaker_raw is not None else None
            segments.append(
                Segment(
                    start_time=float(utt.get("start", 0.0)),
                    end_time=float(utt.get("end", 0.0)),
                    speaker=speaker,
                    text=(utt.get("transcript", "") or "").strip(),
                )
            )

        # Fall back to channel-level text if utterances are empty
        if not full_text and segments:
            full_text = " ".join(s.text for s in segments)

        # Duration from metadata
        duration = float(
            data.get("metadata", {}).get("duration", 0.0) or 0.0
        )
        detected_language = (
            data.get("results", {}).get("channels", [{}])[0]
            .get("detected_language", language)
        ) if channels else language

        result = TranscriptionResult(
            text=full_text,
            segments=segments,
            language=detected_language,
            duration_seconds=duration,
        )

        logger.info(
            "transcription.deepgram.complete",
            filename=filename,
            utterances=len(segments),
            duration=duration,
        )
        return result


# ---------------------------------------------------------------------------
# AssemblyAIProvider
# ---------------------------------------------------------------------------


class AssemblyAIProvider(TranscriptionProvider):
    """AssemblyAI transcription provider with speaker diarization.

    Three-step workflow:
    1. Upload the audio file to AssemblyAI's upload endpoint.
    2. Submit a transcription request with ``speaker_labels: true``.
    3. Poll the transcript endpoint until ``status == "completed"`` or ``"error"``.

    The ``ASSEMBLYAI_API_KEY`` environment variable must be set.
    """

    _UPLOAD_URL = "https://api.assemblyai.com/v2/upload"
    _TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"

    # Polling
    _POLL_INTERVAL_SECONDS = 5.0
    _MAX_POLL_ATTEMPTS = 120  # 10 minutes

    def __init__(self) -> None:
        self._api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
        if not self._api_key:
            logger.warning("transcription.assemblyai.no_api_key")
        self._headers = {
            "authorization": self._api_key,
            "content-type": "application/json",
        }

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str = "en",
    ) -> TranscriptionResult:
        """Upload, submit, and poll AssemblyAI for a transcription."""
        logger.info("transcription.assemblyai.start", filename=filename, language=language)

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Step 1: Upload audio
            upload_url = await self._upload_audio(client, audio_bytes, filename)
            logger.debug("transcription.assemblyai.uploaded", upload_url=upload_url)

            # Step 2: Submit transcript request
            transcript_id = await self._submit_transcript(client, upload_url, language)
            logger.debug(
                "transcription.assemblyai.submitted", transcript_id=transcript_id
            )

        # Step 3: Poll for completion (uses its own client with longer timeout)
        transcript_data = await self._poll_until_complete(transcript_id)

        # Map to our result type
        result = self._map_response(transcript_data, language)

        logger.info(
            "transcription.assemblyai.complete",
            transcript_id=transcript_id,
            segments=len(result.segments),
            duration=result.duration_seconds,
        )
        return result

    async def _upload_audio(
        self, client: httpx.AsyncClient, audio_bytes: bytes, filename: str
    ) -> str:
        """POST audio bytes to the AssemblyAI upload endpoint, return the upload URL."""
        upload_headers = {
            "authorization": self._api_key,
            "content-type": _mime_for_filename(filename),
        }
        try:
            response = await client.post(
                self._UPLOAD_URL,
                headers=upload_headers,
                content=audio_bytes,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "transcription.assemblyai.upload_error",
                status=exc.response.status_code,
                body=exc.response.text[:500],
            )
            raise

        return response.json()["upload_url"]

    async def _submit_transcript(
        self, client: httpx.AsyncClient, audio_url: str, language: str
    ) -> str:
        """Submit the transcription job and return the transcript ID."""
        payload: dict = {
            "audio_url": audio_url,
            "speaker_labels": True,
        }
        if language and language not in ("auto", ""):
            payload["language_code"] = language

        try:
            response = await client.post(
                self._TRANSCRIPT_URL,
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "transcription.assemblyai.submit_error",
                status=exc.response.status_code,
                body=exc.response.text[:500],
            )
            raise

        return response.json()["id"]

    async def _poll_until_complete(self, transcript_id: str) -> dict:
        """Poll GET /v2/transcript/{id} until status is 'completed' or 'error'."""
        poll_url = f"{self._TRANSCRIPT_URL}/{transcript_id}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(self._MAX_POLL_ATTEMPTS):
                try:
                    response = await client.get(poll_url, headers=self._headers)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        "transcription.assemblyai.poll_error",
                        transcript_id=transcript_id,
                        status=exc.response.status_code,
                        attempt=attempt,
                    )
                    raise

                data = response.json()
                status = data.get("status")

                if status == "completed":
                    return data

                if status == "error":
                    error_msg = data.get("error", "Unknown AssemblyAI error")
                    logger.error(
                        "transcription.assemblyai.transcript_error",
                        transcript_id=transcript_id,
                        error=error_msg,
                    )
                    raise RuntimeError(
                        f"AssemblyAI transcription failed: {error_msg}"
                    )

                logger.debug(
                    "transcription.assemblyai.polling",
                    transcript_id=transcript_id,
                    status=status,
                    attempt=attempt,
                )
                await asyncio.sleep(self._POLL_INTERVAL_SECONDS)

        raise TimeoutError(
            f"AssemblyAI transcription {transcript_id} did not complete within "
            f"{self._MAX_POLL_ATTEMPTS * self._POLL_INTERVAL_SECONDS:.0f}s"
        )

    def _map_response(self, data: dict, fallback_language: str) -> TranscriptionResult:
        """Map the AssemblyAI JSON response to a :class:`TranscriptionResult`."""
        full_text = data.get("text", "") or ""
        utterances = data.get("utterances") or []
        segments: list[Segment] = []

        for utt in utterances:
            speaker_raw = utt.get("speaker")
            speaker = f"Speaker {speaker_raw}" if speaker_raw is not None else None
            # AssemblyAI uses milliseconds for start/end
            start_ms = float(utt.get("start", 0))
            end_ms = float(utt.get("end", 0))
            segments.append(
                Segment(
                    start_time=start_ms / 1000.0,
                    end_time=end_ms / 1000.0,
                    speaker=speaker,
                    text=(utt.get("text", "") or "").strip(),
                )
            )

        # Duration in ms from audio_duration field
        audio_duration_ms = float(data.get("audio_duration", 0) or 0)
        duration_seconds = audio_duration_ms / 1000.0 if audio_duration_ms > 1 else audio_duration_ms

        # AssemblyAI uses audio_duration in seconds when using the REST API v2
        # Guard: if the value looks like seconds already (< 10000) keep it as-is
        if audio_duration_ms < 10_000:
            duration_seconds = audio_duration_ms

        detected_language = data.get("language_code", fallback_language) or fallback_language

        return TranscriptionResult(
            text=full_text,
            segments=segments,
            language=detected_language,
            duration_seconds=duration_seconds,
        )


# ---------------------------------------------------------------------------
# Formatting utility
# ---------------------------------------------------------------------------


def format_transcript_with_speakers(result: TranscriptionResult) -> str:
    """Format a :class:`TranscriptionResult` as a readable, speaker-attributed string.

    Each segment is rendered as::

        Speaker A (0:00): Some text here.
        Speaker B (0:32): More text here.

    Segments without a speaker label are attributed to ``"Speaker"`` generically.
    If the result has no segments the plain ``result.text`` is returned.
    """
    if not result.segments:
        return result.text

    lines: list[str] = []
    for seg in result.segments:
        speaker_label = seg.speaker or "Speaker"
        timestamp = _format_timestamp(seg.start_time)
        lines.append(f"{speaker_label} ({timestamp}): {seg.text}")

    return "\n".join(lines)


def _format_timestamp(seconds: float) -> str:
    """Convert a float number of seconds to ``M:SS`` or ``H:MM:SS`` format."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def get_transcription_provider() -> TranscriptionProvider:
    """Return the configured :class:`TranscriptionProvider`.

    Reads the ``TRANSCRIPTION_PROVIDER`` environment variable:

    * ``"whisper"`` (default) → :class:`WhisperProvider`
    * ``"deepgram"`` → :class:`DeepgramProvider`
    * ``"assemblyai"`` → :class:`AssemblyAIProvider`

    Raises
    ------
    ValueError
        If an unrecognised provider name is configured.
    """
    provider_name = os.environ.get("TRANSCRIPTION_PROVIDER", "whisper").lower().strip()

    if provider_name == "whisper":
        return WhisperProvider()
    if provider_name == "deepgram":
        return DeepgramProvider()
    if provider_name == "assemblyai":
        return AssemblyAIProvider()

    raise ValueError(
        f"Unknown TRANSCRIPTION_PROVIDER '{provider_name}'. "
        "Valid options: 'whisper', 'deepgram', 'assemblyai'."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mime_for_filename(filename: str) -> str:
    """Return a best-effort MIME type for the given filename."""
    import mimetypes

    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type:
        return mime_type

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    _FALLBACKS = {
        "mp3": "audio/mpeg",
        "mp4": "video/mp4",
        "m4a": "audio/mp4",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "aac": "audio/aac",
    }
    return _FALLBACKS.get(ext, "application/octet-stream")
