"""
Framework-agnostic audio level monitor for SuperVoxtral.

Pure push-based accumulator: the recording pipeline feeds RMS values via
push_mic() / push_loop() from its own audio callbacks. No separate
sounddevice streams are opened. Consumers call get_and_reset_peaks() at
their own cadence to read and reset the accumulated peaks.

No Qt dependency — usable from CLI or GUI alike.
"""

from __future__ import annotations

import threading

__all__ = ["AudioLevelMonitor"]


class AudioLevelMonitor:
    """
    Thread-safe peak accumulator for audio level monitoring.

    The recording pipeline feeds values via push_mic() / push_loop().
    Consumers call get_and_reset_peaks() at their own polling cadence.

    Args:
        mic_device: Unused (kept for API compatibility). Device selection is
            handled by the recording pipeline, not the monitor.
        loopback_device: Device name for the loopback source, or None to
            disable. Used only to determine whether loop_peak_rms is valid.
    """

    def __init__(
        self,
        mic_device: int | str | None = None,
        loopback_device: str | None = None,
    ) -> None:
        self._loop_device = loopback_device
        self._lock = threading.Lock()
        self._mic_peak: float = 0.0
        self._loop_peak: float = 0.0

    # --- Public API ---

    def stop(self) -> None:
        """Reset accumulated peaks."""
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
