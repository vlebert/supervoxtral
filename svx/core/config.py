"""
Core configuration utilities for SuperVoxtral.

- Loads environment variables from a local .env if present.
- Resolves a per-user configuration directory (cross-platform).
- Can load a user config TOML file and inject env vars from it (without overwriting existing env).
- Exposes project path constants (ROOT_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, LOGS_DIR, PROMPT_DIR)
  as well as user-scoped paths (USER_CONFIG_DIR, USER_PROMPT_DIR).
- Configures logging and ensures required directories exist.

Design:
- User config is optional and lives in a platform-standard location:
  - Linux: ${XDG_CONFIG_HOME:-~/.config}/supervoxtral
  - macOS: ~/Library/Application Support/SuperVoxtral
  - Windows: %APPDATA%/SuperVoxtral

- User config file: config.toml (TOML). For Python 3.11+ `tomllib` is used; for 3.10 a fallback to `tomli` would be expected
  (the project should add `tomli` to dependencies for 3.10).

This module aims to remain small and import-safe.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Final, Mapping

# Use stdlib tomllib (Python >= 3.11 required by project)
import tomllib
from dotenv import load_dotenv

# Project paths (relative to current working directory)
ROOT_DIR: Final[Path] = Path.cwd()
RECORDINGS_DIR: Final[Path] = ROOT_DIR / "recordings"
TRANSCRIPTS_DIR: Final[Path] = ROOT_DIR / "transcripts"
LOGS_DIR: Final[Path] = ROOT_DIR / "logs"
PROMPT_DIR: Final[Path] = ROOT_DIR / "prompt"


# User config (platform standard)
def get_user_config_dir() -> Path:
    """
    Resolve the user configuration directory for SuperVoxtral in a cross-platform way.

    Returns a Path that may not yet exist.
    """
    # Windows: %APPDATA%
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "SuperVoxtral"
        # Fallback to home
        return Path.home() / "AppData" / "Roaming" / "SuperVoxtral"

    # macOS: ~/Library/Application Support/SuperVoxtral
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SuperVoxtral"

    # Linux/Unix: XDG_CONFIG_HOME or ~/.config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "supervoxtral"
    return Path.home() / ".config" / "supervoxtral"


USER_CONFIG_DIR: Final[Path] = get_user_config_dir()
USER_PROMPT_DIR: Final[Path] = USER_CONFIG_DIR / "prompt"
USER_CONFIG_FILE: Final[Path] = USER_CONFIG_DIR / "config.toml"


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
    - Ensures user prompt dir exists (but does not overwrite user files).
    - Configures logging according to `log_level`.
    """
    # Load env from .env (non-destructive to existing env)
    load_dotenv()

    # Ensure output directories exist
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure user config/prompt dirs exist (created but files not overwritten)
    USER_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Configure logging last (requires LOGS_DIR)
    _configure_logging(log_level)


def _read_toml(path: Path) -> dict[str, Any]:
    """
    Read a TOML file and return its contents as a dict using stdlib tomllib.
    If reading/parsing fails, return an empty dict.
    """
    try:
        text = path.read_text(encoding="utf-8")
        return tomllib.loads(text)
    except Exception:
        return {}


def load_user_config() -> dict[str, Any]:
    """
    Load and return a dictionary representing the user's configuration (from USER_CONFIG_FILE).

    If the file does not exist or cannot be parsed, returns an empty dict.

    Expected layout (example):
    [env]
    MISTRAL_API_KEY = "xxx"

    [defaults]
    provider = "mistral"
    format = "mp3"
    model = "voxtral-small-latest"
    language = "fr"
    rate = 16000
    channels = 1
    device = ""
    keep_audio_files = false
    copy = true
    log_level = "INFO"

    [prompt]
    # optional: either file or text
    file = "~/path/to/user.md"
    text = "inline prompt text (less recommended)"
    """
    if not USER_CONFIG_FILE.exists():
        return {}
    return _read_toml(USER_CONFIG_FILE)


def apply_user_env(user_config: Mapping[str, Any]) -> None:
    """
    Apply environment variables from user_config['env'] into os.environ only when not already set.

    Does not overwrite existing environment variables.
    """
    env = user_config.get("env")
    if not isinstance(env, Mapping):
        return
    for k, v in env.items():
        if not isinstance(k, str):
            continue
        if k in os.environ:
            continue
        if v is None:
            continue
        os.environ[k] = str(v)


__all__ = [
    "ROOT_DIR",
    "RECORDINGS_DIR",
    "TRANSCRIPTS_DIR",
    "LOGS_DIR",
    "PROMPT_DIR",
    "USER_CONFIG_DIR",
    "USER_PROMPT_DIR",
    "USER_CONFIG_FILE",
    "setup_environment",
    "load_user_config",
    "apply_user_env",
]
