"""
SuperVoxtral package.

CLI/TUI tool to record audio and send it to transcription/chat providers
(e.g., Mistral Voxtral "chat with audio").

Expose package version via __version__.
"""

from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError, version
except Exception:  # pragma: no cover - very old Python fallback
    # Fallback for environments that might not have importlib.metadata
    # (not expected with Python 3.10+)
    PackageNotFoundError = Exception  # type: ignore

    def version(distribution_name: str) -> str:  # type: ignore
        return "0.0.0"


try:
    __version__ = version("supervoxtral")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
