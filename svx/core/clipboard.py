"""
Clipboard helper for SuperVoxtral.

Provides a small, dependency-light utility to copy text to the system clipboard.

Strategy:
- Primary: use `pyperclip` if available (cross-platform).
- Fallback (macOS): use `pbcopy` via subprocess.
- If neither is available the function will raise a RuntimeError.

This module intentionally keeps a very small surface area (single helper function)
so it can be imported and used from the CLI with minimal coupling.

Usage:
    from svx.core.clipboard import copy_to_clipboard

    try:
        copy_to_clipboard(text)
    except RuntimeError:
        logging.warning("Failed to copy to clipboard")
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Final

__all__ = ["copy_to_clipboard", "ClipboardError"]


class ClipboardError(RuntimeError):
    """Raised when copying to the clipboard fails in an expected way."""


_PBCOPY_CMD: Final[str] = "pbcopy"


def _try_pyperclip(text: str) -> None:
    """
    Attempt to copy `text` using the pyperclip library.

    Raises:
        ClipboardError: if pyperclip is installed but copying fails.
        ImportError: if pyperclip is not installed.
    """
    try:
        import pyperclip
    except Exception as e:
        # Propagate ImportError-like behavior to allow fallback
        raise ImportError("pyperclip not available") from e

    try:
        pyperclip.copy(text)
    except Exception as e:
        raise ClipboardError("pyperclip.copy failed") from e


def _try_pbcopy(text: str) -> None:
    """
    Attempt to copy `text` using the macOS `pbcopy` command.

    Raises:
        ClipboardError: if pbcopy is not available or the subprocess fails.
    """
    # Use shlex to be defensive, but `pbcopy` reads from stdin so we don't pass args.
    cmd = shlex.split(_PBCOPY_CMD)
    try:
        # On macOS, pbcopy reads from stdin.
        subprocess.run(cmd, input=text, text=True, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise ClipboardError("pbcopy not found on PATH") from e
    except subprocess.CalledProcessError as e:
        logging.debug("pbcopy stderr: %s", e.stderr)
        raise ClipboardError("pbcopy failed") from e
    except Exception as e:
        raise ClipboardError("Unexpected error when running pbcopy") from e


def copy_to_clipboard(text: str) -> None:
    """
    Copy the given text to the system clipboard.

    Attempts pyperclip first (recommended). If pyperclip is not installed,
    falls back to `pbcopy` (macOS). If all methods fail, raises ClipboardError.

    Args:
        text: Text to copy. Non-str inputs will be coerced via str().

    Raises:
        ClipboardError: if copying fails or no supported method is available.
    """
    if text is None:
        text = ""

    if not isinstance(text, str):
        text = str(text)

    # 1) Try pyperclip (preferred)
    try:
        _try_pyperclip(text)
        logging.debug("Copied text to clipboard via pyperclip")
        return
    except ImportError:
        logging.debug("pyperclip not available, trying pbcopy fallback")
    except ClipboardError as e:
        # pyperclip import succeeded but copy failed; try fallback before giving up.
        logging.warning("pyperclip.copy failed: %s. Trying fallback.", e)

    # 2) Fallback: pbcopy (macOS)
    try:
        _try_pbcopy(text)
        logging.debug("Copied text to clipboard via pbcopy")
        return
    except ClipboardError as e:
        logging.debug("pbcopy fallback failed: %s", e)

    # No method succeeded
    raise ClipboardError(
        "Failed to copy text to clipboard: no supported method succeeded (pyperclip / pbcopy)."
    )
