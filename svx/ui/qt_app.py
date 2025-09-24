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
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import svx.core.config as config
from svx.core.config import Config
from svx.core.pipeline import RecordingPipeline
from svx.core.prompt import resolve_user_prompt

__all__ = ["RecorderWindow", "run_gui"]


# Simple dark monospace stylesheet
DARK_MONO_STYLESHEET = """
/* Base window */
QWidget {
    background-color: #0f1113;
    color: #e6eef3;
    /* font-family set via QApplication.setFont */
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
    background-color: #1e40af;
    color: #ffffff;
    border: none;
    border-radius: 2px;
    padding: 4px 8px;
    margin: 6px;
    min-width: 60px;
}
QPushButton:disabled {
    background-color: #374151;
    color: #9ca3af;
}
QPushButton:hover {
    background-color: #1d4ed8;
}

/* Cancel button */
QPushButton#cancel_btn {
    background-color: #b91c1c;
}
QPushButton#cancel_btn:hover {
    background-color: #ef4444;
}
QPushButton#cancel_btn:disabled {
    background-color: #4b5563;
    color: #9ca3af;
}

/* Small window border effect (subtle) */
QWidget#recorder_window {
    border: 1px solid #203040;
    border-radius: 8px;
}
"""


def get_fixed_font(point_size: int = 11) -> QFont:
    """
    Return the system fixed-width font with the given point size.
    Using QFontDatabase.FixedFont avoids missing-family substitution warnings.
    """
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSize(point_size)
    return f


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
    canceled = Signal()

    def __init__(
        self,
        cfg: Config,
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.mode: str | None = None
        self.cancel_requested: bool = False
        self._stop_event = threading.Event()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def stop(self) -> None:
        """Request the recording to stop."""
        self._stop_event.set()

    def cancel(self) -> None:
        self.cancel_requested = True
        self._stop_event.set()

    def _resolve_user_prompt(self, key: str) -> str:
        """
        Determine the final user prompt using the shared resolver for the given key.
        """
        return resolve_user_prompt(self.cfg, None, None, self.cfg.user_prompt_dir, key=key)

    def run(self) -> None:
        """
        Execute the pipeline:
        - record (until stop)
        - wait for mode
        - process
        - clean
        """

        try:
            pipeline = RecordingPipeline(
                cfg=self.cfg,
                user_prompt=self.user_prompt,
                user_prompt_file=self.user_prompt_file,
                save_all=self.save_all,
                outfile_prefix=self.outfile_prefix,
                progress_callback=self.status.emit,
            )
            self.status.emit("Recording in progress...")
            wav_path, duration = pipeline.record(self._stop_event)
            self.status.emit("Recording finished.")
            if self.cancel_requested:
                keep_audio = self.save_all or self.cfg.defaults.keep_audio_files
                pipeline.clean(wav_path, {"wav": wav_path}, keep_audio)
                self.canceled.emit()
                return
            self.status.emit("Processing in progress...")
            # Wait for user to select mode in the GUI
            while self.mode is None:
                time.sleep(0.05)

            # Log the selected mode/key for debugging prompt application
            try:
                logging.info("RecorderWorker: selected mode/key: %s", self.mode)
            except Exception:
                # ensure failures in logging don't break the worker
                pass

            transcribe_mode = self.mode == "transcribe"
            if transcribe_mode:
                user_prompt = None
            else:
                # Resolve the user prompt for the selected key and log a short snippet
                user_prompt = self._resolve_user_prompt(self.mode)
                try:
                    if user_prompt:
                        snippet = (
                            user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt
                        )
                    else:
                        snippet = "<EMPTY>"
                    logging.info(
                        "RecorderWorker: resolved prompt snippet for key '%s': %s",
                        self.mode,
                        snippet,
                    )
                except Exception:
                    # avoid breaking the flow on logging errors
                    pass

            result = pipeline.process(wav_path, duration, transcribe_mode, user_prompt)
            keep_audio = self.save_all or self.cfg.defaults.keep_audio_files
            pipeline.clean(wav_path, result["paths"], keep_audio)
            self.done.emit(result["text"])
        except Exception as e:
            logging.exception("Pipeline failed")
            self.error.emit(str(e))


class RecorderWindow(QWidget):
    """
    Frameless always-on-top window with Transcribe and Prompt buttons.

    Launching this window will immediately start the recording in a background thread.

    Window can be dragged by clicking anywhere on the widget background.
    Pressing Esc triggers Prompt mode.
    """

    def __init__(
        self,
        cfg: Config,
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
    ) -> None:
        super().__init__()

        self.cfg = cfg
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.prompt_keys = sorted(self.cfg.prompt.prompts.keys())

        # Background worker (create early for signal connections)
        self._worker = RecorderWorker(
            cfg=self.cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
        )
        self._thread = threading.Thread(target=self._worker.run, daemon=True)

        # Environment and prompt files

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
        prov_model_html = (
            f"<span style='color:#7ee787'>"
            f"{self.cfg.defaults.provider}/{self.cfg.defaults.model}"
            "</span>"
        )
        format_html = f"<span style='color:#ffa657'>{self.cfg.defaults.format}</span>"
        parts = [
            prov_model_html,
            format_html,
        ]
        if self.cfg.defaults.language:
            lang_html = f"<span style='color:#c9b4ff'>{self.cfg.defaults.language}</span>"
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

        self._status_label = QLabel("Recording in progress...")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        # Buttons layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self._transcribe_btn = QPushButton("Transcribe")
        self._transcribe_btn.setToolTip("Stop and transcribe without prompt")
        self._transcribe_btn.clicked.connect(
            lambda checked=False, m="transcribe": self._on_mode_selected(m)
        )
        button_layout.addWidget(self._transcribe_btn)
        self._prompt_buttons: dict[str, QPushButton] = {}
        for key in self.prompt_keys:
            btn = QPushButton(key.capitalize())
            btn.setToolTip(f"Stop and transcribe with '{key}' prompt")
            btn.clicked.connect(lambda checked=False, k=key: self._on_mode_selected(k))
            self._prompt_buttons[key] = btn
            button_layout.addWidget(btn)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("cancel_btn")
        self._cancel_btn.setToolTip("Stop recording and quit without processing")
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self._cancel_btn)
        button_layout.addStretch()
        button_widget = QWidget()
        button_widget.setLayout(button_layout)
        layout.addWidget(button_widget, 0, Qt.AlignmentFlag.AlignCenter)

        self._action_buttons = [self._transcribe_btn] + list(self._prompt_buttons.values())

        # Keyboard shortcut: Esc to stop
        stop_action = QAction(self)
        stop_action.setShortcut(QKeySequence.StandardKey.Cancel)  # Esc
        stop_action.triggered.connect(lambda: self._worker.cancel())
        self.addAction(stop_action)

        # Signals wiring
        self._worker.status.connect(self._on_status)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.canceled.connect(self._close_soon)

        # Apply stylesheet to the application for consistent appearance
        app = QApplication.instance()
        # Narrow the type to QApplication before accessing styleSheet/setStyleSheet
        if isinstance(app, QApplication):
            # Set system fixed-width font and merge stylesheet conservatively
            app.setFont(get_fixed_font(11))
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
        self._worker.cancel()
        super().closeEvent(event)

    def _on_mode_selected(self, mode: str) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("Stopping and processing...")
        self._worker.set_mode(mode)
        self._worker.stop()

    def _on_cancel_clicked(self) -> None:
        for btn in self._action_buttons:
            btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("Canceling...")
        self._worker.cancel()

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
            self._worker.cancel()
        else:
            super().keyPressEvent(event)


def run_gui(
    cfg: Config | None = None,
    user_prompt: str | None = None,
    user_prompt_file: Path | None = None,
    save_all: bool = False,
    outfile_prefix: str | None = None,
    log_level: str = "INFO",
) -> None:
    if cfg is None:
        cfg = Config.load(log_level=log_level)
    """
    Launch the PySide6 app with the minimal recorder window.
    """
    config.setup_environment(log_level=log_level)

    app = QApplication.instance() or QApplication([])
    if isinstance(app, QApplication):
        app.setFont(get_fixed_font(11))

    # Ensure our stylesheet is applied as early as possible
    # Narrow runtime type before calling QWidget-specific methods to satisfy static checkers.
    if isinstance(app, QApplication):
        existing = app.styleSheet() or ""
        app.setStyleSheet(existing + DARK_MONO_STYLESHEET)

    window = RecorderWindow(
        cfg=cfg,
        user_prompt=user_prompt,
        user_prompt_file=user_prompt_file,
        save_all=save_all,
        outfile_prefix=outfile_prefix,
    )
    window.show()
    app.exec()
