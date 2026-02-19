"""
Mistral provider implementation for SuperVoxtral.

This module provides a concrete Provider that uses Mistral's dedicated
transcription endpoint (Voxtral) and text-based LLM chat for transformation.

Requirements:
- User config must define [providers.mistral].api_key in config.toml.
- Package 'mistralai' installed and importable.

It returns a normalized TranscriptionResult: {"text": str, "raw": dict}.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from svx.core.config import Config, ProviderConfig

from .base import Provider, ProviderError, TranscriptionResult, TranscriptionSegment

__all__ = ["MistralProvider"]


def _extract_text_from_response(resp: Any) -> str:
    """
    Attempt to robustly extract the textual content from a Mistral response.

    Handles both dict-like and attribute-like SDK response formats.
    Falls back to str(resp) if extraction fails.
    """
    try:
        # Get first choice
        choice0 = resp["choices"][0] if isinstance(resp, dict) else resp.choices[0]  # type: ignore[index]
        # Get message
        message = choice0["message"] if isinstance(choice0, dict) else choice0.message
        # Get content (could be str or list of segments)
        content = message["content"] if isinstance(message, dict) else message.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
            return "\n".join(p for p in parts if p)
        return str(content)
    except Exception:
        return str(resp)


def _normalize_raw_response(resp: Any) -> dict[str, Any]:
    """
    Convert the response into a plain dict for persistence.

    - If response is already a dict, use it as-is.
    - If it has 'model_dump_json()', parse it.
    - Else, try json.loads(str(resp)).
    - Else, store as {"raw": str(resp)}.
    """
    if isinstance(resp, dict):
        return resp
    try:
        # pydantic-like
        if hasattr(resp, "model_dump_json"):
            return json.loads(resp.model_dump_json())  # type: ignore[call-arg]
    except Exception:
        pass
    try:
        return json.loads(str(resp))
    except Exception:
        return {"raw": str(resp)}


class MistralProvider(Provider):
    """
    Mistral provider implementation.

    Uses the dedicated transcription endpoint for audio-to-text
    and the chat endpoint for text transformation via LLM.
    """

    name = "mistral"

    def __init__(self, cfg: Config | None = None):
        if cfg is None:
            cfg = Config.load()
        mistral_cfg = cfg.providers.get("mistral", ProviderConfig())
        self.api_key = mistral_cfg.api_key
        if not self.api_key:
            raise ProviderError("Missing providers.mistral.api_key in user config (config.toml).")
        self.context_bias = cfg.defaults.context_bias

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = "voxtral-mini-latest",
        language: str | None = None,
        *,
        diarize: bool = False,
        timestamp_granularities: list[str] | None = None,
    ) -> TranscriptionResult:
        """
        Transcribe audio using Mistral's dedicated transcription endpoint.

        Args:
            audio_path: Path to wav/mp3/opus file to send.
            model: Voxtral model identifier (default: "voxtral-mini-latest").
            language: Optional language hint for transcription.
            diarize: Whether to enable speaker diarization.
            timestamp_granularities: Timestamp detail level (e.g. ["segment"]).

        Returns:
            TranscriptionResult: {"text": text, "raw": raw_dict, "segments": [...]}

        Raises:
            ProviderError: for expected configuration/import errors.
        """
        try:
            from mistralai import Mistral
        except Exception as e:
            raise ProviderError(
                "Failed to import 'mistralai'. Ensure the 'mistralai' package is installed."
            ) from e

        if not Path(audio_path).exists():
            raise ProviderError(f"Audio file not found: {audio_path}")

        client = Mistral(api_key=self.api_key)

        model_name = model or "voxtral-mini-latest"
        granularities = timestamp_granularities or (["segment"] if diarize else None)
        logging.info(
            "Calling Mistral transcription endpoint model=%s with audio=%s (%s),"
            " language=%s, diarize=%s, context_bias=%d items",
            model_name,
            Path(audio_path).name,
            Path(audio_path).suffix,
            language or "auto",
            diarize,
            len(self.context_bias),
        )
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.complete(
                model=model_name,
                file={"content": f, "file_name": Path(audio_path).name},
                language=language,
                context_bias=self.context_bias if self.context_bias else None,
                diarize=diarize,
                timestamp_granularities=cast(Any, granularities),
            )
        text = resp.text
        raw = _normalize_raw_response(resp)

        result = TranscriptionResult(text=text, raw=raw)

        # Parse segments when diarization is enabled and response contains them
        if diarize and hasattr(resp, "segments") and resp.segments:
            segments: list[TranscriptionSegment] = []
            for seg in resp.segments:
                segments.append(
                    TranscriptionSegment(
                        text=getattr(seg, "text", ""),
                        start=float(getattr(seg, "start", 0.0)),
                        end=float(getattr(seg, "end", 0.0)),
                        speaker_id=getattr(seg, "speaker_id", None),
                        score=getattr(seg, "score", None),
                    )
                )
            result["segments"] = segments
            logging.info("Parsed %d diarized segments", len(segments))

        return result

    def chat(
        self,
        text: str,
        prompt: str,
        model: str | None = None,
    ) -> TranscriptionResult:
        """
        Transform text using Mistral's chat endpoint with a system prompt.

        Args:
            text: Input text (e.g., raw transcription) to process.
            prompt: System prompt guiding the transformation.
            model: Model identifier (default: None, caller should provide).

        Returns:
            TranscriptionResult: {"text": text, "raw": raw_dict}

        Raises:
            ProviderError: for expected configuration/import errors.
        """
        try:
            from mistralai import Mistral
        except Exception as e:
            raise ProviderError(
                "Failed to import 'mistralai'. Ensure the 'mistralai' package is installed."
            ) from e

        client = Mistral(api_key=self.api_key)

        model_name = model or "mistral-small-latest"
        logging.info(
            "Calling Mistral chat endpoint model=%s for text transformation",
            model_name,
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]

        resp = client.chat.complete(model=model_name, messages=cast(Any, messages))

        result_text = _extract_text_from_response(resp)
        raw = _normalize_raw_response(resp)

        return TranscriptionResult(text=result_text, raw=raw)
