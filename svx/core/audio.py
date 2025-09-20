"""
Audio utilities for SuperVoxtral.

This module provides:
- WAV recording from microphone to a file.
- ffmpeg detection.
- Conversion from WAV to MP3 or Opus using ffmpeg.
- Optional helpers for listing/selecting audio input devices.

Dependencies:
- sounddevice
- soundfile
"""

from __future__ import annotations

import logging
import queue
import subprocess
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any

import sounddevice as sd
import soundfile as sf

__all__ = [
    "timestamp",
    "detect_ffmpeg",
    "convert_audio",
    "record_wav",
    "list_input_devices",
    "default_input_device_index",
]


def timestamp() -> str:
    """
    Return a compact timestamp suitable for filenames: YYYYMMDD_HHMMSS.
    """
    return time.strftime("%Y%m%d_%H%M%S")


def detect_ffmpeg() -> str | None:
    """
    Return 'ffmpeg' if available on PATH, otherwise None.
    """
    try:
        subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return "ffmpeg"
    except Exception:
        return None


def convert_audio(input_wav: Path, fmt: str) -> Path:
    """
    Convert a WAV file to the target compressed audio format using ffmpeg.

    Args:
        input_wav: Path to the source WAV file.
        fmt: Target format, one of {'mp3', 'opus'}.

    Returns:
        Path to the converted file.

    Raises:
        AssertionError: If fmt is not supported.
        RuntimeError: If ffmpeg is not available or conversion fails.
    """
    assert fmt in {"mp3", "opus"}, "fmt must be 'mp3' or 'opus'"
    ffmpeg_bin = detect_ffmpeg()
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg (e.g., brew install ffmpeg).")

    output_path = input_wav.with_suffix(f".{fmt}")
    if fmt == "mp3":
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(output_path),
        ]
    else:  # opus
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            str(output_path),
        ]

    logging.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.error("ffmpeg failed: %s", proc.stderr.strip())
        raise RuntimeError(f"ffmpeg conversion failed with code {proc.returncode}")
    return output_path


def record_wav(
    output_path: Path,
    samplerate: int = 16000,
    channels: int = 1,
    device: int | str | None = None,
    duration_seconds: float | None = None,
    stop_event: Event | None = None,
) -> float:
    """
    Record audio from the default (or specified) input device to a WAV file.

    This function records until one of the following happens:
      - `duration_seconds` elapses (if provided)
      - `stop_event` is set (if provided)
      - a KeyboardInterrupt/EOFError is received

    Note: This function does not handle any interactive UI. If you want a
    "press Enter to stop" behavior, the caller should manage input and set
    the provided `stop_event` accordingly.

    Args:
        output_path: Destination WAV file path.
        samplerate: Sample rate in Hz (e.g., 16000 or 32000).
        channels: Number of channels (1=mono, 2=stereo).
        device: Input device index or name. None uses the default device.
        duration_seconds: Fixed recording duration. If None, run until stop_event or interrupt.
        stop_event: External stop flag. If None and duration_seconds is None, waits for interrupt.

    Returns:
        The recorded duration in seconds (float).
    """
    if channels < 1:
        raise ValueError("channels must be >= 1")
    if samplerate <= 0:
        raise ValueError("samplerate must be > 0")

    q: queue.Queue = queue.Queue()
    writer_stop = Event()
    start_time = time.time()

    def audio_callback(indata, frames, time_info, status):
        if status:
            logging.warning("SoundDevice status: %s", status)
        q.put(indata.copy())

    def writer_thread(wav_file: sf.SoundFile) -> None:
        while not writer_stop.is_set():
            try:
                data = q.get(timeout=0.1)
                wav_file.write(data)
            except queue.Empty:
                continue
            except Exception as e:
                logging.exception("Error writing WAV data: %s", e)
                writer_stop.set()

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=samplerate,
        channels=channels,
        subtype="PCM_16",
    ) as wav_file:
        with sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            device=device,
            callback=audio_callback,
        ):
            t = Thread(target=writer_thread, args=(wav_file,), daemon=True)
            t.start()

            try:
                if duration_seconds is not None:
                    # Fixed-duration recording
                    end_time = start_time + float(duration_seconds)
                    while time.time() < end_time:
                        if stop_event is not None and stop_event.is_set():
                            break
                        time.sleep(0.05)
                else:
                    # Indefinite recording until stop_event or interrupt
                    while True:
                        if stop_event is not None and stop_event.is_set():
                            break
                        time.sleep(0.05)
            except (KeyboardInterrupt, EOFError):
                # Graceful stop on user interrupt
                pass
            finally:
                writer_stop.set()
                t.join()

    duration = time.time() - start_time
    logging.info(
        "Recorded WAV %s (%.2fs @ %d Hz, %d ch)", output_path, duration, samplerate, channels
    )
    return duration


def list_input_devices() -> list[dict[str, Any]]:
    """
    Return a list of available input devices with basic metadata.

    Each entry contains:
      - index: device index
      - name: device name
      - max_input_channels: maximum input channels supported
      - default_samplerate: default sample rate (may be None)
    """
    devices = sd.query_devices()
    results: list[dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        try:
            if int(dev.get("max_input_channels", 0)) > 0:
                results.append(
                    {
                        "index": idx,
                        "name": dev.get("name"),
                        "max_input_channels": dev.get("max_input_channels"),
                        "default_samplerate": dev.get("default_samplerate"),
                    }
                )
        except Exception:
            # Be defensive: ignore any malformed device entries
            continue
    return results


def default_input_device_index() -> int | None:
    """
    Return the default input device index if available, otherwise None.
    """
    try:
        defaults = sd.default.device  # (input, output)
        if isinstance(defaults, (list, tuple)) and len(defaults) >= 1:
            idx = defaults[0]
            return int(idx) if idx is not None else None
    except Exception:
        return None
    return None
