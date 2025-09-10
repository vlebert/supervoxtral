"""
Core package for audio recording, encoding, configuration, and storage.

This module provides lightweight placeholders and shared types/constants that other
modules (e.g., recorder, encoder, config, storage) can import and extend later.

Planned submodules:
- recorder.py: microphone capture to WAV (streamed) with manual stop
- encoder.py: conversion to MP3/Opus via ffmpeg (optional)
- config.py: environment/config management, defaults, validation
- storage.py: file naming, directory management, transcript persistence
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Defaults and shared constants
DEFAULT_SAMPLE_RATE: int = 16000
DEFAULT_CHANNELS: int = 1
SUPPORTED_FORMATS: tuple[str, ...] = ("wav", "mp3", "opus")
DEFAULT_WAV_SUBTYPE: str = "PCM_16"


@dataclass(slots=True)
class RecordingSettings:
    """Parameters used during raw WAV recording."""

    samplerate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    # On some platforms, device can be an int index or a device name string.
    device: int | str | None = None
    subtype: str = DEFAULT_WAV_SUBTYPE  # e.g., "PCM_16"


@dataclass(slots=True)
class EncodingSettings:
    """Parameters for post-recording encoding/export."""

    # Output format to send to provider; recording is always WAV
    output_format: Literal["wav", "mp3", "opus"] = "wav"
    # For opus/mp3, recommended bitrates/quality settings (ffmpeg-specific)
    opus_bitrate: str = "64k"  # used if output_format == "opus"
    mp3_quality: int = 3  # LAME VBR quality (lower is better; typical 0-9)
    keep_wav: bool = True  # keep raw WAV after conversion


@dataclass(slots=True)
class Paths:
    """Project paths used by the CLI and providers."""

    root: Path = Path.cwd()
    recordings_dir: Path = Path("recordings")
    transcripts_dir: Path = Path("transcripts")
    logs_dir: Path = Path("logs")

    @property
    def abs_root(self) -> Path:
        return self.root.resolve()

    @property
    def abs_recordings(self) -> Path:
        return (self.abs_root / self.recordings_dir).resolve()

    @property
    def abs_transcripts(self) -> Path:
        return (self.abs_root / self.transcripts_dir).resolve()

    @property
    def abs_logs(self) -> Path:
        return (self.abs_root / self.logs_dir).resolve()


def ensure_directories(paths: Paths) -> None:
    """Create needed directories if they don't exist."""
    paths.abs_recordings.mkdir(parents=True, exist_ok=True)
    paths.abs_transcripts.mkdir(parents=True, exist_ok=True)
    paths.abs_logs.mkdir(parents=True, exist_ok=True)


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_CHANNELS",
    "SUPPORTED_FORMATS",
    "DEFAULT_WAV_SUBTYPE",
    "RecordingSettings",
    "EncodingSettings",
    "Paths",
    "ensure_directories",
]
