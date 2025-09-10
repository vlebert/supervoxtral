"""
Prompt utilities for SuperVoxtral.

This module provides:
- Safe reading of UTF-8 text files.
- Resolution/combination of inline and file-based prompts.
- Initialization of default prompt files (user.txt).

Intended to be small and dependency-light so it can be imported broadly.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = [
    "read_text_file",
    "resolve_prompt",
    "init_default_prompt_files",
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


def init_default_prompt_files(prompt_dir: Path) -> None:
    """
    Ensure default prompt files exist in `prompt_dir`:
    - user.txt

    If they don't exist, create them as empty files.
    """
    prompt_dir = Path(prompt_dir)
    prompt_dir.mkdir(parents=True, exist_ok=True)

    for _prompt_file in (prompt_dir / "user.txt",):
        try:
            if not _prompt_file.exists():
                _prompt_file.write_text("", encoding="utf-8")
        except Exception as e:
            logging.debug("Could not initialize prompt file %s: %s", _prompt_file, e)
