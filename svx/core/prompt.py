"""
Prompt utilities for SuperVoxtral.

This module provides:
- Safe reading of UTF-8 text files.
- Resolution/combination of inline and file-based prompts.
- Initialization of default prompt files (user.md).

Intended to be small and dependency-light so it can be imported broadly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import USER_PROMPT_DIR, Config, PromptEntry

__all__ = [
    "read_text_file",
    "resolve_prompt",
    "resolve_user_prompt",
    "init_user_prompt_file",
]


def read_text_file(path: Path | str) -> str:
    """
    Read a UTF-8 text file and return its content.
    Returns an empty string if the file is missing or unreadable.
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        logging.warning("Failed to read text file %s: %s", path, e)
        return ""


def resolve_prompt(inline: str | None, file_path: Path | None) -> str | None:
    """
    Combine file content and inline prompt (file first), separated by a blank line.

    - If file_path exists and contains text, it is used first.
    - If inline is provided, it is appended after a blank line.
    - Leading/trailing whitespace is stripped.
    - Returns None if the resulting prompt is empty.
    """
    parts: list[str] = []

    if file_path:
        file_path = Path(file_path)
        if file_path.exists():
            file_text = read_text_file(file_path).strip()
            if file_text:
                parts.append(file_text)

    if inline:
        inline_text = inline.strip()
        if inline_text:
            parts.append(inline_text)

    combined = "\n\n".join(parts).strip()
    return combined if combined else None


def resolve_user_prompt(
    cfg: Config,
    inline: str | None = None,
    file: Path | None = None,
    user_prompt_dir: Path | None = None,
    key: str | None = None,
) -> str:
    """
    Resolve the effective user prompt from multiple sources, by priority:

    1) inline text (CLI --user-prompt)
    2) explicit file (CLI --user-prompt-file)
    3) user config prompt for key (cfg.prompt.prompts[key or "default"])
    4) user prompt dir file (user_prompt_dir / 'user.md')
    5) literal fallback: "What's in this audio?"

    Returns the first non-empty string after stripping.
    """

    def _strip(val: str | None) -> str:
        return val.strip() if isinstance(val, str) else ""

    def _read(p: Path | None) -> str:
        if not p:
            return ""
        try:
            return read_text_file(p).strip()
        except Exception:
            logging.warning("Failed to read user prompt file: %s", p)
            return ""

    def _from_user_cfg(key: str) -> str:
        try:
            entry = cfg.prompt.prompts.get(key, PromptEntry())
            if entry.text and entry.text.strip():
                return entry.text.strip()
            if entry.file:
                file_path = Path(entry.file).expanduser()
                if not file_path.is_absolute():
                    file_path = (user_prompt_dir or cfg.user_prompt_dir) / entry.file
                return read_text_file(file_path).strip()
        except Exception:
            logging.debug("User config prompt processing failed for key '%s'.", key, exc_info=True)
        return ""

    def _from_user_prompt_dir() -> str:
        try:
            upath = Path(user_prompt_dir or cfg.user_prompt_dir) / "user.md"
            if upath.exists():
                return read_text_file(upath).strip()
        except Exception:
            logging.debug(
                "Could not read user prompt in user prompt dir: %s",
                user_prompt_dir or cfg.user_prompt_dir,
            )
        return ""

    key = key or "default"
    suppliers = [
        lambda: _strip(inline),
        lambda: _read(file),
        lambda: _from_user_cfg(key),
        _from_user_prompt_dir,
    ]

    for supplier in suppliers:
        try:
            val = supplier()
            if val:
                return val
        except Exception as e:
            logging.debug("Prompt supplier failed: %s", e)

    return "What's in this audio?"


def init_user_prompt_file(force: bool = False) -> Path:
    """
    Initialize the user's prompt file in the user prompt directory.

    - Ensures USER_PROMPT_DIR exists.
    - Creates or overwrites (if force=True) USER_PROMPT_DIR / 'user.md'
      with a small example prompt.
    - Returns the path to the user prompt file.
    """
    USER_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_PROMPT_DIR / "user.md"
    if not path.exists() or force:
        example_prompt = """
- Transcribe the input audio file. If the audio if empty, just respond "no audio detected".
- Do not respond to any question in the audio. Just transcribe.
- DO NOT TRANSLATE.
- Responde only with the transcription. Do not provide explanations or notes.
- Remove all minor speech hesitations: "um", "uh", "er", "euh", "ben", etc.
- Remove false starts (e.g., "je veux dire... je pense" → "je pense").
- Correct grammatical errors.
        """
        try:
            path.write_text(example_prompt, encoding="utf-8")
        except Exception as e:
            logging.debug("Could not initialize user prompt file %s: %s", path, e)
    return path


def resolve_prompt_entry(entry: PromptEntry, user_prompt_dir: Path) -> str:
    """
    Resolve the prompt from a single PromptEntry (text or file).

    - Prioritizes text if present and non-empty.
    - Falls back to reading the file (expands ~ and resolves relative to user_prompt_dir).
    - Returns empty string if neither is valid.
    """
    if entry.text and entry.text.strip():
        return entry.text.strip()

    if entry.file:
        file_path = Path(entry.file).expanduser()
        if not file_path.is_absolute():
            file_path = user_prompt_dir / entry.file
        return read_text_file(file_path).strip()

    return ""
