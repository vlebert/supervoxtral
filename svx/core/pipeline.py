from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from logging import FileHandler
from pathlib import Path
from typing import Any

import soundfile as sf

import svx.core.config as config
from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.chunking import merge_segments, merge_texts, split_wav
from svx.core.clipboard import copy_to_clipboard
from svx.core.config import Config
from svx.core.formatting import format_diarized_transcript
from svx.core.storage import save_text_file, save_transcript
from svx.providers import get_provider
from svx.providers.base import Provider, TranscriptionResult, TranscriptionSegment


class RecordingPipeline:
    """
    Centralized pipeline for recording audio, transcribing via provider, optionally
    transforming with a text LLM, saving outputs, and copying to clipboard.

    Pipeline steps:
    1. Transcription: audio -> text via dedicated transcription endpoint (always)
       - Supports diarization (speaker identification)
       - Auto-chunks long recordings (> chunk_duration) with overlap
    2. Transformation: text + prompt -> text via text-based LLM (when a prompt is provided)

    Handles temporary files when not keeping audio.
    Supports runtime overrides like save_all for keeping all files and adding log handlers.
    Optional progress_callback for status updates (e.g., for GUI).
    Supports transcribe_mode for pure transcription without prompt (step 1 only).
    Supports dual-device recording (mic + loopback) when loopback_device is configured.
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
        self._chunk_dir: Path | None = None  # temp dir for chunk files
        self._recording_base: str | None = None  # base name set during record()

    def _status(self, msg: str) -> None:
        """Emit status update via callback if provided."""
        if self.progress_callback:
            self.progress_callback(msg)
        logging.info(msg)

    def record(self, stop_event: threading.Event | None = None) -> tuple[Path, float]:
        """
        Record audio and return wav_path, duration.

        Uses dual-device recording if loopback_device is configured.

        Returns:
            tuple[Path, float]: wav_path, duration.
        """
        rate = self.cfg.defaults.rate
        channels = self.cfg.defaults.channels
        device = self.cfg.defaults.device
        audio_format = self.cfg.defaults.format
        base = self.outfile_prefix or f"rec_{timestamp()}"
        self._recording_base = base
        keep_raw = self.save_all or self.cfg.defaults.keep_raw_audio
        loopback_device = self.cfg.defaults.loopback_device

        # Validation (fail fast)
        if channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if rate <= 0:
            raise ValueError("rate must be > 0")
        if audio_format not in {"wav", "mp3", "opus"}:
            raise ValueError("format must be one of wav|mp3|opus")

        stop_for_recording = stop_event or threading.Event()

        # Determine output path
        if keep_raw:
            self.cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self.cfg.recordings_dir / f"{base}.wav"
        else:
            wav_path = Path(tempfile.mktemp(suffix=".wav"))

        # Dual-device or single-device recording
        if loopback_device:
            from svx.core.meeting_audio import find_loopback_device, record_dual_wav

            self._status("Recording (dual: mic + loopback)...")
            loop_idx = find_loopback_device(loopback_device)
            if loop_idx is None:
                raise ValueError(
                    f"Loopback device '{loopback_device}' not found. "
                    "Check your audio configuration."
                )
            duration = record_dual_wav(
                wav_path,
                mic_device=device,
                loopback_device=loop_idx,
                samplerate=rate,
                stop_event=stop_for_recording,
                mic_gain=self.cfg.defaults.mic_gain,
                loopback_gain=self.cfg.defaults.loopback_gain,
            )
        else:
            self._status("Recording...")
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
        self.cfg.defaults.keep_raw_audio = True
        self.cfg.defaults.keep_compressed_audio = True
        self.cfg.defaults.keep_transcript_files = True
        self.cfg.defaults.keep_log_files = True

        self._ensure_output_dirs()

    def _ensure_output_dirs(self) -> None:
        """Create output directories and add file logging if needed."""
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

    def _activate_save_all_for_long_recording(self, duration: float) -> None:
        """Auto-activate save_all for long recordings to protect against data loss."""
        chunk_duration = self.cfg.defaults.chunk_duration
        if duration > chunk_duration and not self.save_all:
            logging.info(
                "Long recording (%.1fs > %ds): auto-activating save_all for data protection",
                duration,
                chunk_duration,
            )
            self.save_all = True
            self.cfg.defaults.keep_raw_audio = True
            self.cfg.defaults.keep_compressed_audio = True
            self.cfg.defaults.keep_transcript_files = True
            self.cfg.defaults.keep_log_files = True
            self._ensure_output_dirs()

    def _transcribe_single(
        self,
        audio_path: Path,
        provider_name: str,
        model: str,
        language: str | None,
        diarize: bool,
        prov: Provider | None = None,
    ) -> TranscriptionResult:
        """Transcribe a single audio file. Reuses `prov` if given."""
        if prov is None:
            prov = get_provider(provider_name, cfg=self.cfg)
        return prov.transcribe(
            audio_path,
            model=model,
            language=language,
            diarize=diarize,
            timestamp_granularities=["segment"] if diarize else None,
        )

    def _transcribe_chunked(
        self,
        audio_path: Path,
        provider_name: str,
        model: str,
        language: str | None,
        diarize: bool,
        base: str,
    ) -> TranscriptionResult:
        """Split audio into chunks, transcribe each, and merge results."""
        chunk_duration = self.cfg.defaults.chunk_duration
        chunk_overlap = self.cfg.defaults.chunk_overlap
        keep_transcript = self.save_all or self.cfg.defaults.keep_transcript_files

        self._status(f"Splitting audio into {chunk_duration}s chunks...")
        chunks = split_wav(audio_path, chunk_duration=chunk_duration, overlap=chunk_overlap)
        self._chunk_dir = chunks[0].path.parent if chunks and chunks[0].path != audio_path else None

        prov = get_provider(provider_name, cfg=self.cfg)

        all_segments: list[list[TranscriptionSegment]] = []
        all_texts: list[str] = []
        all_raws: list[dict[str, Any]] = []

        for chunk in chunks:
            self._status(
                f"Transcribing chunk {chunk.index + 1}/{len(chunks)} "
                f"({chunk.start_seconds:.0f}s - {chunk.end_seconds:.0f}s)..."
            )
            result = self._transcribe_single(
                chunk.path, provider_name, model, language, diarize, prov=prov
            )
            all_texts.append(result["text"])
            all_raws.append(result["raw"])
            if "segments" in result:
                all_segments.append(result["segments"])

            # Save intermediate transcript for resilience
            if keep_transcript:
                self.cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
                save_text_file(
                    self.cfg.transcripts_dir / f"{base}_chunk{chunk.index:03d}.txt",
                    result["text"],
                )

        # Merge results
        merged_raw: dict[str, Any] = {"chunks": all_raws}
        if all_segments and len(all_segments) == len(chunks):
            merged_segs = merge_segments(chunks, all_segments)
            merged_text = merge_texts(chunks, all_texts, chunk_overlap)
            merged_result = TranscriptionResult(text=merged_text, raw=merged_raw)
            merged_result["segments"] = merged_segs
            return merged_result
        else:
            merged_text = merge_texts(chunks, all_texts, chunk_overlap)
            return TranscriptionResult(text=merged_text, raw=merged_raw)

    def process(
        self, wav_path: Path, duration: float, transcribe_mode: bool, user_prompt: str | None = None
    ) -> dict[str, Any]:
        """
        Process recorded audio: convert if needed, transcribe, optionally transform, save, copy.

        Pipeline:
        1. Transcription: audio -> text via dedicated endpoint (always)
           - With diarization if enabled
           - With auto-chunking if recording exceeds chunk_duration
        2. Transformation: text + prompt -> text via LLM (when prompt is provided)

        Args:
            wav_path: Path to the recorded WAV file.
            duration: Recording duration in seconds.
            transcribe_mode: Whether to use pure transcription mode (step 1 only).
            user_prompt: User prompt to use for transformation (None for transcribe_mode).

        Returns:
            Dict with 'text' (str), 'raw_transcript' (str), 'raw' (dict),
            'duration' (float), 'paths' (dict of Path or None).
        """
        # Resolve parameters
        provider = self.cfg.defaults.provider
        audio_format = self.cfg.defaults.format
        model = self.cfg.defaults.model
        language = self.cfg.defaults.language
        diarize = self.cfg.defaults.diarize
        chunk_duration = self.cfg.defaults.chunk_duration

        base = self._recording_base or wav_path.stem
        keep_transcript = self.save_all or self.cfg.defaults.keep_transcript_files
        copy_to_clip = self.cfg.defaults.copy

        # Resolve user prompt if not provided
        final_user_prompt = None
        if not transcribe_mode:
            if user_prompt is None:
                final_user_prompt = self.cfg.resolve_prompt(self.user_prompt, self.user_prompt_file)
            else:
                final_user_prompt = user_prompt
            self._status("Prompt mode: transcription then transformation.")
        else:
            self._status("Transcribe mode: transcription only, no prompt.")

        logging.debug(f"Applied prompt: {final_user_prompt or 'None (transcribe mode)'}")

        paths: dict[str, Path | None] = {"wav": wav_path}

        # Convert if needed
        to_send_path = wav_path
        if audio_format in {"mp3", "opus"}:
            self._status("Converting...")
            to_send_path = convert_audio(wav_path, audio_format)
            logging.info("Converted %s -> %s", wav_path, to_send_path)
            paths["converted"] = to_send_path

        # Get actual audio duration from file (fall back to wall-clock duration)
        audio_duration = self._get_audio_duration(to_send_path, fallback=duration)

        # Auto-activate save_all for long recordings (uses same duration as chunking)
        self._activate_save_all_for_long_recording(audio_duration)
        # Refresh keep_transcript after potential auto-activation
        keep_transcript = self.save_all or self.cfg.defaults.keep_transcript_files

        # Move compressed file to recordings dir if keeping it
        if audio_format in {"mp3", "opus"} and "converted" in paths:
            keep_compressed = self.save_all or self.cfg.defaults.keep_compressed_audio
            if keep_compressed and paths["converted"] is not None:
                self.cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
                final = self.cfg.recordings_dir / f"{base}.{audio_format}"
                if paths["converted"] != final:
                    shutil.move(str(paths["converted"]), final)
                    to_send_path = final
                    paths["converted"] = final

        # Step 1: Transcription (with optional chunking and diarization)
        if audio_duration > chunk_duration:
            self._status(
                f"Long recording ({audio_duration:.0f}s > {chunk_duration}s): chunking enabled."
            )
            result = self._transcribe_chunked(
                to_send_path, provider, model, language, diarize, base
            )
        else:
            self._status("Transcribing...")
            result = self._transcribe_single(to_send_path, provider, model, language, diarize)

        # Format output text
        raw_transcript: str
        if diarize and "segments" in result and result["segments"]:
            raw_transcript = format_diarized_transcript(result["segments"])
        else:
            raw_transcript = result["text"]

        # Step 2: Transformation (if prompt)
        if not transcribe_mode and final_user_prompt:
            self._status("Applying prompt...")
            chat_model = self.cfg.defaults.chat_model
            prov = get_provider(provider, cfg=self.cfg)
            chat_result = prov.chat(raw_transcript, final_user_prompt, model=chat_model)
            text = chat_result["text"]
            raw: dict[str, Any] = {
                "transcription": result["raw"],
                "transformation": chat_result["raw"],
            }
        else:
            text = raw_transcript
            raw = result["raw"]

        # Save if keeping transcripts
        if keep_transcript:
            self.cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
            txt_path, json_path = save_transcript(
                self.cfg.transcripts_dir, base, provider, text, raw
            )
            paths["txt"] = txt_path
            paths["json"] = json_path

            # Save raw transcript separately when transformation was applied
            if not transcribe_mode and final_user_prompt:
                raw_txt_path = self.cfg.transcripts_dir / f"{base}_{provider}_raw.txt"
                save_text_file(raw_txt_path, raw_transcript)
                paths["raw_txt"] = raw_txt_path
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
            "raw_transcript": raw_transcript,
            "raw": raw,
            "duration": duration,
            "paths": paths,
        }

    def _get_audio_duration(self, audio_path: Path, fallback: float = 0.0) -> float:
        """Get audio duration in seconds from file metadata, with fallback."""
        try:
            info = sf.info(str(audio_path))
            return info.frames / info.samplerate
        except Exception:
            logging.warning(
                "Could not read audio duration from %s, using fallback %.1fs",
                audio_path,
                fallback,
            )
            return fallback

    def clean(
        self,
        wav_path: Path,
        paths: dict[str, Path | None],
        keep_raw: bool,
        keep_compressed: bool,
    ) -> None:
        """
        Clean up temporary files.

        Args:
            wav_path: The original WAV path.
            paths: The paths dict from process().
            keep_raw: Whether to keep the raw WAV file.
            keep_compressed: Whether to keep the compressed audio file (mp3/opus).
        """
        if not keep_raw and wav_path.exists():
            wav_path.unlink()
            logging.info("Deleted temp WAV: %s", wav_path)

        if not keep_compressed:
            if "converted" in paths and paths["converted"] and paths["converted"] != wav_path:
                if paths["converted"].exists():
                    paths["converted"].unlink()
                    logging.info("Deleted temp converted: %s", paths["converted"])

        # Clean up chunk temp directory
        if self._chunk_dir and self._chunk_dir.exists():
            shutil.rmtree(self._chunk_dir, ignore_errors=True)
            logging.info("Deleted temp chunk dir: %s", self._chunk_dir)
            self._chunk_dir = None

        self._status("Cleanup completed.")

    def run(self, stop_event: threading.Event | None = None) -> dict[str, Any]:
        """
        Execute the full pipeline.

        Args:
            stop_event: Optional event to signal recording stop (e.g., for GUI).

        Returns:
            Dict with 'text' (str), 'raw_transcript' (str), 'raw' (dict),
            'duration' (float), 'paths' (dict of Path or None).

        Raises:
            Exception: On recording, conversion, or transcription errors.
        """
        self._setup_save_all()

        wav_path, duration = self.record(stop_event)

        if self.transcribe_mode:
            final_user_prompt = None
            self._status("Mode Transcribe activated: no prompt used.")
        else:
            final_user_prompt = self.cfg.resolve_prompt(self.user_prompt, self.user_prompt_file)

        result = self.process(wav_path, duration, self.transcribe_mode, final_user_prompt)

        keep_raw = self.save_all or self.cfg.defaults.keep_raw_audio
        keep_compressed = self.save_all or self.cfg.defaults.keep_compressed_audio
        self.clean(wav_path, result["paths"], keep_raw=keep_raw, keep_compressed=keep_compressed)

        logging.info("Pipeline finished (%.2fs)", duration)
        return result
