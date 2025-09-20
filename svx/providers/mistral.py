"""
Mistral provider implementation for SuperVoxtral.

This module provides a concrete Provider that uses Mistral's
"chat with audio" capability (Voxtral) to process audio and return text.

Requirements:
- User config must define [providers.mistral].api_key in config.toml.
- Package 'mistralai' installed and importable.

The provider composes messages with:
- User content including the audio (base64) and optional user prompt text.

It returns a normalized TranscriptionResult: {"text": str, "raw": dict}.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, cast

from svx.core.config import Config, ProviderConfig

from .base import Provider, ProviderError, TranscriptionResult

__all__ = ["MistralProvider"]


def _read_file_as_base64(path: Path) -> str:
    """
    Read a file and return its base64-encoded string.
    """
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("utf-8")


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
    Mistral Voxtral provider implementation.

    Uses the Mistral Python SDK to call `chat.with_audio` endpoint.
    """

    name = "mistral"

    def __init__(self, cfg: Config | None = None):
        if cfg is None:
            cfg = Config.load()
        mistral_cfg = cfg.providers.get("mistral", ProviderConfig())
        self.api_key = mistral_cfg.api_key
        if not self.api_key:
            raise ProviderError("Missing providers.mistral.api_key in user config (config.toml).")

    def transcribe(
        self,
        audio_path: Path,
        user_prompt: str | None,
        model: str | None = "voxtral-small-latest",
        language: str | None = None,
        transcribe_mode: bool = False,
    ) -> TranscriptionResult:
        """
        Transcribe/process audio using Mistral's chat-with-audio or transcription endpoint.

        Args:
            audio_path: Path to wav/mp3/opus file to send.
            user_prompt: Optional user prompt to include with the audio
                         (ignored in transcribe_mode).
            model: Voxtral model identifier (default: "voxtral-small-latest" for chat,
                   "voxtral-mini-latest" for transcribe).
            language: Optional language hint for transcription (used only in
                      transcribe_mode).
            transcribe_mode: If True, use dedicated transcription endpoint without prompt.

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

        if not Path(audio_path).exists():
            raise ProviderError(f"Audio file not found: {audio_path}")

        client = Mistral(api_key=self.api_key)

        if transcribe_mode:
            if user_prompt:
                logging.warning("Transcribe mode: user_prompt is ignored.")
            model_name = model or "voxtral-mini-latest"
            logging.info(
                "Calling Mistral transcription endpoint model=%s with audio=%s (%s), language=%s",
                model_name,
                Path(audio_path).name,
                Path(audio_path).suffix,
                language or "auto",
            )
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.complete(
                    model=model_name,
                    file={"content": f, "file_name": Path(audio_path).name},
                    language=language,
                )
            text = resp.text
            raw = _normalize_raw_response(resp)
        else:
            audio_b64 = _read_file_as_base64(Path(audio_path))

            # Compose messages (user only)
            messages: list[dict[str, Any]] = []
            user_content: list[dict[str, Any]] = [{"type": "input_audio", "input_audio": audio_b64}]
            if user_prompt:
                user_content.append({"type": "text", "text": user_prompt})
            messages.append({"role": "user", "content": user_content})

            # Execute request
            model_name = model or "voxtral-small-latest"
            logging.info(
                "Calling Mistral chat-with-audio model=%s with audio=%s (%s)",
                model_name,
                Path(audio_path).name,
                Path(audio_path).suffix,
            )
            resp = client.chat.complete(model=model_name, messages=cast(Any, messages))

            # Extract normalized text and raw payload
            text = _extract_text_from_response(resp)
            raw = _normalize_raw_response(resp)

        return TranscriptionResult(text=text, raw=raw)
