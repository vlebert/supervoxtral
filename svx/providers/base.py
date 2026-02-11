"""
Base provider interface for SuperVoxtral.

This module defines:
- TranscriptionResult: a simple TypedDict structure for provider responses
- Provider: a Protocol describing the required transcription and chat interface
- ProviderError: a generic exception for provider-related failures

All concrete providers should implement the `Provider` protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, runtime_checkable


class TranscriptionSegment(TypedDict):
    """
    A single transcription segment with timing and optional speaker info.

    Attributes:
        text: The transcribed text for this segment.
        start: Start time in seconds from the beginning of the audio.
        end: End time in seconds from the beginning of the audio.
        speaker_id: Speaker identifier (e.g. "speaker_0"), or None if not diarized.
        score: Confidence score for the segment, or None if not available.
    """

    text: str
    start: float
    end: float
    speaker_id: str | None
    score: float | None


class TranscriptionResult(TypedDict):
    """
    Normalized transcription result returned by providers.

    Attributes:
        text: The best-effort, human-readable transcript or model output.
        raw:  Provider-specific raw response payload (JSON-like dict).
        segments: List of transcription segments (present when diarize=True).
    """

    text: str
    raw: dict
    segments: NotRequired[list[TranscriptionSegment]]


class ProviderError(RuntimeError):
    """
    Generic provider exception to represent recoverable/handled failures.
    """


@runtime_checkable
class Provider(Protocol):
    """
    Provider interface for transcription and text transformation services.

    Implementations should be side-effect free aside from network I/O and must
    raise `ProviderError` (or a subclass) for expected provider failures
    (misconfiguration, auth, invalid arguments). Unexpected errors may propagate.

    Required attributes:
        name: A short, lowercase, unique identifier for the provider (e.g. "mistral").

    Required methods:
        transcribe: Perform audio transcription via a dedicated endpoint.
        chat: Transform text with a prompt via a text-based LLM.
    """

    # Short, unique name (e.g., "mistral", "whisper")
    name: str

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
        *,
        diarize: bool = False,
        timestamp_granularities: list[str] | None = None,
    ) -> TranscriptionResult:
        """
        Transcribe `audio_path` using a dedicated transcription endpoint.

        Args:
            audio_path: Path to an audio file (wav/mp3/opus...) to send to the provider.
            model: Optional provider-specific model identifier.
            language: Optional language hint/constraint (e.g., "en", "fr").
            diarize: Whether to enable speaker diarization.
            timestamp_granularities: Timestamp detail level (e.g. ["segment"]).

        Returns:
            TranscriptionResult including a human-readable `text` and
            provider `raw` payload. When diarize=True, also includes `segments`.

        Raises:
            ProviderError: For known/handled provider errors (e.g., missing API key).
            Exception: For unexpected failures (network issues, serialization, etc.).
        """
        ...

    def chat(
        self,
        text: str,
        prompt: str,
        model: str | None = None,
    ) -> TranscriptionResult:
        """
        Transform `text` using a text-based LLM with the given `prompt`.

        Args:
            text: Input text (e.g., raw transcription) to process.
            prompt: System prompt guiding the transformation.
            model: Optional provider-specific model identifier for the chat LLM.

        Returns:
            TranscriptionResult including the transformed `text` and
            provider `raw` payload.

        Raises:
            ProviderError: For known/handled provider errors (e.g., missing API key).
            Exception: For unexpected failures (network issues, serialization, etc.).
        """
        ...
