"""
Core configuration utilities for SuperVoxtral.


- Resolves a per-user configuration directory (cross-platform).

- Exposes project path constants (ROOT_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, LOGS_DIR)
  as well as user-scoped paths (USER_CONFIG_DIR, USER_PROMPT_DIR).
- Configures logging and ensures required directories exist.

Design:
- User config is optional and lives in a platform-standard location:
  - Linux: ${XDG_CONFIG_HOME:-~/.config}/supervoxtral
  - macOS: ~/Library/Application Support/SuperVoxtral
  - Windows: %APPDATA%/SuperVoxtral

- User config file: config.toml (TOML). For Python 3.11+, `tomllib` is used;
  for 3.10, a fallback to `tomli` would be expected
  (the project should add `tomli` to dependencies for 3.10).

This module aims to remain small and import-safe.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

# Use stdlib tomllib (Python >= 3.11 required by project)
import tomllib

# Project paths (relative to current working directory)
ROOT_DIR: Final[Path] = Path.cwd()
RECORDINGS_DIR: Final[Path] = ROOT_DIR / "recordings"
TRANSCRIPTS_DIR: Final[Path] = ROOT_DIR / "transcripts"
LOGS_DIR: Final[Path] = ROOT_DIR / "logs"


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
    Ensure project directories exist and configure logging.


    - Creates recordings/, transcripts/, logs/ directories as needed.
    - Ensures user prompt dir exists (but does not overwrite user files).
    - Configures logging according to `log_level`.
    """

    # Ensure user config/prompt dirs exist (created but files not overwritten)
    USER_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Initial stream logging (file logging added conditionally later)
    log_level_int = _get_log_level(log_level)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_int)
    while root_logger.handlers:
        root_logger.handlers.pop()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level_int)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


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


def init_user_config(force: bool = False, prompt_file: Path | None = None) -> Path:
    """
    Initialize the user's config.toml with example content.

    - Ensures USER_CONFIG_DIR exists.
    - Writes USER_CONFIG_FILE with example content if missing or force=True.
    - The example references the provided prompt_file (or USER_PROMPT_DIR/'user.md' by default).
    """
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if prompt_file is None:
        prompt_file = USER_PROMPT_DIR / "user.md"

    example_toml = (
        "# SuperVoxtral - User configuration\n"
        "#\n"
        "# Basics:\n"
        "# - This configuration controls the default behavior of `svx record`.\n"
        "# - The parameters below override the binary's built-in defaults.\n"
        "# - You can override a few options at runtime via the CLI:\n"
        "#   --prompt / --prompt-file (set a one-off prompt for this run)\n"
        "#   --log-level (debugging)\n"
        "#   --outfile-prefix (one-off output naming)\n"
        "#\n"
        "# Output persistence:\n"
        "# - Set keep_* = true to create and save files to project\n"
        "#   directories (recordings/, transcripts/, logs/).\n"
        "# - false (default): use temp files/console only (no disk\n"
        "#   footprint in project dir).\n"
        "#\n"
        "# Authentication:\n"
        "# - API keys are defined in provider-specific sections in this file.\n"
        "[providers.mistral]\n"
        '# api_key = ""\n\n'
        "[defaults]\n"
        '# Provider to use (currently supported: "mistral")\n'
        'provider = "mistral"\n\n'
        '# File format sent to the provider: "wav" | "mp3" | "opus"\n'
        '# Recording is always WAV; conversion is applied if "mp3" or "opus"\n'
        'format = "opus"\n\n'
        "# Model to use on the provider side (example for Mistral Voxtral)\n"
        'model = "voxtral-mini-latest"\n\n'
        "# Language hint (may help the provider)\n"
        'language = "fr"\n\n'
        "# Audio recording parameters\n"
        "rate = 16000\n"
        "channels = 1\n"
        '#device = ""\n\n'
        "# Output persistence:\n"
        "# - keep_audio_files: false uses temp files (no recordings/ dir),\n"
        "#   true saves to recordings/\n"
        "keep_audio_files = false\n"
        "# - keep_transcript_files: false prints/copies only (no\n"
        "#   transcripts/ dir), true saves to transcripts/\n"
        "keep_transcript_files = false\n"
        "# - keep_log_files: false console only (no logs/ dir), true\n"
        "#   saves to logs/app.log\n"
        "keep_log_files = false\n\n"
        "# Automatically copy the transcribed text to the system clipboard\n"
        "copy = true\n\n"
        '# Log level: "DEBUG" | "INFO" | "WARNING" | "ERROR"\n'
        'log_level = "INFO"\n\n'
        "[prompt.default]\n"
        "# Default user prompt source:\n"
        "# - Option 1: Use a file (recommended)\n"
        f'file = "{str(prompt_file)}"\n'
        "#\n"
        "# - Option 2: Inline prompt (less recommended for long text)\n"
        '# text = "Please transcribe the audio and provide a concise summary in French."\n'
        "#\n"
        "# For multiple prompts in future, add [prompt.other] sections.\n"
    )

    if not USER_CONFIG_FILE.exists() or force:
        try:
            USER_CONFIG_FILE.write_text(example_toml, encoding="utf-8")
        except Exception:
            logging.debug("Could not write user config file: %s", USER_CONFIG_FILE)
    return USER_CONFIG_FILE


@dataclass
class ProviderConfig:
    api_key: str | None = None


@dataclass
class DefaultsConfig:
    provider: str = "mistral"
    format: str = "opus"
    model: str = "voxtral-mini-latest"
    language: str | None = None
    rate: int = 16000
    channels: int = 1
    device: str | None = None
    keep_audio_files: bool = False
    keep_transcript_files: bool = False
    keep_log_files: bool = False
    copy: bool = True
    log_level: str = "INFO"
    outfile_prefix: str | None = None


@dataclass
class PromptEntry:
    text: str | None = None
    file: str | None = None


@dataclass
class PromptConfig:
    prompts: dict[str, PromptEntry] = field(default_factory=lambda: {"default": PromptEntry()})


@dataclass
class Config:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    recordings_dir: Path = RECORDINGS_DIR
    transcripts_dir: Path = TRANSCRIPTS_DIR
    logs_dir: Path = LOGS_DIR
    user_prompt_dir: Path = USER_PROMPT_DIR
    user_config_file: Path = USER_CONFIG_FILE

    @classmethod
    def load(cls, log_level: str = "INFO") -> Config:
        setup_environment(log_level)
        user_config = load_user_config()
        user_defaults_raw = user_config.get("defaults", {})
        # Coerce defaults
        defaults_data = {
            "provider": str(user_defaults_raw.get("provider", "mistral")),
            "format": str(user_defaults_raw.get("format", "opus")),
            "model": str(user_defaults_raw.get("model", "voxtral-mini-latest")),
            "language": user_defaults_raw.get("language"),
            "rate": int(user_defaults_raw.get("rate", 16000)),
            "channels": int(user_defaults_raw.get("channels", 1)),
            "device": user_defaults_raw.get("device"),
            "keep_audio_files": bool(user_defaults_raw.get("keep_audio_files", False)),
            "keep_transcript_files": bool(user_defaults_raw.get("keep_transcript_files", False)),
            "keep_log_files": bool(user_defaults_raw.get("keep_log_files", False)),
            "copy": bool(user_defaults_raw.get("copy", True)),
            "log_level": str(user_defaults_raw.get("log_level", log_level)),
            "outfile_prefix": user_defaults_raw.get("outfile_prefix"),
        }
        channels = defaults_data["channels"]
        if channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        rate = defaults_data["rate"]
        if rate <= 0:
            raise ValueError("rate must be > 0")
        format_ = defaults_data["format"]
        if format_ not in {"wav", "mp3", "opus"}:
            raise ValueError("format must be one of wav|mp3|opus")
        defaults = DefaultsConfig(**defaults_data)
        # Conditional output directories
        if defaults.keep_audio_files:
            RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        if defaults.keep_transcript_files:
            TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        if defaults.keep_log_files:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Update logging level to effective (user or CLI fallback)
        root_logger = logging.getLogger()
        root_logger.setLevel(_get_log_level(defaults.log_level))
        # Add file handler if enabled
        if defaults.keep_log_files:
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
            file_handler = logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8")
            file_level = _get_log_level(defaults.log_level)
            file_handler.setLevel(file_level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        # Providers
        providers_raw = user_config.get("providers", {})
        providers_data = {}
        for name, prov_raw in providers_raw.items():
            if isinstance(prov_raw, dict):
                api_key = str(prov_raw.get("api_key", ""))
                providers_data[name] = ProviderConfig(api_key=api_key)
        # Prompt
        prompt_raw = user_config.get("prompt", {})
        prompts_data: dict[str, PromptEntry] = {}
        if isinstance(prompt_raw, dict):
            if any(k in prompt_raw for k in ["text", "file"]):  # old flat style
                logging.warning(
                    "Old [prompt] format detected in %s; "
                    "please migrate to [prompt.default] manually.",
                    USER_CONFIG_FILE,
                )
                entry = PromptEntry(
                    text=prompt_raw.get("text")
                    if isinstance(prompt_raw.get("text"), str)
                    else None,
                    file=prompt_raw.get("file")
                    if isinstance(prompt_raw.get("file"), str)
                    else None,
                )
                prompts_data["default"] = entry
            else:  # new nested style
                for key, entry_raw in prompt_raw.items():
                    if isinstance(entry_raw, dict):
                        entry = PromptEntry(
                            text=entry_raw.get("text")
                            if isinstance(entry_raw.get("text"), str)
                            else None,
                            file=entry_raw.get("file")
                            if isinstance(entry_raw.get("file"), str)
                            else None,
                        )
                        prompts_data[key] = entry
        # Ensure "default" always exists
        if "default" not in prompts_data:
            prompts_data["default"] = PromptEntry()
        prompt = PromptConfig(prompts=prompts_data)
        data = {
            "defaults": defaults,
            "providers": providers_data,
            "prompt": prompt,
            "recordings_dir": RECORDINGS_DIR,
            "transcripts_dir": TRANSCRIPTS_DIR,
            "logs_dir": LOGS_DIR,
            "user_prompt_dir": USER_PROMPT_DIR,
            "user_config_file": USER_CONFIG_FILE,
        }
        return cls(**data)

    def resolve_prompt(self, inline: str | None = None, file_path: Path | None = None) -> str:
        from svx.core.prompt import resolve_user_prompt

        return resolve_user_prompt(self, inline, file_path, self.user_prompt_dir, key="default")

    def get_provider_config(self, name: str) -> dict[str, Any]:
        return asdict(self.providers.get(name, ProviderConfig()))


__all__ = [
    "ROOT_DIR",
    "RECORDINGS_DIR",
    "TRANSCRIPTS_DIR",
    "LOGS_DIR",
    "USER_CONFIG_DIR",
    "USER_PROMPT_DIR",
    "USER_CONFIG_FILE",
    "setup_environment",
    "load_user_config",
    "init_user_config",
    "Config",
    "ProviderConfig",
    "DefaultsConfig",
    "PromptConfig",
]
