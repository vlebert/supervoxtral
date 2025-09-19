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

from .config import USER_PROMPT_DIR

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
    user_cfg: dict[str, object] | None,
    inline: str | None,
    file: Path | None,
    user_prompt_dir: Path,
) -> str:
    """
    Resolve the effective user prompt from multiple sources, by priority:

    1) inline text (CLI --user-prompt)
    2) explicit file (CLI --user-prompt-file)
    3) user config inline text (user_cfg['prompt']['text'])
    4) user config file path (user_cfg['prompt']['file'])
    5) user prompt dir file (user_prompt_dir / 'user.md')
    6) literal fallback: "What's in this audio?"

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

    def _from_user_cfg() -> str:
        try:
            cfg_prompt = (user_cfg or {}).get("prompt") if isinstance(user_cfg, dict) else None
            if not isinstance(cfg_prompt, dict):
                return ""
            cfg_text = cfg_prompt.get("text")
            if isinstance(cfg_text, str) and cfg_text.strip():
                return cfg_text.strip()
            cfg_file = cfg_prompt.get("file")
            if isinstance(cfg_file, str) and cfg_file.strip():
                return read_text_file(Path(cfg_file).expanduser()).strip()
        except Exception:
            logging.debug("User config prompt processing failed.", exc_info=True)
        return ""

    def _from_user_prompt_dir() -> str:
        try:
            upath = Path(user_prompt_dir) / "user.md"
            if upath.exists():
                return read_text_file(upath).strip()
        except Exception:
            logging.debug("Could not read user prompt in user prompt dir: %s", user_prompt_dir)
        return ""

    suppliers = [
        lambda: _strip(inline),
        lambda: _read(file),
        _from_user_cfg,
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
        example_prompt = (
            "# SuperVoxtral user prompt\nPlease transcribe the audio and provide a short summary.\n"
        )
        try:
            path.write_text(example_prompt, encoding="utf-8")
        except Exception as e:
            logging.debug("Could not initialize user prompt file %s: %s", path, e)
    return path
