"""
Storage utilities for SuperVoxtral.

This module centralizes file persistence for transcription results:
- Save plain-text transcripts
- Save optional raw JSON responses (pretty-printed)
- Provide a single helper to save both consistently

Design goals:
- Safe path handling and directory creation
- UTF-8 everywhere
- Minimal, dependency-light, easy to unit test
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "save_text_file",
    "save_json_file",
    "save_transcript",
]


def _ensure_parent_dir(path: Path) -> None:
    """
    Ensure the parent directory of `path` exists.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def _sanitize_component(value: str) -> str:
    """
    Sanitize a filename component by replacing unsafe characters.

    - Keeps letters, digits, dot, underscore, and dash.
    - Replaces any other character sequences with underscores.
    - Strips leading/trailing whitespace.
    - Returns 'out' if the result is empty.
    """
    value = value.strip()
    # Replace disallowed characters with underscores
    sanatized = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return sanatized or "out"


def save_text_file(path: Path, content: str) -> Path:
    """
    Save `content` as UTF-8 text to `path`.

    Returns:
        The same `path` for convenience.
    """
    _ensure_parent_dir(path)
    path.write_text(content or "", encoding="utf-8")
    return path


def save_json_file(path: Path, data: Any, pretty: bool = True) -> Path:
    """
    Save `data` as JSON to `path`.

    Args:
        path: Destination file path.
        data: JSON-serializable object.
        pretty: Whether to pretty-print with indentation.

    Returns:
        The same `path` for convenience.
    """
    _ensure_parent_dir(path)
    if pretty:
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(serialized, encoding="utf-8")
    return path


def save_transcript(
    transcripts_dir: Path,
    base_name: str,
    provider: str,
    text: str,
    raw: dict | None = None,
) -> tuple[Path, Path | None]:
    """
    Save a transcript text and, optionally, the raw JSON response.

    Args:
        transcripts_dir: Base directory where transcripts are stored.
        base_name: Base file name (without extension).
        provider: Provider name used as suffix (e.g., 'mistral').
        text: Transcript text to write.
        raw: Optional raw response to serialize as JSON.

    Returns:
        (text_path, json_path_or_None)
    """
    transcripts_dir = Path(transcripts_dir)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    safe_base = _sanitize_component(base_name)
    safe_provider = _sanitize_component(provider)

    text_path = transcripts_dir / f"{safe_base}_{safe_provider}.txt"
    save_text_file(text_path, text or "")

    json_path: Path | None = None
    if raw is not None:
        json_path = transcripts_dir / f"{safe_base}_{safe_provider}.json"
        save_json_file(json_path, raw, pretty=True)

    return text_path, json_path
