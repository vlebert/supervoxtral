"""
Framework-agnostic audio level monitor for SuperVoxtral.

Opens lightweight read-only sounddevice InputStreams alongside (and independent
from) the recording pipeline, accumulating peak RMS values for each source.
Consumers call get_and_reset_peaks() at their own cadence to read and reset
the accumulated peaks.

No Qt dependency — usable from CLI or GUI alike.
"""

from __future__ import annotations

import logging
import threading

__all__ = ["AudioLevelMonitor"]


class AudioLevelMonitor:
    """
    Monitors audio input levels by opening lightweight read-only streams.

    Thread-safe peak accumulation: each PortAudio callback updates a max-hold
    peak; get_and_reset_peaks() returns and resets the values atomically.

    Args:
        mic_device: Device index or name for the microphone (None = system default).
        loopback_device: Device name for the loopback source, or None to disable.
    """

    def __init__(
        self,
        mic_device: int | str | None = None,
        loopback_device: str | None = None,
    ) -> None:
        self._mic_device = mic_device
        self._loop_device = loopback_device
        self._lock = threading.Lock()
        self._mic_peak: float = 0.0
        self._loop_peak: float = 0.0
        self._mic_stream: object = None
        self._loop_stream: object = None

    # --- PortAudio real-time callbacks (called from audio thread) ---

    def _mic_cb(self, indata, frames, time_info, status) -> None:  # type: ignore[override]
        import numpy as np

        rms = float(np.sqrt(np.mean(indata**2)))
        with self._lock:
            if rms > self._mic_peak:
                self._mic_peak = rms

    def _loop_cb(self, indata, frames, time_info, status) -> None:  # type: ignore[override]
        import numpy as np

        rms = float(np.sqrt(np.mean(indata**2)))
        with self._lock:
            if rms > self._loop_peak:
                self._loop_peak = rms

    # --- Public API ---

    def start(self) -> None:
        """Open monitoring streams and begin accumulating peak values."""
        import sounddevice as sd

        try:
            self._mic_stream = sd.InputStream(
                device=self._mic_device,
                channels=1,
                dtype="float32",
                callback=self._mic_cb,
            )
            self._mic_stream.start()  # type: ignore[union-attr]
        except Exception:
            logging.debug("AudioLevelMonitor: could not open mic monitor stream", exc_info=True)

        if self._loop_device is not None:
            try:
                self._loop_stream = sd.InputStream(
                    device=self._loop_device,
                    channels=1,
                    dtype="float32",
                    callback=self._loop_cb,
                )
                self._loop_stream.start()  # type: ignore[union-attr]
            except Exception:
                logging.debug(
                    "AudioLevelMonitor: could not open loopback monitor stream", exc_info=True
                )

    def stop(self) -> None:
        """Close monitoring streams and reset peaks."""
        for stream in (self._mic_stream, self._loop_stream):
            if stream is not None:
                try:
                    stream.stop()  # type: ignore[union-attr]
                    stream.close()  # type: ignore[union-attr]
                except Exception:
                    pass
        self._mic_stream = None
        self._loop_stream = None
        with self._lock:
            self._mic_peak = 0.0
            self._loop_peak = 0.0

    def push_mic(self, rms: float) -> None:
        """Push a mic RMS value from an external source (e.g. a recording callback)."""
        with self._lock:
            if rms > self._mic_peak:
                self._mic_peak = rms

    def push_loop(self, rms: float) -> None:
        """Push a loopback RMS value from an external source."""
        with self._lock:
            if rms > self._loop_peak:
                self._loop_peak = rms

    def get_and_reset_peaks(self) -> tuple[float, float]:
        """
        Return (mic_peak_rms, loop_peak_rms) accumulated since the last call,
        then reset both accumulators to zero.

        loop_peak_rms is -1.0 when no loopback device is configured.
        """
        with self._lock:
            mic = self._mic_peak
            loop = self._loop_peak if self._loop_device is not None else -1.0
            self._mic_peak = 0.0
            self._loop_peak = 0.0
        return mic, loop
