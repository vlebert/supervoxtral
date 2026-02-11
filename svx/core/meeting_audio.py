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
) -> float:
    """
    Record from two input devices simultaneously into a mono WAV file.

    Both sources (mic + loopback) are averaged together. Adjust input levels
    at the OS level if one source is too loud or too quiet.

    Args:
        output_path: Destination WAV file path (mono).
        mic_device: Microphone device index or name. None for default.
        loopback_device: Loopback device index or name (e.g. BlackHole).
        samplerate: Sample rate in Hz.
        stop_event: External stop flag. If None, records until KeyboardInterrupt.

    Returns:
        Recording duration in seconds.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Raw sample queues — callbacks push int16 arrays, no processing
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

    def _drain_queue(
        q: queue.Queue[np.ndarray[Any, np.dtype[np.int16]]],
    ) -> np.ndarray[Any, np.dtype[np.float32]]:
        """Drain all pending blocks from a queue into a single float32 array."""
        blocks: list[np.ndarray[Any, np.dtype[np.int16]]] = []
        while True:
            try:
                blocks.append(q.get_nowait())
            except queue.Empty:
                break
        if not blocks:
            return np.array([], dtype=np.float32)
        return np.concatenate(blocks).flatten().astype(np.float32)

    def writer_thread(wav_file: sf.SoundFile) -> None:
        """Periodically drain both queues, average the overlapping part, write."""
        # Persistent carry-over buffers for samples not yet mixed
        mic_carry = np.array([], dtype=np.float32)
        loop_carry = np.array([], dtype=np.float32)

        while not writer_stop.is_set():
            time.sleep(0.05)

            # Drain queues and append to carry-over
            mic_new = _drain_queue(mic_q)
            loop_new = _drain_queue(loop_q)

            if len(mic_new) > 0:
                mic_carry = np.concatenate([mic_carry, mic_new])
            if len(loop_new) > 0:
                loop_carry = np.concatenate([loop_carry, loop_new])

            # Mix the overlapping portion (average), keep remainder
            mic_len = len(mic_carry)
            loop_len = len(loop_carry)
            mix_len = min(mic_len, loop_len)

            if mix_len > 0:
                mixed = (mic_carry[:mix_len] + loop_carry[:mix_len]) * 0.5
                clipped = np.clip(mixed, -32768.0, 32767.0).astype(np.int16)
                wav_file.write(clipped)
                mic_carry = mic_carry[mix_len:]
                loop_carry = loop_carry[mix_len:]

        # Final drain after stop
        mic_new = _drain_queue(mic_q)
        loop_new = _drain_queue(loop_q)
        if len(mic_new) > 0:
            mic_carry = np.concatenate([mic_carry, mic_new])
        if len(loop_new) > 0:
            loop_carry = np.concatenate([loop_carry, loop_new])

        mic_len = len(mic_carry)
        loop_len = len(loop_carry)
        mix_len = min(mic_len, loop_len)

        if mix_len > 0:
            mixed = (mic_carry[:mix_len] + loop_carry[:mix_len]) * 0.5
            clipped = np.clip(mixed, -32768.0, 32767.0).astype(np.int16)
            wav_file.write(clipped)
            mic_carry = mic_carry[mix_len:]
            loop_carry = loop_carry[mix_len:]

        # Write any leftover from whichever source has more
        if len(mic_carry) > 0:
            wav_file.write(mic_carry.astype(np.int16))
        if len(loop_carry) > 0:
            wav_file.write(loop_carry.astype(np.int16))

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
        "Recorded dual WAV %s (%.2fs @ %d Hz, mono mix: mic + loopback)",
        output_path,
        duration,
        samplerate,
    )
    return duration
