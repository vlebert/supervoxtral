from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Callable
from logging import FileHandler
from pathlib import Path
from typing import Any

import svx.core.config as config
from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import copy_to_clipboard
from svx.core.config import Config
from svx.core.storage import save_transcript
from svx.providers import get_provider


class RecordingPipeline:
    """
    Centralized pipeline for recording audio, transcribing via provider, saving outputs,
    and copying to clipboard. Handles temporary files when not keeping audio.

    Supports runtime overrides like save_all for keeping all files and adding log handlers.
    Optional progress_callback for status updates (e.g., for GUI).
    Supports transcribe_mode for pure transcription without prompt using dedicated endpoint.
    """

    def __init__(
        self,
        cfg: Config,
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
        progress_callback: Callable[[str], None] | None = None,
        transcribe_mode: bool = False,
    ) -> None:
        self.cfg = cfg
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.progress_callback = progress_callback
        self.transcribe_mode = transcribe_mode

    def _status(self, msg: str) -> None:
        """Emit status update via callback if provided."""
        if self.progress_callback:
            self.progress_callback(msg)
        logging.info(msg)

    def record(self, stop_event: threading.Event | None = None) -> tuple[Path, float]:
        """
        Record audio and return wav_path, duration.

        Returns:
            tuple[Path, float]: wav_path, duration.
        """
        # Resolve parameters
        _provider = self.cfg.defaults.provider
        audio_format = self.cfg.defaults.format
        model = self.cfg.defaults.model
        _original_model = model
        _language = self.cfg.defaults.language
        rate = self.cfg.defaults.rate
        channels = self.cfg.defaults.channels
        device = self.cfg.defaults.device
        base = self.outfile_prefix or f"rec_{timestamp()}"
        keep_audio = self.save_all or self.cfg.defaults.keep_audio_files

        # Validation (fail fast)
        if channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if audio_format not in {"wav", "mp3", "opus"}:
            raise ValueError("format must be one of wav|mp3|opus")

        stop_for_recording = stop_event or threading.Event()

        self._status("Recording...")
        if keep_audio:
            self.cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self.cfg.recordings_dir / f"{base}.wav"
            duration = record_wav(
                wav_path,
                samplerate=rate,
                channels=channels,
                device=device,
                stop_event=stop_for_recording,
            )
        else:
            # Use mktemp for temp wav_path
            wav_path = Path(tempfile.mktemp(suffix=".wav"))
            duration = record_wav(
                wav_path,
                samplerate=rate,
                channels=channels,
                device=device,
                stop_event=stop_for_recording,
            )

        self._status("Recording completed.")
        return wav_path, duration

    def _setup_save_all(self) -> None:
        """Apply save_all overrides: set keeps to True, create dirs, add file logging."""
        if not self.save_all:
            return

        # Override config defaults
        self.cfg.defaults.keep_audio_files = True
        self.cfg.defaults.keep_transcript_files = True
        self.cfg.defaults.keep_log_files = True

        # Ensure directories
        config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        config.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

        # Add file handler if not present
        root_logger = logging.getLogger()
        if not any(isinstance(h, FileHandler) for h in root_logger.handlers):  # type: ignore[reportUnknownMemberType]
            from svx.core.config import _get_log_level

            log_level_int = _get_log_level(self.cfg.defaults.log_level)
            formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
            file_handler = logging.FileHandler(config.LOGS_DIR / "app.log", encoding="utf-8")
            file_handler.setLevel(log_level_int)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            logging.info("File logging enabled for this run")

    def process(
        self, wav_path: Path, duration: float, transcribe_mode: bool, user_prompt: str | None = None
    ) -> dict[str, Any]:
        """
        Process recorded audio: convert if needed, transcribe, save, copy.

        Args:
            wav_path: Path to the recorded WAV file.
            duration: Recording duration in seconds.
            transcribe_mode: Whether to use pure transcription mode.
            user_prompt: User prompt to use (None for transcribe_mode).

        Returns:
            Dict with 'text' (str), 'raw' (dict), 'duration' (float),
            'paths' (dict of Path or None).
        """
        # Resolve parameters
        provider = self.cfg.defaults.provider
        audio_format = self.cfg.defaults.format
        model = self.cfg.defaults.model
        original_model = model
        if transcribe_mode:
            model = "voxtral-mini-latest"
            if original_model != "voxtral-mini-latest":
                logging.warning(
                    "Transcribe mode: model override from '%s' to 'voxtral-mini-latest'\n"
                    "(optimized for transcription).",
                    original_model,
                )
        language = self.cfg.defaults.language
        if wav_path.stem.endswith(".wav"):
            base = wav_path.stem.replace(".wav", "")
        else:
            base = wav_path.stem
        keep_transcript = self.save_all or self.cfg.defaults.keep_transcript_files
        copy_to_clip = self.cfg.defaults.copy

        # Resolve user prompt if not provided
        final_user_prompt = None
        if not transcribe_mode:
            if user_prompt is None:
                final_user_prompt = self.cfg.resolve_prompt(self.user_prompt, self.user_prompt_file)
            else:
                final_user_prompt = user_prompt
            self._status("Transcribe mode not activated: using prompt.")
        else:
            self._status("Transcribe mode activated: no prompt used.")

        logging.debug(f"Applied prompt: {final_user_prompt or 'None (transcribe mode)'}")

        paths: dict[str, Path | None] = {"wav": wav_path}

        # Convert if needed
        to_send_path = wav_path
        _converted = False
        if audio_format in {"mp3", "opus"}:
            self._status("Converting...")
            to_send_path = convert_audio(wav_path, audio_format)
            logging.info("Converted %s -> %s", wav_path, to_send_path)
            paths["converted"] = to_send_path
            _converted = True

        # Transcribe
        self._status("Transcribing...")
        prov = get_provider(provider, cfg=self.cfg)
        result = prov.transcribe(
            to_send_path,
            user_prompt=final_user_prompt,
            model=model,
            language=language,
            transcribe_mode=transcribe_mode,
        )
        text = result["text"]
        raw = result["raw"]

        # Save if keeping transcripts
        if keep_transcript:
            self.cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
            txt_path, json_path = save_transcript(
                self.cfg.transcripts_dir, base, provider, text, raw
            )
            paths["txt"] = txt_path
            paths["json"] = json_path
        else:
            paths["txt"] = None
            paths["json"] = None

        # Copy to clipboard
        if copy_to_clip:
            try:
                copy_to_clipboard(text)
                logging.info("Copied transcription to clipboard")
            except Exception as e:
                logging.warning("Failed to copy to clipboard: %s", e)

        logging.info("Processing finished (%.2fs)", duration)
        return {
            "text": text,
            "raw": raw,
            "duration": duration,
            "paths": paths,
        }

    def clean(self, wav_path: Path, paths: dict[str, Path | None], keep_audio: bool) -> None:
        """
        Clean up temporary files.

        Args:
            wav_path: The original WAV path.
            paths: The paths dict from process().
            keep_audio: Whether to keep audio files (if True, no deletion).
        """
        if not keep_audio and wav_path.exists():
            wav_path.unlink()
            logging.info("Deleted temp WAV: %s", wav_path)

        if "converted" in paths and paths["converted"] and paths["converted"] != wav_path:
            if paths["converted"].exists():
                paths["converted"].unlink()
                logging.info("Deleted temp converted: %s", paths["converted"])

        self._status("Cleanup completed.")

    def run(self, stop_event: threading.Event | None = None) -> dict[str, Any]:
        """
        Execute the full pipeline.

        Args:
            stop_event: Optional event to signal recording stop (e.g., for GUI).

        Returns:
            Dict with 'text' (str), 'raw' (dict), 'duration' (float),
            'paths' (dict of Path or None).

        Raises:
            Exception: On recording, conversion, or transcription errors.
        """
        self._setup_save_all()

        wav_path, duration = self.record(stop_event)
        keep_audio = self.save_all or self.cfg.defaults.keep_audio_files

        if self.transcribe_mode:
            final_user_prompt = None
            self._status("Mode Transcribe activated: no prompt used.")
        else:
            final_user_prompt = self.cfg.resolve_prompt(self.user_prompt, self.user_prompt_file)

        result = self.process(wav_path, duration, self.transcribe_mode, final_user_prompt)

        self.clean(wav_path, result["paths"], keep_audio=keep_audio)

        logging.info("Pipeline finished (%.2fs)", duration)
        return result
