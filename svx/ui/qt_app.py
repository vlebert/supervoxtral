"""
Minimal PySide6 GUI for SuperVoxtral.

This module provides a tiny always-on-top frameless window with a single "Stop" button.
Behavior:
- Starts recording immediately on launch.
- When "Stop" is pressed (or Esc), stops recording, converts to desired format (default: opus),
  sends to the transcription provider (default: mistral), copies the result to clipboard,
  optionally deletes audio files, and then exits.

UI changes in this file:
- Frameless window (no native title bar).
- Draggable window via mouse press/move on the widget.
- Monospace dark stylesheet applied to the application.
- Esc shortcut bound to Stop.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import svx.core.config as config
from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import ClipboardError, copy_to_clipboard
from svx.core.prompt import init_default_prompt_files, resolve_user_prompt
from svx.core.storage import save_transcript
from svx.providers import get_provider

__all__ = ["RecorderWindow", "run_gui"]


# Simple dark monospace stylesheet
DARK_MONO_STYLESHEET = """
/* Base window */
QWidget {
    background-color: #0f1113;
    color: #e6eef3;
    font-family: "JetBrains Mono", "Fira Code", "Menlo", "Courier New", monospace;
    font-size: 11pt;
}

/* Labels */
QLabel {
    color: #cfe8ff;
    padding: 6px;
}
/* Info line (geek/minimal) */
QLabel#info_label {
    color: #9fb8e6;
    padding: 2px 6px;
    font-size: 10pt;
}

/* Stop button */
QPushButton {
    background-color: #1f6feb;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    margin: 6px;
    min-width: 80px;
}
QPushButton:disabled {
    background-color: #274a7a;
    color: #9fb8e6;
}
QPushButton:hover {
    background-color: #2a78ff;
}

/* Small window border effect (subtle) */
QWidget#recorder_window {
    border: 1px solid #203040;
    border-radius: 8px;
}
"""


class WaveformWidget(QWidget):
    """
    Simple autonomous waveform-like widget.
    This widget does not read audio; it animates a smooth sinusoidal/breathing
    waveform to indicate recording activity. It is lightweight and self-contained.
    """

    def __init__(self, parent=None, height: int = 64) -> None:
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.phase: float = 0.0
        self.amp: float = 0.18  # base amplitude (increased for stronger motion)
        self._target_amp: float = 0.12
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)  # ~60 FPS
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()
        # lazily import time to avoid top-level dependency issues
        import time as _time

        self._last_time = _time.time()

    def _on_tick(self) -> None:
        # advance phase and animate a subtle breathing amplitude
        import math as _math
        import time as _time

        now = _time.time()
        dt = max(0.0, now - self._last_time)
        self._last_time = now
        self.phase += 10.0 * dt  # speed factor (increased for faster motion)

        # simpler breathing target using a sine on phase
        # increase breathing depth and slightly faster breathing frequency
        self._target_amp = 0.12 + 0.12 * (0.5 + 0.5 * _math.sin(self.phase * 0.35))

        # simple lerp towards target amplitude
        lerp_alpha = 0.06
        self.amp = (1.0 - lerp_alpha) * self.amp + lerp_alpha * self._target_amp
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        import math as _math

        from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

        w = self.width()
        h = self.height()
        center_y = h / 2.0

        p = QPainter(self)
        # Use RenderHint enum for compatibility with type checkers
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # background is handled by stylesheet; draw a subtle inner rect
        bg_color = QColor(20, 24, 28, 120)
        p.fillRect(0, 0, w, h, bg_color)

        # waveform color
        wave_color = QColor(90, 200, 255, 220)
        pen = QPen(wave_color)
        pen.setWidthF(2.0)
        p.setPen(pen)

        path = QPainterPath()
        samples = max(64, max(1, w // 3))
        # larger visual amplitude for a more noticeable waveform
        amplitude = (h / 1.8) * self.amp
        # draw a sin-based waveform with phase offset for motion
        for i in range(samples):
            x = (i / (samples - 1)) * w if samples > 1 else 0
            angle = (i / samples) * 4.0 * 3.14159 + self.phase
            # combine fundamental and harmonic for a richer shape
            y = center_y + amplitude * (
                0.9 * (0.6 * _math.sin(angle) + 0.4 * _math.sin(2.3 * angle))
            )
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        p.drawPath(path)


class RecorderWorker(QObject):
    """
    Worker object running the audio/transcription pipeline in a background thread.

    Signals:
        status (str): human-readable status updates for the UI.
        done (str): emitted with the final transcription text on success.
        error (str): emitted with an error message on failure.
    """

    status = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        provider: str,
        audio_format: str,
        model: str,
        language: str | None,
        rate: int,
        channels: int,
        device: str | None,
        keep_audio_files: bool,
        outfile_prefix: str | None,
        do_copy: bool,
        user_prompt: str | None,
        user_prompt_file: Path | None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.audio_format = audio_format
        self.model = model
        self.language = language
        self.rate = rate
        self.channels = channels
        self.device = device
        self.keep_audio_files = keep_audio_files
        self.outfile_prefix = outfile_prefix
        self.do_copy = do_copy
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Request the recording to stop."""
        self._stop_event.set()

    def _resolve_user_prompt(self) -> str:
        """
        Determine the final user prompt using the shared resolver.
        """
        user_cfg = config.load_user_config() or {}
        return resolve_user_prompt(
            user_cfg,
            self.user_prompt,
            self.user_prompt_file,
            config.USER_PROMPT_DIR,
            config.PROMPT_DIR,
        )

    def run(self) -> None:
        """
        Execute the pipeline:
        - record_wav (until stop)
        - optional convert (mp3/opus)
        - provider.transcribe
        - save_transcript
        - copy_to_clipboard
        - optionally delete audio files
        """
        base = self.outfile_prefix or f"rec_{timestamp()}"
        wav_path = config.RECORDINGS_DIR / f"{base}.wav"
        to_send_path = wav_path

        try:
            # 1) Record
            self.status.emit("Recording...")
            duration = record_wav(
                wav_path,
                samplerate=self.rate,
                channels=self.channels,
                device=self.device,
                stop_event=self._stop_event,
            )

            # 2) Convert if requested
            if self.audio_format in {"mp3", "opus"}:
                self.status.emit("Converting...")
                to_send_path = convert_audio(wav_path, self.audio_format)

            # 3) Transcribe
            self.status.emit("Transcribing...")
            final_user_prompt = self._resolve_user_prompt()
            prov = get_provider(self.provider)
            result = prov.transcribe(
                to_send_path,
                user_prompt=final_user_prompt,
                model=self.model,
                language=self.language,
            )
            text = result["text"]
            raw = result["raw"]

            # 4) Save outputs
            save_transcript(config.TRANSCRIPTS_DIR, base, self.provider, text, raw)

            # 5) Copy to clipboard
            if self.do_copy:
                try:
                    copy_to_clipboard(text)
                except ClipboardError as e:
                    logging.warning("Failed to copy transcript to clipboard: %s", e)

            # 6) Cleanup audio files if requested
            if not self.keep_audio_files:
                try:
                    if wav_path.exists():
                        wav_path.unlink(missing_ok=True)
                    if to_send_path != wav_path and Path(to_send_path).exists():
                        Path(to_send_path).unlink(missing_ok=True)
                except Exception:
                    logging.debug("Audio cleanup encountered a non-fatal error.", exc_info=True)

            # 7) Done
            logging.info("Recording/transcription finished (%.2fs)", duration)
            self.done.emit(text)
        except Exception as e:  # broad except to surface to the UI
            logging.exception("Pipeline failed")
            self.error.emit(str(e))


class RecorderWindow(QWidget):
    """
    Frameless always-on-top window with a single Stop button.

    Launching this window will immediately start the recording in a background thread.

    Window can be dragged by clicking anywhere on the widget background.
    Pressing Esc triggers Stop.
    """

    def __init__(
        self,
        provider: str = "mistral",
        audio_format: str = "opus",
        model: str = "voxtral-small-latest",
        language: str | None = None,
        rate: int = 16000,
        channels: int = 1,
        device: str | None = None,
        keep_audio_files: bool = False,
        outfile_prefix: str | None = None,
        do_copy: bool = True,
        log_level: str = "INFO",
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
    ) -> None:
        super().__init__()

        # Environment and prompt files
        config.setup_environment(log_level=log_level)
        init_default_prompt_files(config.PROMPT_DIR)

        # Window basics
        self.setObjectName("recorder_window")
        self.setWindowTitle("SuperVoxtral")
        # Frameless and always on top
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumWidth(260)

        # For dragging
        self._drag_active = False
        self._drag_pos = QPoint(0, 0)

        # UI layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Animated waveform (autonomous, not yet linked to audio)
        self._waveform = WaveformWidget(self, height=64)
        layout.addWidget(self._waveform)

        # Minimal geek status line under waveform (colored + bullets)
        sep = "<span style='color:#8b949e'> • </span>"
        prov_model_html = f"<span style='color:#7ee787'>{provider}/{model}</span>"
        format_html = f"<span style='color:#ffa657'>{audio_format}</span>"
        rate_html = f"<span style='color:#a5d6ff'>{rate // 1000}k/{channels}ch</span>"
        parts = [prov_model_html, format_html, rate_html]
        if language:
            lang_html = f"<span style='color:#c9b4ff'>{language}</span>"
            parts.append(lang_html)
        info_core = sep.join(parts)
        info_line = (
            "<span style='color:#8b949e'>[svx:</span> "
            f"{info_core} "
            "<span style='color:#8b949e'>]</span>"
        )
        self._info_label = QLabel(info_line)
        self._info_label.setObjectName("info_label")
        self._info_label.setTextFormat(Qt.TextFormat.RichText)
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

        self._status_label = QLabel("Recording... Press Stop to finish")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn, 0, Qt.AlignmentFlag.AlignCenter)

        # Keyboard shortcut: Esc to stop
        stop_action = QAction(self)
        stop_action.setShortcut(QKeySequence.StandardKey.Cancel)  # Esc
        stop_action.triggered.connect(self._on_stop_clicked)
        self.addAction(stop_action)

        # Background worker
        self._worker = RecorderWorker(
            provider=provider,
            audio_format=audio_format,
            model=model,
            language=language,
            rate=rate,
            channels=channels,
            device=device,
            keep_audio_files=keep_audio_files,
            outfile_prefix=outfile_prefix,
            do_copy=do_copy,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
        )
        self._thread = threading.Thread(target=self._worker.run, daemon=True)

        # Signals wiring
        self._worker.status.connect(self._on_status)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)

        # Apply stylesheet to the application for consistent appearance
        app = QApplication.instance()
        # Narrow the type to QApplication before accessing styleSheet/setStyleSheet
        if isinstance(app, QApplication):
            # Merge existing stylesheet conservatively by appending our theme
            existing = app.styleSheet() or ""
            app.setStyleSheet(existing + DARK_MONO_STYLESHEET)
        else:
            # If no app exists yet, we'll rely on run_gui to set the stylesheet.
            pass

        # Start recording immediately
        self._thread.start()
        QApplication.beep()

        # Ensure proper shutdown if user closes the window directly
        self._closing = False
        self._schedule_topmost_refresh()

    def _schedule_topmost_refresh(self) -> None:
        # Some WMs may ignore the first set; nudge it again shortly after show.
        QTimer.singleShot(50, lambda: self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True))

    def _on_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    def _on_done(self, text: str) -> None:
        self._status_label.setText("Done.")
        QApplication.beep()
        self._close_soon()

    def _on_error(self, message: str) -> None:
        QApplication.beep()
        QMessageBox.critical(self, "SuperVoxtral", f"Error: {message}")
        self._close_soon()

    def _close_soon(self) -> None:
        if not self._closing:
            self._closing = True
            QTimer.singleShot(200, self.close)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Attempt to stop recording if the user closes the window via window controls.
        self._worker.stop()
        super().closeEvent(event)

    def _on_stop_clicked(self) -> None:
        self._stop_btn.setEnabled(False)
        self._status_label.setText("Stopping...")
        self._worker.stop()

    # --- Drag handling for frameless window ---
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            # global position minus top-left corner gives offset
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # Support pressing Esc as an alternative to clicking Stop
    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        # Qt.Key_Escape is a safety stop
        if event.key() == Qt.Key.Key_Escape:
            self._on_stop_clicked()
        else:
            super().keyPressEvent(event)


def run_gui(
    provider: str = "mistral",
    audio_format: str = "opus",
    model: str = "voxtral-small-latest",
    language: str | None = None,
    rate: int = 16000,
    channels: int = 1,
    device: str | None = None,
    keep_audio_files: bool = False,
    outfile_prefix: str | None = None,
    do_copy: bool = True,
    log_level: str = "INFO",
    user_prompt: str | None = None,
    user_prompt_file: Path | None = None,
) -> None:
    """
    Launch the PySide6 app with the minimal recorder window.

    Args mirror the CLI options, with defaults matching:
      --provider mistral --format opus --copy --no-keep-audio-files
    """
    # Apply user defaults before launching the app
    config.setup_environment(log_level=log_level)
    user_cfg = config.load_user_config() or {}

    user_defaults = user_cfg.get("defaults") or {}
    if provider == "mistral" and "provider" in user_defaults:
        provider = user_defaults["provider"]
    if audio_format == "opus" and "format" in user_defaults:
        audio_format = user_defaults["format"]
    if model == "voxtral-small-latest" and "model" in user_defaults:
        model = user_defaults["model"]
    if language is None and "language" in user_defaults:
        language = user_defaults["language"]
    if rate == 16000 and "rate" in user_defaults:
        rate = int(user_defaults["rate"])
    if channels == 1 and "channels" in user_defaults:
        channels = int(user_defaults["channels"])
    if device is None and "device" in user_defaults:
        device = user_defaults["device"] or None
    if keep_audio_files is False and "keep_audio_files" in user_defaults:
        keep_audio_files = bool(user_defaults["keep_audio_files"])
    if outfile_prefix is None and "outfile_prefix" in user_defaults:
        outfile_prefix = user_defaults["outfile_prefix"] or None
    if do_copy is True and "copy" in user_defaults:
        do_copy = bool(user_defaults["copy"])
    if log_level == "INFO" and "log_level" in user_defaults:
        log_level = str(user_defaults["log_level"])

    app = QApplication.instance() or QApplication([])

    # Ensure our stylesheet is applied as early as possible
    # Narrow runtime type before calling QWidget-specific methods to satisfy static checkers.
    if isinstance(app, QApplication):
        existing = app.styleSheet() or ""
        app.setStyleSheet(existing + DARK_MONO_STYLESHEET)

    window = RecorderWindow(
        provider=provider,
        audio_format=audio_format,
        model=model,
        language=language,
        rate=rate,
        channels=channels,
        device=device,
        keep_audio_files=keep_audio_files,
        outfile_prefix=outfile_prefix,
        do_copy=do_copy,
        log_level=log_level,
        user_prompt=user_prompt,
        user_prompt_file=user_prompt_file,
    )
    window.show()
    app.exec()
