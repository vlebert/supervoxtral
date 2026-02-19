"""
Dual-device audio recording for SuperVoxtral.

Captures audio from two input devices simultaneously (e.g. microphone + system loopback)
and mixes them into a single mono WAV file.

The two device callbacks fire independently (unsynchronized clocks). To produce a clean
mix, each source accumulates raw samples into its own buffer. A writer thread periodically
takes the samples available from both buffers, averages the overlapping portion, and
carries over any remainder for the next cycle.

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
    mic_gain: float = 1.0,
    loopback_gain: float = 1.0,
) -> float:
    """
    Record from two input devices simultaneously into a mono WAV file.

    Both sources (mic + loopback) are averaged together with optional gain
    adjustment per source.

    Args:
        output_path: Destination WAV file path (mono).
        mic_device: Microphone device index or name. None for default.
        loopback_device: Loopback device index or name (e.g. BlackHole).
        samplerate: Sample rate in Hz.
        stop_event: External stop flag. If None, records until KeyboardInterrupt.
        mic_gain: Gain multiplier for microphone (1.0 = no change, 0.5 = half, 2.0 = double).
        loopback_gain: Gain multiplier for loopback (1.0 = no change, 0.5 = half, 2.0 = double).

    Returns:
        Recording duration in seconds.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use the mic device's native sample rate to avoid PortAudio resampling artifacts
    try:
        dev_info = sd.query_devices(mic_device, "input")
        native_rate = int(dev_info["default_samplerate"])
        if native_rate > 0:
            logging.info(
                "Using device native sample rate %d Hz (requested %d Hz)", native_rate, samplerate
            )
            samplerate = native_rate
    except Exception:
        logging.debug("Could not query device native sample rate, using %d Hz", samplerate)

    # Raw sample queues — callbacks push float32 arrays in [-1.0, 1.0]
    mic_q: queue.Queue[np.ndarray[Any, np.dtype[np.float32]]] = queue.Queue()
    loop_q: queue.Queue[np.ndarray[Any, np.dtype[np.float32]]] = queue.Queue()

    writer_stop = Event()
    start_time = time.time()

    def mic_callback(
        indata: np.ndarray[Any, np.dtype[np.float32]],
        frames: int,
        time_info: sd.CallbackFlags,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.warning("Mic device status: %s", status)
        mic_q.put(indata.copy())

    def loop_callback(
        indata: np.ndarray[Any, np.dtype[np.float32]],
        frames: int,
        time_info: sd.CallbackFlags,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logging.warning("Loopback device status: %s", status)
        loop_q.put(indata.copy())

    def _drain_queue(
        q: queue.Queue[np.ndarray[Any, np.dtype[np.float32]]],
    ) -> np.ndarray[Any, np.dtype[np.float32]]:
        """Drain all pending blocks from a queue into a single float32 array."""
        blocks: list[np.ndarray[Any, np.dtype[np.float32]]] = []
        while True:
            try:
                blocks.append(q.get_nowait())
            except queue.Empty:
                break
        if not blocks:
            return np.array([], dtype=np.float32)
        return np.concatenate(blocks).flatten()

    def _mix_and_write(
        wav_file: sf.SoundFile,
        mic_carry: np.ndarray[Any, np.dtype[np.float32]],
        loop_carry: np.ndarray[Any, np.dtype[np.float32]],
    ) -> tuple[np.ndarray[Any, np.dtype[np.float32]], np.ndarray[Any, np.dtype[np.float32]]]:
        """Mix overlapping samples from both carries, write to file, return remainders."""
        mix_len = min(len(mic_carry), len(loop_carry))
        if mix_len > 0:
            mixed = mic_carry[:mix_len] * mic_gain + loop_carry[:mix_len] * loopback_gain
            wav_file.write(np.clip(mixed, -1.0, 1.0))
            mic_carry = mic_carry[mix_len:]
            loop_carry = loop_carry[mix_len:]
        return mic_carry, loop_carry

    def writer_thread(wav_file: sf.SoundFile) -> None:
        """Periodically drain both queues, mix the overlapping part, write."""
        mic_carry = np.array([], dtype=np.float32)
        loop_carry = np.array([], dtype=np.float32)

        while not writer_stop.is_set():
            time.sleep(0.05)

            mic_new = _drain_queue(mic_q)
            loop_new = _drain_queue(loop_q)
            if len(mic_new) > 0:
                mic_carry = np.concatenate([mic_carry, mic_new])
            if len(loop_new) > 0:
                loop_carry = np.concatenate([loop_carry, loop_new])

            mic_carry, loop_carry = _mix_and_write(wav_file, mic_carry, loop_carry)

        # Final drain after stop
        mic_new = _drain_queue(mic_q)
        loop_new = _drain_queue(loop_q)
        if len(mic_new) > 0:
            mic_carry = np.concatenate([mic_carry, mic_new])
        if len(loop_new) > 0:
            loop_carry = np.concatenate([loop_carry, loop_new])

        mic_carry, loop_carry = _mix_and_write(wav_file, mic_carry, loop_carry)

        # Write any leftover from whichever source has more (with gain applied)
        if len(mic_carry) > 0:
            wav_file.write(np.clip(mic_carry * mic_gain, -1.0, 1.0))
        if len(loop_carry) > 0:
            wav_file.write(np.clip(loop_carry * loopback_gain, -1.0, 1.0))

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=samplerate,
        channels=1,
        subtype="PCM_16",
    ) as wav_file:
        mic_stream = sd.InputStream(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            device=mic_device,
            callback=mic_callback,
        )
        loop_stream = sd.InputStream(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
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
        "Recorded dual WAV %s (%.2fs @ %d Hz, mono mix: mic + loopback)",
        output_path,
        duration,
        samplerate,
    )
    return duration
