"""
Base provider interface for SuperVoxtral.

This module defines:
- TranscriptionResult: a simple TypedDict structure for provider responses
- Provider: a Protocol describing the required transcription interface
- ProviderError: a generic exception for provider-related failures

All concrete providers should implement the `Provider` protocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable


class TranscriptionResult(TypedDict):
    """
    Normalized transcription result returned by providers.

    Attributes:
        text: The best-effort, human-readable transcript or model output.
        raw:  Provider-specific raw response payload (JSON-like dict).
    """

    text: str
    raw: dict


class ProviderError(RuntimeError):
    """
    Generic provider exception to represent recoverable/handled failures.
    """


@runtime_checkable
class Provider(Protocol):
    """
    Provider interface for transcription/chat-with-audio services.

    Implementations should be side-effect free aside from network I/O and must
    raise `ProviderError` (or a subclass) for expected provider failures
    (misconfiguration, auth, invalid arguments). Unexpected errors may propagate.

    Required attributes:
        name: A short, lowercase, unique identifier for the provider (e.g. "mistral").

    Required methods:
        transcribe: Perform the transcription given an audio file and optional user prompt.
    """

    # Short, unique name (e.g., "mistral", "whisper")
    name: str

    def transcribe(
        self,
        audio_path: Path,
        user_prompt: str | None,
        model: str | None = None,
        language: str | None = None,
        transcribe_mode: bool = False,
    ) -> TranscriptionResult:
        """
        Transcribe or process `audio_path` and return a normalized result.

        Args:
            audio_path: Path to an audio file (wav/mp3/opus...) to send to the provider.
            user_prompt: Optional user prompt to guide the transcription or analysis.
            model: Optional provider-specific model identifier.
            language: Optional language hint/constraint (e.g., "en", "fr").
            transcribe_mode: Optional bool to enable specialized modes like pure
                             transcription (default False).

        Returns:
            TranscriptionResult including a human-readable `text` and
            provider `raw` payload.

        Raises:
            ProviderError: For known/handled provider errors (e.g., missing API key).
            Exception: For unexpected failures (network issues, serialization, etc.).
        """
        ...
