"""
Dual-device audio recording for SuperVoxtral.

Captures audio from two input devices simultaneously (e.g. microphone + system loopback)
and writes a stereo WAV file with mic on left channel and loopback on right channel.

Dependencies:
- sounddevice
- soundfile
- numpy
"""

from __future__ import annotations

import logging
import queue
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

__all__ = [
    "record_dual_wav",
    "find_loopback_device",
]


def find_loopback_device(name: str) -> int | None:
    """
    Find a loopback input device by name.

    Searches sounddevice's device list for a device whose name contains `name`
    (case-insensitive) and has at least one input channel.

    Args:
        name: Substring to match in device names (e.g. "BlackHole 2ch").

    Returns:
        Device index if found, None otherwise.
    """
    devices = sd.query_devices()
    name_lower = name.lower()
    for idx, dev in enumerate(devices):
        dev_name = str(dev.get("name", "")).lower()
        max_input = int(dev.get("max_input_channels", 0))
        if name_lower in dev_name and max_input > 0:
            logging.info("Found loopback device: [%d] %s", idx, dev.get("name"))
            return idx
    logging.warning("Loopback device '%s' not found", name)
    return None


def record_dual_wav(
    output_path: Path,
    mic_device: int | str | None,
    loopback_device: int | str,
    samplerate: int = 16000,
    stop_event: Event | None = None,
) -> float:
    """
    Record from two input devices simultaneously into a stereo WAV file.

    Left channel = microphone, Right channel = loopback (system audio).

    Args:
        output_path: Destination WAV file path (stereo).
        mic_device: Microphone device index or name. None for default.
        loopback_device: Loopback device index or name (e.g. BlackHole).
        samplerate: Sample rate in Hz.
        stop_event: External stop flag. If None, records until KeyboardInterrupt.

    Returns:
        Recording duration in seconds.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mic_q: queue.Queue[np.ndarray[Any, np.dtype[np.int16]]] = queue.Queue()
    loop_q: queue.Queue[np.ndarray[Any, np.dtype[np.int16]]] = queue.Queue()
    writer_stop = Event()
    start_time = time.time()

    def mic_callback(
        indata: np.ndarray[Any, np.dtype[np.int16]],
        frames: int,
        time_info: sd.CallbackFlags,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.warning("Mic device status: %s", status)
        mic_q.put(indata.copy())

    def loop_callback(
        indata: np.ndarray[Any, np.dtype[np.int16]],
        frames: int,
        time_info: sd.CallbackFlags,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.warning("Loopback device status: %s", status)
        loop_q.put(indata.copy())

    def _write_pair(
        wav_file: sf.SoundFile,
        mic_data: np.ndarray[Any, np.dtype[np.int16]] | None,
        loop_data: np.ndarray[Any, np.dtype[np.int16]] | None,
    ) -> None:
        """Write one pair of mic/loopback buffers as interleaved stereo."""
        if mic_data is not None and loop_data is not None:
            min_len = min(len(mic_data), len(loop_data))
            stereo = np.column_stack((
                mic_data[:min_len].flatten(),
                loop_data[:min_len].flatten(),
            ))
            wav_file.write(stereo)
        elif mic_data is not None:
            mono = mic_data.flatten()
            wav_file.write(np.column_stack((mono, np.zeros_like(mono))))
        elif loop_data is not None:
            mono = loop_data.flatten()
            wav_file.write(np.column_stack((np.zeros_like(mono), mono)))

    def writer_thread(wav_file: sf.SoundFile) -> None:
        """Interleave mic (L) and loopback (R) into stereo frames."""
        while not writer_stop.is_set():
            mic_data: np.ndarray[Any, np.dtype[np.int16]] | None = None
            loop_data: np.ndarray[Any, np.dtype[np.int16]] | None = None

            try:
                mic_data = mic_q.get(timeout=0.1)
            except queue.Empty:
                pass
            try:
                loop_data = loop_q.get(timeout=0.1)
            except queue.Empty:
                pass

            _write_pair(wav_file, mic_data, loop_data)

        # Drain remaining buffered data after stop
        while not mic_q.empty() or not loop_q.empty():
            mic_data = None
            loop_data = None
            try:
                mic_data = mic_q.get_nowait()
            except queue.Empty:
                pass
            try:
                loop_data = loop_q.get_nowait()
            except queue.Empty:
                pass
            _write_pair(wav_file, mic_data, loop_data)

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=samplerate,
        channels=2,
        subtype="PCM_16",
    ) as wav_file:
        mic_stream = sd.InputStream(
            samplerate=samplerate,
            channels=1,
            dtype="int16",
            device=mic_device,
            callback=mic_callback,
        )
        loop_stream = sd.InputStream(
            samplerate=samplerate,
            channels=1,
            dtype="int16",
            device=loopback_device,
            callback=loop_callback,
        )

        with mic_stream, loop_stream:
            t = Thread(target=writer_thread, args=(wav_file,), daemon=True)
            t.start()

            try:
                while True:
                    if stop_event is not None and stop_event.is_set():
                        break
                    time.sleep(0.05)
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                writer_stop.set()
                t.join()

    duration = time.time() - start_time
    logging.info(
        "Recorded dual WAV %s (%.2fs @ %d Hz, stereo: mic L + loopback R)",
        output_path,
        duration,
        samplerate,
    )
    return duration
