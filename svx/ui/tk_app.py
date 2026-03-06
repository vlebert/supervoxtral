"""
Minimal tkinter GUI for SuperVoxtral.

Pure stdlib — no PySide6/Qt required.

Behavior:
- Starts recording immediately on launch.
- Frameless always-on-top window, draggable by clicking anywhere.
- Transcribe / Prompt buttons stop recording and process.
- Cancel stops recording and discards.
- Esc triggers Cancel.
- Review mode opens a result dialog before closing.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from pathlib import Path
from tkinter import ttk
from typing import Any

import svx.core.config as config
from svx.core.config import Config
from svx.core.pipeline import RecordingPipeline
from svx.core.prompt import resolve_user_prompt

__all__ = ["RecorderWindow", "run_gui"]

# ── Palette ──────────────────────────────────────────────────────────────────
BG = "#0f1113"
FG = "#e6eef3"
FG_DIM = "#9fb8e6"
FG_DIMMER = "#3d5a72"
BTN_BG = "#1e40af"
BTN_FG = "#ffffff"
BTN_CANCEL_BG = "#7f1d1d"
ENTRY_BG = "#161b22"
BORDER = "#203040"

SEG_OFF = "#0d1a22"
SEG_LO = "#0e7490"
SEG_MID = "#a1580a"
SEG_HI = "#a01e1e"
SEG_PK_LO = "#1ea0c4"
SEG_PK_MID = "#d26e0e"
SEG_PK_HI = "#d22d2d"

# ── Persistent settings (replaces QSettings) ─────────────────────────────────
_SETTINGS_FILE = config.USER_DATA_DIR / "ui_settings.json"


def _load_settings() -> dict[str, Any]:
    try:
        return json.loads(_SETTINGS_FILE.read_text())  # type: ignore[return-value]
    except Exception:
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# ── Level meter ───────────────────────────────────────────────────────────────
class LevelMeterWidget:
    """
    Compact horizontal segmented level meter rendered on a tk.Canvas.

    Mirrors the look of the Qt version: 20 LED-style segments in
    cyan → amber → red zones with peak-hold marker.
    """

    _LABEL_W = 130
    _NUM_SEGS = 20
    _SEG_GAP = 2
    _TRACK_H = 8
    _WARN_SEG = int(_NUM_SEGS * 0.68)
    _CLIP_SEG = int(_NUM_SEGS * 0.86)

    def __init__(self, parent: tk.Misc, label: str, device_name: str = "") -> None:
        self._label = label
        self._device_name = device_name
        self._display_level: float = 0.0
        self._peak: float = 0.0
        self.canvas = tk.Canvas(parent, height=36, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=0, pady=1)
        self._decay_job: str | None = None
        self._start_decay()

    def set_level(self, rms: float) -> None:
        level = 0.0
        if rms > 1e-5:
            level = max(0.0, min(1.0, (20 * math.log10(rms) + 50) / 50))
        if level > self._display_level:
            self._display_level = level
        if self._display_level > self._peak:
            self._peak = self._display_level
        self._redraw()

    def _start_decay(self) -> None:
        self._display_level = max(0.0, self._display_level * 0.82)
        self._peak = max(0.0, self._peak - 0.018)
        self._redraw()
        self._decay_job = self.canvas.after(80, self._start_decay)

    def stop_decay(self) -> None:
        if self._decay_job:
            self.canvas.after_cancel(self._decay_job)
            self._decay_job = None

    def _seg_color(self, i: int, active: int, peak_seg: int, show_peak: bool) -> str:
        is_active = i < active
        is_peak = show_peak and i == peak_seg and not is_active
        if is_active:
            if i >= self._CLIP_SEG:
                return SEG_HI
            if i >= self._WARN_SEG:
                return SEG_MID
            return SEG_LO
        if is_peak:
            if i >= self._CLIP_SEG:
                return SEG_PK_HI
            if i >= self._WARN_SEG:
                return SEG_PK_MID
            return SEG_PK_LO
        return SEG_OFF

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 380
        h = c.winfo_height() or 36
        lw = self._LABEL_W
        mid = h // 2

        # Label text
        if self._device_name:
            c.create_text(
                lw - 4, mid // 2 + 1,
                text=self._label, anchor="e", fill="#648cac", font=("TkFixedFont", 8),
            )
            dn = self._device_name
            dev = (dn[:22] + "\u2026") if len(dn) > 23 else dn
            c.create_text(
                lw - 4, mid + mid // 2,
                text=dev, anchor="e", fill="#375269", font=("TkFixedFont", 7),
            )
        else:
            c.create_text(
                lw - 4, mid,
                text=self._label, anchor="e", fill="#648cac", font=("TkFixedFont", 8),
            )

        # Bar geometry
        bar_x = lw + 4
        bar_w = max(1, w - bar_x - 12)
        if bar_w <= 0:
            return
        bar_y = (h - self._TRACK_H) // 2
        seg_w = max(1, (bar_w - (self._NUM_SEGS - 1) * self._SEG_GAP) // self._NUM_SEGS)

        active = int(self._NUM_SEGS * self._display_level)
        peak_seg = int(self._NUM_SEGS * self._peak)
        show_peak = self._peak > 0.04 and peak_seg < self._NUM_SEGS

        for i in range(self._NUM_SEGS):
            x = bar_x + i * (seg_w + self._SEG_GAP)
            color = self._seg_color(i, active, peak_seg, show_peak)
            c.create_rectangle(x, bar_y, x + seg_w, bar_y + self._TRACK_H, fill=color, outline="")


# ── Worker classes ────────────────────────────────────────────────────────────
class RecorderWorker:
    """
    Runs the audio/transcription pipeline in a background thread.
    Events are delivered via a Queue polled by the main thread.

    Queue items are (event_name: str, payload: Any):
      "status"   → str message
      "done"     → (text, raw_transcript, paths)
      "error"    → str message
      "canceled" → None
    """

    def __init__(
        self,
        cfg: Config,
        result_queue: queue.Queue[tuple[str, Any]],
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
        level_monitor: object | None = None,
    ) -> None:
        self.cfg = cfg
        self._queue = result_queue
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.level_monitor = level_monitor
        self.mode: str | None = None
        self.review_mode: bool = False
        self.cancel_requested: bool = False
        self._force_discard: bool = False
        self._stop_event = threading.Event()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def set_review_mode(self, value: bool) -> None:
        self.review_mode = value

    def stop(self) -> None:
        self._stop_event.set()

    def cancel(self) -> None:
        self.cancel_requested = True
        self._stop_event.set()

    def cancel_discard(self) -> None:
        self._force_discard = True
        self.cancel_requested = True
        self._stop_event.set()

    def _emit(self, event: str, payload: Any = None) -> None:
        self._queue.put((event, payload))

    def _resolve_user_prompt(self, key: str) -> str:
        return resolve_user_prompt(self.cfg, None, None, self.cfg.user_prompt_dir, key=key)

    def run(self) -> None:
        try:
            pipeline = RecordingPipeline(
                cfg=self.cfg,
                user_prompt=self.user_prompt,
                user_prompt_file=self.user_prompt_file,
                save_all=self.save_all,
                outfile_prefix=self.outfile_prefix,
                progress_callback=lambda msg: self._emit("status", msg),
                level_monitor=self.level_monitor,
            )
            self._emit("status", "Recording in progress...")
            wav_path, duration = pipeline.record(self._stop_event)
            self._emit("status", "Recording finished.")

            if self.cancel_requested:
                keep_raw = (
                    False
                    if self._force_discard
                    else (self.save_all or self.cfg.defaults.keep_raw_audio)
                )
                keep_compressed = self.save_all or self.cfg.defaults.keep_compressed_audio
                pipeline.clean(
                    wav_path, {"wav": wav_path}, keep_raw=keep_raw, keep_compressed=keep_compressed
                )
                self._emit("canceled")
                return

            self._emit("status", "Processing in progress...")
            while self.mode is None:
                time.sleep(0.05)

            logging.info("RecorderWorker: selected mode/key: %s", self.mode)
            transcribe_mode = self.mode == "transcribe"
            user_prompt: str | None = None
            if not transcribe_mode:
                user_prompt = self._resolve_user_prompt(self.mode)

            if self.review_mode:
                self.cfg.defaults.copy = False
            result = pipeline.process(wav_path, duration, transcribe_mode, user_prompt)
            keep_raw = self.save_all or self.cfg.defaults.keep_raw_audio
            keep_compressed = self.save_all or self.cfg.defaults.keep_compressed_audio
            pipeline.clean(
                wav_path, result["paths"], keep_raw=keep_raw, keep_compressed=keep_compressed
            )
            self._emit("done", (result["text"], result["raw_transcript"], result["paths"]))
        except Exception as e:
            logging.exception("Pipeline failed")
            self._emit("error", str(e))


class ProcessFileWorker:
    """Worker that processes an existing audio file (no recording step)."""

    def __init__(
        self,
        cfg: Config,
        audio_path: Path,
        mode: str,
        save_all: bool,
        result_queue: queue.Queue[tuple[str, Any]],
    ) -> None:
        self.cfg = cfg
        self.audio_path = audio_path
        self.mode = mode
        self.save_all = save_all
        self._queue = result_queue

    def _emit(self, event: str, payload: Any = None) -> None:
        self._queue.put((event, payload))

    def _resolve_user_prompt(self, key: str) -> str:
        return resolve_user_prompt(self.cfg, None, None, self.cfg.user_prompt_dir, key=key)

    def run(self) -> None:
        pipeline: RecordingPipeline | None = None
        try:
            transcribe_mode = self.mode == "transcribe"
            user_prompt: str | None = None
            if not transcribe_mode:
                user_prompt = self._resolve_user_prompt(self.mode)

            pipeline = RecordingPipeline(
                cfg=self.cfg,
                save_all=self.save_all,
                progress_callback=lambda msg: self._emit("status", msg),
            )
            self._emit("status", f"Processing {self.audio_path.name}...")
            result = pipeline.process(self.audio_path, 0.0, transcribe_mode, user_prompt)
            keep_compressed = self.save_all or self.cfg.defaults.keep_compressed_audio
            pipeline.clean(
                self.audio_path, result["paths"], keep_raw=True, keep_compressed=keep_compressed
            )
            self._emit("done", (result["text"], result["raw_transcript"], result["paths"]))
        except Exception as e:
            logging.exception("File processing pipeline failed")
            if pipeline is not None:
                pipeline.clean(self.audio_path, {}, keep_raw=True, keep_compressed=False)
            self._emit("error", str(e))


# ── Result dialog ─────────────────────────────────────────────────────────────
class ResultDialog:
    """
    Modal dialog shown in review mode.
    Displays raw transcript and optionally the transformed text side-by-side.
    """

    def __init__(
        self,
        parent: tk.Misc | None,
        text: str,
        raw_transcript: str,
        paths: dict[str, Any],
    ) -> None:
        self._win = tk.Toplevel(parent)
        self._win.title("SuperVoxtral \u2014 Review")
        self._win.configure(bg=BG)
        self._win.attributes("-topmost", True)  # type: ignore[arg-type]
        self._win.grab_set()

        has_transformation = text.strip() != raw_transcript.strip()

        tk.Label(
            self._win, text="Review", bg=BG, fg=FG, font=("TkFixedFont", 12)
        ).pack(pady=(10, 6))

        if has_transformation:
            paned = ttk.PanedWindow(self._win, orient="horizontal")
            paned.pack(fill="both", expand=True, padx=8, pady=4)

            left = tk.Frame(paned, bg=BG)
            tk.Label(
                left, text="Raw Transcript", bg=BG, fg=FG_DIM, font=("TkFixedFont", 9)
            ).pack(anchor="w")
            raw_txt = tk.Text(
                left, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                font=("TkFixedFont", 10), relief="flat", wrap="word",
            )
            raw_txt.insert("1.0", raw_transcript)
            raw_txt.config(state="disabled")
            raw_txt.pack(fill="both", expand=True)
            tk.Button(
                left, text="Copy Raw", bg=BTN_BG, fg=BTN_FG, relief="flat",
                command=lambda: self._copy(raw_transcript),
            ).pack(pady=4)
            paned.add(left)

            right = tk.Frame(paned, bg=BG)
            tk.Label(
                right, text="Transformed", bg=BG, fg=FG_DIM, font=("TkFixedFont", 9)
            ).pack(anchor="w")
            xfm_txt = tk.Text(
                right, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                font=("TkFixedFont", 10), relief="flat", wrap="word",
            )
            xfm_txt.insert("1.0", text)
            xfm_txt.config(state="disabled")
            xfm_txt.pack(fill="both", expand=True)
            tk.Button(
                right, text="Copy Transformed", bg=BTN_BG, fg=BTN_FG, relief="flat",
                command=lambda: self._copy(text),
            ).pack(pady=4)
            paned.add(right)

            self._win.geometry("800x520")
        else:
            tk.Label(self._win, text="Transcript", bg=BG, fg=FG_DIM, font=("TkFixedFont", 9)).pack(
                anchor="w", padx=8
            )
            txt = tk.Text(
                self._win, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                font=("TkFixedFont", 10), relief="flat", wrap="word",
            )
            txt.insert("1.0", text)
            txt.config(state="disabled")
            txt.pack(fill="both", expand=True, padx=8, pady=4)
            tk.Button(
                self._win, text="Copy Transcript", bg=BTN_BG, fg=BTN_FG, relief="flat",
                command=lambda: self._copy(text),
            ).pack(pady=4)
            self._win.geometry("500x400")

        # Transcript file path (plain label)
        transcript_path = paths.get("transcript") or paths.get("txt")
        if isinstance(transcript_path, Path):
            tk.Label(
                self._win, text=str(transcript_path), bg=BG, fg="#5ea8ff",
                font=("TkFixedFont", 9),
            ).pack(pady=2)

        # Bottom bar
        bottom = tk.Frame(self._win, bg=BG)
        bottom.pack(fill="x", padx=8, pady=(4, 10))
        tk.Button(
            bottom, text="Close", bg=BTN_BG, fg=BTN_FG, relief="flat",
            command=self._win.destroy,
        ).pack(side="right")


    def _copy(self, text: str) -> None:
        self._win.clipboard_clear()
        self._win.clipboard_append(text)

    def wait(self) -> None:
        self._win.wait_window()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_default_input_name() -> str:
    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        return str(dev.get("name", "unknown"))
    except Exception:
        return "unknown"


def _open_directory(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ── Main window ───────────────────────────────────────────────────────────────
class RecorderWindow:
    """
    Frameless always-on-top tkinter window.

    Starts recording immediately on creation. The user clicks a mode button
    (Transcribe / prompt key) to stop and process, or Cancel to discard.
    """

    def __init__(
        self,
        root: tk.Tk,
        cfg: Config,
        user_prompt: str | None = None,
        user_prompt_file: Path | None = None,
        save_all: bool = False,
        outfile_prefix: str | None = None,
    ) -> None:
        self._root = root
        self.cfg = cfg
        self.user_prompt = user_prompt
        self.user_prompt_file = user_prompt_file
        self.save_all = save_all
        self.outfile_prefix = outfile_prefix
        self.prompt_keys = sorted(cfg.prompt.prompts.keys())

        # Persistent settings
        self._settings = _load_settings()
        _keep_raw = bool(self._settings.get("keep_raw_audio", cfg.defaults.keep_raw_audio))
        _keep_compressed = bool(
            self._settings.get("keep_compressed_audio", cfg.defaults.keep_compressed_audio)
        )
        _keep_transcripts = bool(
            self._settings.get("keep_transcript_files", cfg.defaults.keep_transcript_files)
        )
        _review_mode = bool(self._settings.get("review_mode", False))
        cfg.defaults.keep_raw_audio = _keep_raw
        cfg.defaults.keep_compressed_audio = _keep_compressed
        cfg.defaults.keep_transcript_files = _keep_transcripts

        # State
        self._review_mode = _review_mode
        self._closing = False
        self._record_start_time: float | None = None
        self._pending_file: Path | None = None
        self._elapsed_job: str | None = None
        # When True, next "canceled" event means "file loaded" not "close"
        self._discard_for_file = False

        # Queue for worker→UI communication
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        # Level monitor core (push mode, no extra audio streams)
        from svx.core.level_monitor import AudioLevelMonitor as _CoreMonitor

        self._level_core = _CoreMonitor(
            mic_device=None,
            loopback_device=cfg.defaults.loopback_device,
        )

        # Recording worker
        self._worker = RecorderWorker(
            cfg=cfg,
            result_queue=self._queue,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
            level_monitor=self._level_core,
        )
        self._worker_thread = threading.Thread(target=self._worker.run, daemon=True)

        # Window configuration
        root.title("SuperVoxtral")
        root.configure(bg=BG)
        root.attributes("-topmost", True)  # type: ignore[arg-type]
        root.minsize(380, 0)

        # Build UI widgets
        self._checkbuttons: list[tk.Checkbutton] = []
        self._build_ui()

        # Key bindings and window close handler
        root.bind("<Escape>", lambda _e: self._on_cancel_clicked())
        root.protocol("WM_DELETE_WINDOW", self._on_cancel_clicked)

        # Start pipeline, queue polling and level polling
        self._worker_thread.start()
        self._poll_queue()
        self._poll_levels()

        # Centre on screen
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = root.winfo_reqwidth()
        root.geometry(f"+{(sw - w) // 2}+{sh // 4}")
        root.bell()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = self._root
        bar_offset = self._mic_meter_offset()  # horizontal indent matching bar start

        # Title row
        title_row = tk.Frame(root, bg=BG)
        title_row.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(
            title_row, text="\u2550\u2550", bg=BG, fg="#1e3a52", font=("TkFixedFont", 11)
        ).pack(side="left")
        tk.Label(
            title_row, text=" SuperVoxtral ", bg=BG, fg="#5a8fae", font=("TkFixedFont", 11, "bold")
        ).pack(side="left")
        tk.Label(
            title_row, text="\u2550\u2550", bg=BG, fg="#1e3a52", font=("TkFixedFont", 11)
        ).pack(side="left")

        # Level meters
        mic_name = _get_default_input_name()
        self._mic_meter = LevelMeterWidget(root, "MIC", device_name=mic_name)
        self._loop_meter: LevelMeterWidget | None = None
        if self.cfg.defaults.loopback_device:
            self._loop_meter = LevelMeterWidget(
                root, "LOOP", device_name=self.cfg.defaults.loopback_device
            )

        # Info line
        info_parts = [
            f"model: {self.cfg.defaults.model}",
            f"llm: {self.cfg.defaults.chat_model}",
            f"audio: {self.cfg.defaults.format}",
        ]
        if self.cfg.defaults.language:
            info_parts.append(f"lang: {self.cfg.defaults.language}")
        info_row = tk.Frame(root, bg=BG)
        info_row.pack(fill="x", padx=(bar_offset, 10), pady=(0, 2))
        tk.Label(
            info_row, text="  \u00b7  ".join(info_parts),
            bg=BG, fg=FG_DIMMER, font=("TkFixedFont", 9),
        ).pack(side="left")

        # Checkboxes row 1: audio retention
        self._keep_raw_var = tk.BooleanVar(value=self.cfg.defaults.keep_raw_audio)
        self._keep_compressed_var = tk.BooleanVar(value=self.cfg.defaults.keep_compressed_audio)
        self._keep_transcripts_var = tk.BooleanVar(value=self.cfg.defaults.keep_transcript_files)

        chk1 = tk.Frame(root, bg=BG)
        chk1.pack(fill="x", padx=(bar_offset, 10), pady=1)
        for text, var, cmd in [
            ("Keep raw WAV", self._keep_raw_var, self._on_keep_raw_changed),
            ("Keep compressed", self._keep_compressed_var, self._on_keep_compressed_changed),
            ("Keep transcripts", self._keep_transcripts_var, self._on_keep_transcripts_changed),
        ]:
            cb = tk.Checkbutton(
                chk1, text=text, variable=var, bg=BG, fg=FG_DIM,
                selectcolor=BG, activebackground=BG, activeforeground=FG_DIM,
                command=cmd,
            )
            cb.pack(side="left", padx=(0, 8))
            self._checkbuttons.append(cb)

        # Checkboxes row 2: review + data dir
        self._review_var = tk.BooleanVar(value=self._review_mode)
        chk2 = tk.Frame(root, bg=BG)
        chk2.pack(fill="x", padx=(bar_offset, 10), pady=(1, 6))
        review_cb = tk.Checkbutton(
            chk2, text="Review result", variable=self._review_var,
            bg=BG, fg=FG_DIM, selectcolor=BG, activebackground=BG, activeforeground=FG_DIM,
            command=self._on_review_changed,
        )
        review_cb.pack(side="left")
        self._checkbuttons.append(review_cb)
        tk.Button(
            chk2, text="SuperVoxtral Directory",
            bg=BG, fg=FG_DIM, relief="flat", font=("TkFixedFont", 9),
            command=self._on_open_data_dir,
        ).pack(side="right")

        # Separator
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=10, pady=(2, 4))

        # Status label
        status_row = tk.Frame(root, bg=BG)
        status_row.pack(fill="x", padx=(bar_offset, 10), pady=(2, 8))
        self._status_label = tk.Label(
            status_row, text="", bg=BG, fg=FG, font=("TkFixedFont", 10), anchor="w",
        )
        self._status_label.pack(side="left", fill="x", expand=True)

        # Action buttons row (Transcribe, prompt keys, Cancel)
        btn_row = tk.Frame(root, bg=BG)
        btn_row.pack(fill="x", padx=10, pady=(0, 4))

        self._transcribe_btn = tk.Button(
            btn_row, text="Transcribe", bg=BTN_BG, fg=BTN_FG, relief="flat",
            font=("TkFixedFont", 10), padx=8,
            command=lambda: self._on_mode_selected("transcribe"),
        )
        self._transcribe_btn.pack(side="left", padx=(0, 4))

        self._prompt_buttons: dict[str, tk.Button] = {}
        for key in self.prompt_keys:
            btn = tk.Button(
                btn_row, text=key.capitalize(), bg=BTN_BG, fg=BTN_FG, relief="flat",
                font=("TkFixedFont", 10), padx=8,
                command=lambda k=key: self._on_mode_selected(k),  # type: ignore[misc]
            )
            btn.pack(side="left", padx=(0, 4))
            self._prompt_buttons[key] = btn

        self._cancel_btn = tk.Button(
            btn_row, text="Cancel", bg=BTN_CANCEL_BG, fg=BTN_FG, relief="flat",
            font=("TkFixedFont", 10), padx=8,
            command=self._on_cancel_clicked,
        )
        self._cancel_btn.pack(side="right")

        # "Process file..." row
        file_row = tk.Frame(root, bg=BG)
        file_row.pack(fill="x", padx=10, pady=(0, 10))
        self._process_file_btn = tk.Button(
            file_row, text="Process file...", bg=BG, fg=FG_DIM,
            relief="flat", font=("TkFixedFont", 9),
            command=self._on_process_file,
        )
        self._process_file_btn.pack(side="left")

        self._action_buttons = [self._transcribe_btn] + list(self._prompt_buttons.values())

    def _mic_meter_offset(self) -> int:
        """Horizontal padding so rows align with the bar start of the level meter."""
        return LevelMeterWidget._LABEL_W + 4 + 10  # label + gap + window padding

    # ── Queue polling (replaces Qt signals) ───────────────────────────────────

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self._queue.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        if not self._closing:
            self._root.after(50, self._poll_queue)

    def _handle_event(self, event: str, payload: Any) -> None:
        if event == "status":
            self._on_status(str(payload))
        elif event == "done":
            text, raw, paths = payload
            self._on_done(text, raw, paths)
        elif event == "error":
            self._on_error(str(payload))
        elif event == "canceled":
            if self._discard_for_file:
                self._discard_for_file = False
                self._on_recording_discarded_for_file()
            else:
                self._close_soon()

    # ── Level monitor polling ─────────────────────────────────────────────────

    def _poll_levels(self) -> None:
        mic_peak, loop_peak = self._level_core.get_and_reset_peaks()
        self._mic_meter.set_level(mic_peak)
        if self._loop_meter is not None and loop_peak >= 0.0:
            self._loop_meter.set_level(loop_peak)
        if not self._closing:
            self._root.after(50, self._poll_levels)

    # ── Status / elapsed timer ────────────────────────────────────────────────

    def _on_status(self, msg: str) -> None:
        if msg == "Recording in progress...":
            self._record_start_time = time.monotonic()
            self._update_elapsed()
        elif msg in ("Recording finished.", "Processing in progress..."):
            self._cancel_elapsed()
        self._set_status(msg)

    def _update_elapsed(self) -> None:
        if self._record_start_time is None:
            return
        elapsed = int(time.monotonic() - self._record_start_time)
        mins, secs = divmod(elapsed, 60)
        self._set_status(f"Recording in progress... {mins:02d}:{secs:02d}")
        self._elapsed_job = self._root.after(1000, self._update_elapsed)

    def _cancel_elapsed(self) -> None:
        if self._elapsed_job:
            self._root.after_cancel(self._elapsed_job)
            self._elapsed_job = None

    def _set_status(self, msg: str) -> None:
        self._status_label.config(text=f"Status: {msg}")

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_done(self, text: str, raw_transcript: str, paths: object) -> None:
        self._set_status("Done.")
        self._root.bell()
        if self._review_mode:
            self._root.withdraw()
            dlg = ResultDialog(
                parent=None,
                text=text,
                raw_transcript=raw_transcript,
                paths=paths if isinstance(paths, dict) else {},
            )
            dlg.wait()
            self._root.destroy()
        else:
            self._close_soon()

    def _on_error(self, message: str) -> None:
        self._root.bell()
        messagebox.showerror("SuperVoxtral", f"Error: {message}", parent=self._root)
        self._close_soon()

    def _on_keep_raw_changed(self) -> None:
        v = bool(self._keep_raw_var.get())
        self.cfg.defaults.keep_raw_audio = v
        self._settings["keep_raw_audio"] = v
        _save_settings(self._settings)

    def _on_keep_compressed_changed(self) -> None:
        v = bool(self._keep_compressed_var.get())
        self.cfg.defaults.keep_compressed_audio = v
        self._settings["keep_compressed_audio"] = v
        _save_settings(self._settings)

    def _on_keep_transcripts_changed(self) -> None:
        v = bool(self._keep_transcripts_var.get())
        self.cfg.defaults.keep_transcript_files = v
        self._settings["keep_transcript_files"] = v
        _save_settings(self._settings)

    def _on_review_changed(self) -> None:
        self._review_mode = bool(self._review_var.get())
        self._settings["review_mode"] = self._review_mode
        _save_settings(self._settings)

    def _on_open_data_dir(self) -> None:
        _open_directory(str(config.USER_DATA_DIR))

    def _freeze_controls(self) -> None:
        """Disable all interactive controls once processing or cancel is triggered."""
        for btn in self._action_buttons:
            btn.config(state="disabled")
        self._process_file_btn.config(state="disabled")
        self._cancel_btn.config(state="disabled")
        for cb in self._checkbuttons:
            cb.config(state="disabled")

    def _on_process_file(self) -> None:
        """Open a file dialog, discard the current recording, and load the file."""
        self._process_file_btn.config(state="disabled")
        audio_filter = [
            (
                "Audio/Video Files",
                "*.wav *.mp3 *.m4a *.ogg *.flac *.opus *.mp4 *.mov *.mkv *.avi *.webm",
            ),
            ("All Files", "*.*"),
        ]
        file_path = filedialog.askopenfilename(title="Select Audio File", filetypes=audio_filter)
        if not file_path:
            self._process_file_btn.config(state="normal")
            return

        self._pending_file = Path(file_path)
        self._cancel_elapsed()
        self._level_core.stop()
        # Signal that the next "canceled" event means "file loaded", not "close"
        self._discard_for_file = True
        self._worker.cancel_discard()

    def _on_recording_discarded_for_file(self) -> None:
        if self._pending_file is None:
            return
        self._set_status(f"{self._pending_file.name} loaded \u2014 choose action")

    def _start_file_processing(self, audio_path: Path, mode: str) -> None:
        file_worker = ProcessFileWorker(
            cfg=self.cfg,
            audio_path=audio_path,
            mode=mode,
            save_all=self.save_all,
            result_queue=self._queue,
        )
        threading.Thread(target=file_worker.run, daemon=True).start()

    def _on_mode_selected(self, mode: str) -> None:
        self._cancel_elapsed()
        self._level_core.stop()
        self._freeze_controls()
        if self._pending_file is not None:
            audio_path = self._pending_file
            self._pending_file = None
            self._set_status(f"Processing {audio_path.name}...")
            self._start_file_processing(audio_path, mode)
        else:
            self._set_status("Stopping and processing...")
            self._worker.set_review_mode(self._review_mode)
            self._worker.set_mode(mode)
            self._worker.stop()

    def _on_cancel_clicked(self) -> None:
        self._cancel_elapsed()
        self._level_core.stop()
        self._freeze_controls()
        if self._pending_file is not None:
            self._pending_file = None
            self._close_soon()
        else:
            self._set_status("Canceling...")
            self._worker.cancel()

    def _close_soon(self) -> None:
        if not self._closing:
            self._closing = True
            self._root.after(200, self._root.destroy)


# ── Entry point ───────────────────────────────────────────────────────────────
def run_gui(
    cfg: Config | None = None,
    user_prompt: str | None = None,
    user_prompt_file: Path | None = None,
    save_all: bool = False,
    outfile_prefix: str | None = None,
    log_level: str = "INFO",
) -> None:
    """Launch the tkinter app with the minimal recorder window."""
    if cfg is None:
        cfg = Config.load(log_level=log_level)
    config.setup_environment(log_level=log_level)

    root = tk.Tk()
    RecorderWindow(
        root=root,
        cfg=cfg,
        user_prompt=user_prompt,
        user_prompt_file=user_prompt_file,
        save_all=save_all,
        outfile_prefix=outfile_prefix,
    )
    root.mainloop()
