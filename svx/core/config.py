"""
Core configuration utilities for SuperVoxtral.

- Loads environment variables from a local .env if present.
- Exposes project path constants (ROOT_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, LOGS_DIR, PROMPT_DIR).
- Configures logging and ensures required directories exist.

This module is intentionally small and dependency-light so it can be imported early.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# Project paths (relative to current working directory)
ROOT_DIR: Final[Path] = Path.cwd()
RECORDINGS_DIR: Final[Path] = ROOT_DIR / "recordings"
TRANSCRIPTS_DIR: Final[Path] = ROOT_DIR / "transcripts"
LOGS_DIR: Final[Path] = ROOT_DIR / "logs"
PROMPT_DIR: Final[Path] = ROOT_DIR / "prompt"


def _get_log_level(level: str) -> int:
    """
    Convert a string log level to logging module constant, defaulting to INFO.
    """
    try:
        return getattr(logging, level.upper())
    except AttributeError:
        return logging.INFO


def _configure_logging(level: str) -> None:
    """
    Configure root logger with stream and file handlers.

    This function resets existing handlers to avoid duplicate logs if called multiple times.
    """
    log_level = _get_log_level(level)

    # Ensure logs directory exists before configuring FileHandler
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Reset handlers if any (idempotent setup)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    while root_logger.handlers:
        root_logger.handlers.pop()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)


def setup_environment(log_level: str = "INFO") -> None:
    """
    Load environment variables, ensure project directories exist, and configure logging.

    - Loads .env file if present (does not override already-set environment variables).
    - Creates recordings/, transcripts/, logs/, prompt/ directories as needed.
    - Configures logging according to `log_level`.
    """
    # Load env from .env (non-destructive to existing env)
    load_dotenv()

    # Ensure output directories exist
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)

    # Configure logging last (requires LOGS_DIR)
    _configure_logging(log_level)


__all__ = [
    "ROOT_DIR",
    "RECORDINGS_DIR",
    "TRANSCRIPTS_DIR",
    "LOGS_DIR",
    "PROMPT_DIR",
    "setup_environment",
]
