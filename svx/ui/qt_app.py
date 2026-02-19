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
import math
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import svx.core.config as config
from svx.core.config import Config
from svx.core.pipeline import RecordingPipeline
from svx.core.prompt import resolve_user_prompt

__all__ = ["RecorderWindow", "run_gui"]

_SETTINGS_ORG = "supervoxtral"
_SETTINGS_APP = "ui"
_KEY_REVIEW_MODE = "review_mode"

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

QCheckBox {
    color: #9fb8e6;
    spacing: 6px;
    padding: 2px 6px;
}
QCheckBox::indicator {
    width: 13px; height: 13px;
    border: 1px solid #374151;
    border-radius: 2px;
    background-color: #161b22;
}
QCheckBox::indicator:checked {
    background-color: #1e40af;
    border-color: #1d4ed8;
}
QDialog {
    background-color: #0f1113;
    border: 1px solid #203040;
    border-radius: 4px;
}
QTextEdit {
    background-color: #161b22;
    color: #e6eef3;
    border: 1px solid #30363d;
    border-radius: 2px;
    padding: 4px;
    selection-background-color: #1e40af;
}
QSplitter::handle {
    background-color: #203040;
    width: 3px;
}
QLabel#panel_header {
    color: #8b949e;
    font-size: 9pt;
    padding: 2px 0px;
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


class LevelMeterWidget(QWidget):
    """
    Compact horizontal audio level meter with a retro segmented look.

    Draws discrete LED-style segments in muted dark tones (cyan → amber → dark-red)
    to match the app's dark monospace aesthetic. Includes a peak-hold segment.
    """

    _LABEL_W = 44
    _NUM_SEGS = 20
    _SEG_GAP = 2
    _TRACK_H = 8

    # Colour zones (segment index thresholds)
    _WARN_SEG = int(_NUM_SEGS * 0.68)   # amber starts here
    _CLIP_SEG = int(_NUM_SEGS * 0.86)   # dark-red starts here

    # Muted palette — dark enough to feel at home in the #0f1113 theme
    _COL_OFF    = (13,  26,  34)   # barely-visible inactive segment
    _COL_ON_LO  = (14, 116, 144)   # dark cyan  (normal signal)
    _COL_ON_MID = (161,  88,  10)  # dark amber (warning)
    _COL_ON_HI  = (160,  30,  30)  # dark red   (clip)
    _COL_PK_LO  = (30,  160, 196)  # peak: brighter cyan
    _COL_PK_MID = (210, 110,  14)  # peak: brighter amber
    _COL_PK_HI  = (210,  45,  45)  # peak: brighter red

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._display_level: float = 0.0
        self._peak: float = 0.0
        self.setMinimumHeight(22)
        self.setMaximumHeight(22)
        self._decay_timer = QTimer(self)
        self._decay_timer.setInterval(80)
        self._decay_timer.timeout.connect(self._decay)
        self._decay_timer.start()

    def set_level(self, rms: float) -> None:
        """Update the meter with a new RMS value (0.0 .. 1.0)."""
        level = 0.0
        if rms > 1e-5:
            # Log scale: map [-50 dB, 0 dB] → [0, 1]
            level = max(0.0, min(1.0, (20 * math.log10(rms) + 50) / 50))
        if level > self._display_level:
            self._display_level = level
        if self._display_level > self._peak:
            self._peak = self._display_level
        self.update()

    def _decay(self) -> None:
        changed = self._display_level > 0.0 or self._peak > 0.0
        self._display_level = max(0.0, self._display_level * 0.82)
        self._peak = max(0.0, self._peak - 0.018)
        if changed:
            self.update()

    def _zone_colors(self, seg: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """Return (on_color, peak_color) for the given segment index."""
        if seg >= self._CLIP_SEG:
            return self._COL_ON_HI, self._COL_PK_HI
        if seg >= self._WARN_SEG:
            return self._COL_ON_MID, self._COL_PK_MID
        return self._COL_ON_LO, self._COL_PK_LO

    def paintEvent(self, event) -> None:  # type: ignore[override]
        from PySide6.QtGui import QColor, QPainter

        h = self.height()
        bar_x = self._LABEL_W + 4
        bar_w = max(1, self.width() - bar_x - 4)
        bar_y = (h - self._TRACK_H) // 2

        seg_w = max(1, (bar_w - (self._NUM_SEGS - 1) * self._SEG_GAP) // self._NUM_SEGS)

        p = QPainter(self)

        # Label — muted blue-grey, same family as info_label
        p.setPen(QColor(100, 140, 172))
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        p.drawText(
            0,
            0,
            self._LABEL_W,
            h,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            self._label,
        )

        active = int(self._NUM_SEGS * self._display_level)
        peak_seg = int(self._NUM_SEGS * self._peak)
        show_peak = self._peak > 0.04 and peak_seg < self._NUM_SEGS

        for i in range(self._NUM_SEGS):
            x = bar_x + i * (seg_w + self._SEG_GAP)
            on_col, pk_col = self._zone_colors(i)
            is_active = i < active
            is_peak = show_peak and i == peak_seg and not is_active
            if is_active:
                r, g, b = on_col
            elif is_peak:
                r, g, b = pk_col
            else:
                r, g, b = self._COL_OFF
            p.fillRect(x, bar_y, seg_w, self._TRACK_H, QColor(r, g, b))


class AudioLevelMonitor(QObject):
    """
    Monitors audio input levels by opening lightweight read-only streams
    alongside (and independent from) the recording pipeline streams.

    Emits `levels(mic_rms, loop_rms)` at ~20 Hz.
    `loop_rms` is -1.0 when no loopback device is configured.
    """

    levels = Signal(float, float)

    def __init__(
        self,
        mic_device: int | str | None = None,
        loopback_device: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._mic_device = mic_device
        self._loop_device = loopback_device
        self._mic_rms: float = 0.0
        self._loop_rms: float = 0.0
        self._mic_stream: object = None
        self._loop_stream: object = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)  # 20 Hz
        self._timer.timeout.connect(self._emit_and_decay)

    # --- audio callbacks (called from PortAudio real-time thread) ---

    def _mic_cb(self, indata, frames, time_info, status) -> None:  # type: ignore[override]
        import numpy as np

        rms = float(np.sqrt(np.mean(indata**2)))
        # Peak-hold between emit frames
        if rms > self._mic_rms:
            self._mic_rms = rms

    def _loop_cb(self, indata, frames, time_info, status) -> None:  # type: ignore[override]
        import numpy as np

        rms = float(np.sqrt(np.mean(indata**2)))
        if rms > self._loop_rms:
            self._loop_rms = rms

    # --- public API ---

    def start(self) -> None:
        import sounddevice as sd

        try:
            self._mic_stream = sd.InputStream(
                device=self._mic_device,
                channels=1,
                dtype="float32",
                blocksize=2048,
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
                    blocksize=2048,
                    callback=self._loop_cb,
                )
                self._loop_stream.start()  # type: ignore[union-attr]
            except Exception:
                logging.debug(
                    "AudioLevelMonitor: could not open loopback monitor stream", exc_info=True
                )

        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        for stream in (self._mic_stream, self._loop_stream):
            if stream is not None:
                try:
                    stream.stop()  # type: ignore[union-attr]
                    stream.close()  # type: ignore[union-attr]
                except Exception:
                    pass
        self._mic_stream = None
        self._loop_stream = None
        self._mic_rms = 0.0
        self._loop_rms = 0.0

    def _emit_and_decay(self) -> None:
        loop_rms = self._loop_rms if self._loop_device is not None else -1.0
        self.levels.emit(self._mic_rms, loop_rms)
        # Natural decay so the meter falls during silence
        self._mic_rms *= 0.6
        self._loop_rms *= 0.6


class RecorderWorker(QObject):
    """
    Worker object running the audio/transcription pipeline in a background thread.

    Signals:
        status (str): human-readable status updates for the UI.
        done (str): emitted with the final transcription text on success.
        error (str): emitted with an error message on failure.
    """

    status = Signal(str)
    done = Signal(str, str, object)  # text, raw_transcript, paths
    error = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        cfg: Config,
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
        review_mode: bool = False,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.mode: str | None = None
        self.cancel_requested: bool = False
        self.review_mode = review_mode
        self._stop_event = threading.Event()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def set_review_mode(self, value: bool) -> None:
        self.review_mode = value

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

            if self.review_mode:
                self.cfg.defaults.copy = False
            result = pipeline.process(wav_path, duration, transcribe_mode, user_prompt)
            keep_audio = self.save_all or self.cfg.defaults.keep_audio_files
            pipeline.clean(wav_path, result["paths"], keep_audio)
            self.done.emit(result["text"], result["raw_transcript"], result["paths"])
        except Exception as e:
            logging.exception("Pipeline failed")
            self.error.emit(str(e))


class ResultDialog(QDialog):
    """
    Dialog shown in review mode, displaying the raw transcript and optionally
    the transformed text side-by-side with copy buttons.
    """

    def __init__(
        self,
        text: str,
        raw_transcript: str,
        paths: dict,  # type: ignore[type-arg]
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._has_transformation = text.strip() != raw_transcript.strip()
        self._drag_active = False
        self._drag_pos = QPoint(0, 0)

        self.setWindowTitle("SuperVoxtral — Review")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header
        header = QLabel("Review")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("color: #cfe8ff; font-size: 12pt; padding: 2px;")
        root.addWidget(header)

        if self._has_transformation:
            splitter = QSplitter(Qt.Orientation.Horizontal)

            # Left panel — raw transcript
            left_panel = QWidget()
            left_layout = QVBoxLayout(left_panel)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(4)
            raw_header = QLabel("Raw Transcript")
            raw_header.setObjectName("panel_header")
            left_layout.addWidget(raw_header)
            self._raw_edit = QTextEdit()
            self._raw_edit.setReadOnly(True)
            self._raw_edit.setPlainText(raw_transcript)
            left_layout.addWidget(self._raw_edit)
            copy_raw_btn = QPushButton("Copy Raw")
            copy_raw_btn.clicked.connect(lambda: self._copy(raw_transcript, copy_raw_btn))
            left_layout.addWidget(copy_raw_btn)
            splitter.addWidget(left_panel)

            # Right panel — transformed text
            right_panel = QWidget()
            right_layout = QVBoxLayout(right_panel)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.setSpacing(4)
            transformed_header = QLabel("Transformed")
            transformed_header.setObjectName("panel_header")
            right_layout.addWidget(transformed_header)
            self._text_edit = QTextEdit()
            self._text_edit.setReadOnly(True)
            self._text_edit.setPlainText(text)
            right_layout.addWidget(self._text_edit)
            copy_text_btn = QPushButton("Copy Transformed")
            copy_text_btn.clicked.connect(lambda: self._copy(text, copy_text_btn))
            right_layout.addWidget(copy_text_btn)
            splitter.addWidget(right_panel)

            root.addWidget(splitter, 1)
            self.resize(800, 540)
        else:
            # Single panel
            single_header = QLabel("Transcript")
            single_header.setObjectName("panel_header")
            root.addWidget(single_header)
            self._text_edit = QTextEdit()
            self._text_edit.setReadOnly(True)
            self._text_edit.setPlainText(text)
            root.addWidget(self._text_edit, 1)
            copy_btn = QPushButton("Copy Transcript")
            copy_btn.clicked.connect(lambda: self._copy(text, copy_btn))
            root.addWidget(copy_btn)
            self.resize(500, 420)

        # Optional file link
        transcript_path = paths.get("transcript") or paths.get("txt")
        if isinstance(transcript_path, Path):
            link_label = QLabel(
                f'<a href="{transcript_path.as_uri()}" style="color:#5ea8ff;">'
                f"{transcript_path}</a>"
            )
            link_label.setOpenExternalLinks(True)
            link_label.setTextFormat(Qt.TextFormat.RichText)
            link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(link_label)

        # Bottom bar
        bottom = QHBoxLayout()
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _copy(self, text: str, btn: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        original = btn.text()
        btn.setText("Copied!")
        # Pass self as context: Qt cancels the timer if the dialog is destroyed first,
        # preventing a callback into an already-deleted C++ QPushButton object.
        QTimer.singleShot(1500, self, lambda: btn.setText(original))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)


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
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._review_mode: bool = bool(
            self._settings.value(_KEY_REVIEW_MODE, False, type=bool)
        )

        # Background worker (create early for signal connections)
        self._worker = RecorderWorker(
            cfg=self.cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
        )
        self._thread = threading.Thread(target=self._worker.run, daemon=True)

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

        # Title
        title_html = (
            "<span style='color:#1e3a52'>══</span>"
            " <span style='color:#5a8fae'>SuperVoxtral</span> "
            "<span style='color:#1e3a52'>══</span>"
        )
        title_label = QLabel(title_html)
        title_label.setTextFormat(Qt.TextFormat.RichText)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # Audio level meters (real-time RMS from separate monitoring streams)
        self._mic_meter = LevelMeterWidget("MIC", self)
        layout.addWidget(self._mic_meter)
        has_loopback = bool(self.cfg.defaults.loopback_device)
        if has_loopback:
            self._loop_meter: LevelMeterWidget | None = LevelMeterWidget("LOOP", self)
            layout.addWidget(self._loop_meter)
        else:
            self._loop_meter = None

        # Audio level monitor (separate monitoring streams, independent from recording)
        self._level_monitor = AudioLevelMonitor(
            mic_device=None,
            loopback_device=self.cfg.defaults.loopback_device,
            parent=self,
        )
        self._level_monitor.levels.connect(self._on_levels)

        # Config info line: model / chat model / audio format / language
        _k = "color:#3d5a72"   # key label colour (dimmed)
        _sep = "<span style='color:#1c2e3c'> · </span>"
        model_html = (
            f"<span style='{_k}'>model:</span> "
            f"<span style='color:#6090b0'>{self.cfg.defaults.model}</span>"
        )
        chat_model_html = (
            f"<span style='{_k}'>llm:</span> "
            f"<span style='color:#508070'>{self.cfg.defaults.chat_model}</span>"
        )
        format_html = (
            f"<span style='{_k}'>audio format:</span> "
            f"<span style='color:#906840'>{self.cfg.defaults.format}</span>"
        )
        info_parts = [model_html, chat_model_html, format_html]
        if self.cfg.defaults.language:
            lang_html = (
                f"<span style='{_k}'>lang:</span> "
                f"<span style='color:#705890'>{self.cfg.defaults.language}</span>"
            )
            info_parts.append(lang_html)
        self._info_label = QLabel(_sep.join(info_parts))
        self._info_label.setObjectName("info_label")
        self._info_label.setTextFormat(Qt.TextFormat.RichText)
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

        # Status line — hidden during recording (meters handle that feedback)
        self._status_label = QLabel("")
        self._status_label.setObjectName("info_label")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        self._review_checkbox = QCheckBox("Review result")
        self._review_checkbox.setChecked(self._review_mode)
        self._review_checkbox.toggled.connect(self._on_review_mode_changed)
        layout.addWidget(self._review_checkbox, 0, Qt.AlignmentFlag.AlignCenter)
        self._review_hint = QLabel()
        self._review_hint.setObjectName("info_label")
        self._review_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_review_hint()
        layout.addWidget(self._review_hint)

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

        # Start recording and level monitoring simultaneously
        self._thread.start()
        self._level_monitor.start()
        QApplication.beep()

        # Ensure proper shutdown if user closes the window directly
        self._closing = False
        self._schedule_topmost_refresh()

    def _schedule_topmost_refresh(self) -> None:
        # Some WMs may ignore the first set; nudge it again shortly after show.
        QTimer.singleShot(50, lambda: self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True))

    def _on_levels(self, mic_rms: float, loop_rms: float) -> None:
        self._mic_meter.set_level(mic_rms)
        if self._loop_meter is not None and loop_rms >= 0.0:
            self._loop_meter.set_level(loop_rms)

    def _on_status(self, msg: str) -> None:
        # "Recording in progress..." is conveyed by the level meters; skip it
        if msg == "Recording in progress...":
            return
        self._status_label.setText(msg)

    def _on_done(self, text: str, raw_transcript: str, paths: object) -> None:
        self._status_label.setText("Done.")
        QApplication.beep()
        if self._review_mode:
            self.hide()
            dialog = ResultDialog(
                text=text,
                raw_transcript=raw_transcript,
                paths=paths if isinstance(paths, dict) else {},
                parent=None,
            )
            dialog.exec()
            self.close()
        else:
            self._close_soon()

    def _on_review_mode_changed(self, checked: bool) -> None:
        self._review_mode = checked
        self._settings.setValue(_KEY_REVIEW_MODE, checked)
        self._update_review_hint()

    def _update_review_hint(self) -> None:
        if self._review_mode:
            self._review_hint.setText("→ result shown for review, copy manually")
        else:
            self._review_hint.setText("→ result auto-copied to clipboard, window closes")

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
        self._level_monitor.stop()
        self._worker.cancel()
        super().closeEvent(event)

    def _on_mode_selected(self, mode: str) -> None:
        self._level_monitor.stop()
        for btn in self._action_buttons:
            btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("Stopping and processing...")
        self._worker.set_review_mode(self._review_mode)
        self._worker.set_mode(mode)
        self._worker.stop()

    def _on_cancel_clicked(self) -> None:
        self._level_monitor.stop()
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
    """Launch the PySide6 app with the minimal recorder window."""
    if cfg is None:
        cfg = Config.load(log_level=log_level)
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
