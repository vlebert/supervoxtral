"""
User configuration management for SuperVoxtral.

This module provides:
- Detection and creation of user config directory using platformdirs
- Loading and saving of TOML configuration files
- Management of user prompt files (Markdown)
- Default configuration values
- Configuration precedence handling

The user configuration is stored in the platform-specific config directory:
- Linux: ~/.config/supervoxtral/
- macOS: ~/Library/Application Support/supervoxtral/
- Windows: %APPDATA%/supervoxtral/
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, TypedDict

import platformdirs
import toml

__all__ = [
    "get_user_config_dir",
    "get_user_config_path",
    "get_user_prompt_path",
    "load_user_config",
    "save_user_config",
    "init_user_config",
    "get_default_config",
]


class ConfigDict(TypedDict):
    """Type definition for the configuration dictionary."""

    default: dict[str, Any]
    api_keys: dict[str, str]
    prompt: dict[str, str]


def get_user_config_dir() -> Path:
    """
    Get the user configuration directory using platformdirs.

    Returns:
        Path: The platform-specific user configuration directory.
    """
    return Path(platformdirs.user_config_dir("supervoxtral"))


def get_user_config_path() -> Path:
    """
    Get the path to the user configuration file.

    Returns:
        Path: Path to config.toml in the user config directory.
    """
    return get_user_config_dir() / "config.toml"


def get_user_prompt_path(config_file: str | None = None) -> Path:
    """
    Get the path to the user prompt file.

    Args:
        config_file: Optional prompt file path from config. If None, uses default.

    Returns:
        Path: Path to the user prompt file.
    """
    config_dir = get_user_config_dir()

    if config_file:
        # If it's an absolute path, use it as-is
        if Path(config_file).is_absolute():
            return Path(config_file)
        # Otherwise, treat as relative to config directory
        return config_dir / config_file

    # Default to userprompt.md in config directory
    return config_dir / "userprompt.md"


def get_default_config() -> ConfigDict:
    """
    Get the default configuration values.

    Returns:
        ConfigDict: Default configuration dictionary.
    """
    return ConfigDict(
        default={
            "provider": "mistral",
            "model": "voxtral-small-latest",
            "audio_format": "wav",
            "language": None,
            "rate": 16000,
            "channels": 1,
            "keep_audio_files": True,
            "copy": False,
        },
        api_keys={
            "mistral": "",
            "openai": "",
        },
        prompt={
            "file": "userprompt.md",
        },
    )


def load_user_config() -> ConfigDict:
    """
    Load the user configuration from file.

    Returns:
        ConfigDict: Loaded configuration or default if file doesn't exist.
    """
    config_path = get_user_config_path()

    if not config_path.exists():
        logging.debug("No user config file found at %s, using defaults", config_path)
        return get_default_config()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = toml.load(f)

        # Merge with defaults to ensure all keys exist
        default_config = get_default_config()

        # Merge default section
        if "default" in config_data:
            default_config["default"].update(config_data["default"])

        # Merge api_keys section
        if "api_keys" in config_data:
            default_config["api_keys"].update(config_data["api_keys"])

        # Merge prompt section
        if "prompt" in config_data:
            default_config["prompt"].update(config_data["prompt"])

        logging.debug("Loaded user config from %s", config_path)
        return default_config

    except Exception as e:
        logging.warning("Failed to load user config from %s: %s", config_path, e)
        return get_default_config()


def save_user_config(config: ConfigDict) -> None:
    """
    Save the user configuration to file.

    Args:
        config: Configuration dictionary to save.
    """
    config_path = get_user_config_path()
    config_dir = config_path.parent

    # Ensure config directory exists
    config_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            toml.dump(dict(config), f)

        logging.debug("Saved user config to %s", config_path)

    except Exception as e:
        logging.error("Failed to save user config to %s: %s", config_path, e)
        raise


def init_user_config() -> tuple[Path, Path]:
    """
    Initialize user configuration with default files.

    Creates:
    - config.toml with default configuration
    - userprompt.md with default prompt content

    Returns:
        tuple[Path, Path]: Paths to created config and prompt files.
    """
    config_dir = get_user_config_dir()
    config_path = get_user_config_path()
    prompt_path = get_user_prompt_path()

    # Create config directory
    config_dir.mkdir(parents=True, exist_ok=True)

    # Save default config
    default_config = get_default_config()
    save_user_config(default_config)

    # Create default prompt file if it doesn't exist
    if not prompt_path.exists():
        default_prompt = """You will be cleaning and formatting audio transcriptions from voice dictations. Your goal is to produce a clean, well-formatted written transcription while preserving the speaker's tone and phrasing style.

First, here is a business lexicon that contains technical terms, acronyms, and
industry-specific vocabulary that may appear in the audio:

<business_lexicon>
- Lizmap
- GRACE THD
- NATHD
- IA
- QGIS
- QField
- Airtable
- Aniita
- n8n
- PBO
- PTO
- DBT
- GRIST
- Quiberon
</business_lexicon>


Please provide a transcription following these guidelines:

**Content Preservation:**
- Maintain the speaker's natural tone and sentence structure
- Keep the original phrasing and speaking style intact
- Do not change the meaning or substantially alter the speaker's word choices
- KEEP THE SPEAKER'S LANGUAGE: if the audio is in french, the transcription should be in french

**Speech Cleaning:**
- Remove minor speech hesitations like "um", "uh", "er"
- Remove false starts where the speaker begins a word or phrase then restarts like
  "puisque je me suis... puisque j'ai toujours des broches" -> "puisque j'ai toujours des broches"

**Technical Accuracy:**
- Reference the business lexicon to correctly identify and spell technical terms,
  acronyms, or industry jargon
- If a word in the transcription seems like a mishearing of a technical term from the
  lexicon, replace it with the correct term
- When in doubt about technical terms not in the lexicon, keep the transcription as-is

**Formatting:**
- Structure the text into clear paragraphs based on topic changes or natural breaks
- Put each complete sentence on its own line
- Use proper punctuation and capitalization

**Output Format:**
[Only your transcription result, without any tags, quotes nor any mention of this prompt]

Example transformation:
Raw: "So um, we need to implement the, uh, the CRM system and, well, make sure that
all the... all the data migration goes smoothly, you know?"

Cleaned: "So we need to implement the CRM system and make sure that all the data
migration goes smoothly."
"""
        try:
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(default_prompt)
            logging.debug("Created default prompt file at %s", prompt_path)
        except Exception as e:
            logging.error("Failed to create default prompt file at %s: %s", prompt_path, e)
            raise

    logging.info("Initialized user config directory at %s", config_dir)
    return config_path, prompt_path


def get_config_value(config: ConfigDict, section: str, key: str, default: Any = None) -> Any:
    """
    Get a configuration value with fallback to default.

    Args:
        config: The configuration dictionary.
        section: The section name (e.g., "default", "api_keys", "prompt").
        key: The key within the section.
        default: Default value if key doesn't exist.

    Returns:
        The configuration value or default.
    """
    try:
        return config[section][key]
    except (KeyError, TypeError):
        return default


def get_api_key(config: ConfigDict, provider: str) -> str | None:
    """
    Get API key for a provider from config or environment variable.

    Args:
        config: The configuration dictionary.
        provider: The provider name (e.g., "mistral", "openai").

    Returns:
        The API key or None if not found.
    """
    # First try config
    api_key = get_config_value(config, "api_keys", provider)
    if api_key:
        return api_key

    # Then try environment variable
    env_var = f"{provider.upper()}_API_KEY"
    return os.getenv(env_var)
