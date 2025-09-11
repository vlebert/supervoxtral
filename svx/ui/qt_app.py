"""
Minimal PySide6 GUI for SuperVoxtral.

This module provides a tiny always-on-top window with a single "Stop" button.
Behavior:
- Starts recording immediately on launch.
- When "Stop" is pressed, stops recording, converts to desired format (default: opus),
  sends to the transcription provider (default: mistral), copies the result to clipboard,
  optionally deletes audio files, and then exits.

Dependencies:
- PySide6
- Existing SuperVoxtral core modules (audio, storage, providers, etc.)

Exports:
- RecorderWindow: the tiny window widget
- run_gui: convenience launcher to start the Qt application
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import ClipboardError, copy_to_clipboard
from svx.core.config import PROMPT_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, setup_environment
from svx.core.prompt import init_default_prompt_files
from svx.core.storage import save_transcript
from svx.providers import get_provider

__all__ = ["RecorderWindow", "run_gui"]


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
        Determine the final user prompt with the same priority as the CLI:
        inline > file > PROMPT_DIR/user.md > default fallback.
        """
        final_user_prompt: str | None = None

        if self.user_prompt and self.user_prompt.strip():
            final_user_prompt = self.user_prompt.strip()
        elif self.user_prompt_file:
            try:
                text = Path(self.user_prompt_file).read_text(encoding="utf-8").strip()
                if text:
                    final_user_prompt = text
            except Exception:
                logging.warning("Failed to read user prompt file: %s", self.user_prompt_file)
        else:
            fallback_file = PROMPT_DIR / "user.md"
            if fallback_file.exists():
                try:
                    text = fallback_file.read_text(encoding="utf-8").strip()
                    if text:
                        final_user_prompt = text
                except Exception:
                    logging.debug("Could not read fallback prompt file %s", fallback_file)

        if not final_user_prompt:
            final_user_prompt = "What's in this audio?"
        return final_user_prompt

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
        wav_path = RECORDINGS_DIR / f"{base}.wav"
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
            save_transcript(TRANSCRIPTS_DIR, base, self.provider, text, raw)

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
    Minimal always-on-top window with a single Stop button.

    Launching this window will immediately start the recording in a background thread.
    """

    def __init__(
        self,
        provider: str = "mistral",
        audio_format: str = "opus",
        model: str = "voxtral-mini-latest",
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
        setup_environment(log_level=log_level)
        init_default_prompt_files(PROMPT_DIR)

        # Window basics
        self.setWindowTitle("SuperVoxtral")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(260)

        # UI layout
        layout = QVBoxLayout(self)
        self._status_label = QLabel("Recording... Press Stop to finish")
        layout.addWidget(self._status_label)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self._stop_btn)

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

        # Start recording immediately
        self._thread.start()

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
        self._close_soon()

    def _on_error(self, message: str) -> None:
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


def run_gui(
    provider: str = "mistral",
    audio_format: str = "opus",
    model: str = "voxtral-mini-latest",
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
    app = QApplication.instance() or QApplication([])
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
