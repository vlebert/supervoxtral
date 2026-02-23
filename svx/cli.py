from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

import svx.core.config as config
from svx.core.config import (
    Config,
    ProviderConfig,
)
from svx.core.pipeline import RecordingPipeline
from svx.core.prompt import init_user_prompt_file

app = typer.Typer(help="SuperVoxtral CLI: record audio and send to transcription/chat providers.")
console = Console()

# Config subcommands (open/show user configuration)
config_app = typer.Typer(help="Config utilities (open/show user configuration)")
app.add_typer(config_app, name="config")


@config_app.command("open")
def config_open() -> None:
    """
    Open the user configuration directory in the platform's file manager.
    """
    path = config.USER_CONFIG_DIR
    if not path.exists():
        console.print(f"[yellow]User config directory does not exist:[/yellow] {path}")
        console.print("It will be created on demand when saving config or prompts.")
    try:
        typer.launch(str(path))
        console.print(f"Opened config directory: {path}")
    except Exception as e:
        console.print(f"[red]Failed to open config directory with system handler:[/red] {e}")
        console.print(f"Please open it manually: {path}")


@config_app.command("show")
def config_show() -> None:
    """
    Display the effective configuration and relevant paths.
    """
    # Ensure base environment and directories are available (but do not change user state)
    config.setup_environment(log_level="INFO")

    cfg = Config.load()

    # Helper to mask secrets for display
    def _mask_secret(val: str | None, keep: int = 4) -> str:
        try:
            if not val:
                return "(not set)"
            v = str(val)
            if len(v) <= keep * 2 + 3:
                return v[:keep] + "..." + v[-keep:]
            return v[:keep] + "..." + v[-keep:]
        except Exception:
            return "(error)"

    mistral_key = str(cfg.providers.get("mistral", ProviderConfig()).api_key or "")

    # Gather info
    user_config_file = cfg.user_config_file
    user_prompt_file = cfg.user_prompt_dir / "user.md"

    defaults_section = asdict(cfg.defaults)
    prompt_section = {k: asdict(e) for k, e in cfg.prompt.prompts.items()}

    # Resolve prompt source (same logic as record command, but read-only)
    resolved_prompt = cfg.resolve_prompt(None, None)
    resolved_prompt_source = "resolved from config"
    resolved_prompt_excerpt = resolved_prompt

    # Short excerpt
    excerpt = resolved_prompt_excerpt.replace("\n", " ")[:200]
    if len(resolved_prompt_excerpt) > 200:
        excerpt += "..."

    # Print summary
    console.print("[bold underline]SuperVoxtral - Configuration (effective)[/bold underline]")
    console.print(
        f"[cyan]User config file:[/cyan] {user_config_file} (exists={user_config_file.exists()})"
    )
    console.print(
        f"[cyan]User prompt file:[/cyan] {user_prompt_file} (exists={user_prompt_file.exists()})"
    )

    console.print()
    console.print("[bold]Provider credentials (from config.toml)[/bold]")
    console.print(f"  providers.mistral.api_key: {_mask_secret(mistral_key)}")
    console.print()
    console.print("[bold]User config sections (loaded from config.toml)[/bold]")
    console.print(f"  defaults: {defaults_section or '(none)'}")
    console.print(f"  prompt: {prompt_section or '(none)'}")
    console.print()
    console.print(f"[bold]Resolved prompt source:[/bold] {resolved_prompt_source}")
    console.print(f"[bold]Prompt excerpt:[/bold] {excerpt}")


@config_app.command("init")
def config_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files"),
) -> None:
    """
    Initialize the user configuration directory with an active config.toml and a prompt/user.md.
    Does not overwrite existing files unless --force is specified.
    """
    # Delegate initialization to core modules
    prompt_path = init_user_prompt_file(force=force)
    cfg_path = config.init_user_config(force=force, prompt_file=prompt_path)

    console.print(f"Ensured user config: {cfg_path}")
    console.print(f"Ensured user prompt: {prompt_path}")


def _make_meter_bar(display_level: float, peak: float, num_segs: int = 24) -> Text:
    """
    Build a Rich Text meter bar.

    Args:
        display_level: Current display level in [0, 1] (already log-scaled).
        peak: Peak-hold position in [0, 1].
        num_segs: Total number of bar segments.

    Returns:
        A Rich Text object with coloured block characters.
    """
    _WARN = int(num_segs * 0.68)   # amber zone starts here
    _CLIP = int(num_segs * 0.86)   # red zone starts here

    active = int(num_segs * max(0.0, min(1.0, display_level)))
    peak_seg = int(num_segs * max(0.0, min(1.0, peak)))
    show_peak = peak > 0.04 and peak_seg < num_segs

    bar = Text()
    for i in range(num_segs):
        is_active = i < active
        is_peak = show_peak and i == peak_seg and not is_active
        if is_active:
            if i >= _CLIP:
                style = "bold red"
            elif i >= _WARN:
                style = "bold yellow"
            else:
                style = "bold cyan"
            bar.append("█", style=style)
        elif is_peak:
            if peak_seg >= _CLIP:
                style = "red"
            elif peak_seg >= _WARN:
                style = "yellow"
            else:
                style = "cyan"
            bar.append("|", style=style)
        else:
            bar.append("░", style="dim")
    return bar


def _make_live_renderable(
    cfg: Config,
    mic_name: str,
    mic_lvl: float,
    mic_pk: float,
    loop_lvl: float,
    loop_pk: float,
    elapsed: float,
) -> Panel:
    """
    Build the Rich Panel displayed during live CLI recording.

    Args:
        cfg: Current Config instance (for model/format/language info).
        mic_name: Resolved mic device name (caller resolves once before the loop).
        mic_lvl: Mic display level [0, 1].
        mic_pk: Mic peak-hold [0, 1].
        loop_lvl: Loopback display level [0, 1].
        loop_pk: Loopback peak-hold [0, 1].
        elapsed: Seconds elapsed since recording started.

    Returns:
        A Rich Panel renderable.
    """
    from rich.padding import Padding

    lines = Text()

    # Mic meter row
    mic_label = Text("  MIC   ", style="color(67)")
    mic_bar = _make_meter_bar(mic_lvl, mic_pk)
    mic_row = Text.assemble(mic_label, mic_bar, Text(f"  {mic_name}", style="dim"))
    lines.append_text(mic_row)
    lines.append("\n")

    # Loopback meter row (only when configured)
    if cfg.defaults.loopback_device:
        loop_label = Text("  LOOP  ", style="color(67)")
        loop_bar = _make_meter_bar(loop_lvl, loop_pk)
        loop_row = Text.assemble(
            loop_label, loop_bar, Text(f"  {cfg.defaults.loopback_device}", style="dim")
        )
        lines.append_text(loop_row)
        lines.append("\n")

    lines.append("\n")

    # Info line: model · llm · audio [· lang]
    sep = Text(" · ", style="color(237)")
    info = Text("  ", style="")
    info.append("model: ", style="color(237)")
    info.append(cfg.defaults.model, style="color(67)")
    info.append_text(sep)
    info.append("llm: ", style="color(237)")
    info.append(cfg.defaults.chat_model, style="color(65)")
    info.append_text(sep)
    info.append("audio: ", style="color(237)")
    info.append(cfg.defaults.format, style="color(136)")
    if cfg.defaults.language:
        info.append_text(sep)
        info.append("lang: ", style="color(237)")
        info.append(cfg.defaults.language, style="color(97)")
    info.append_text(sep)
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    info.append(f"{mins:02d}:{secs:02d}", style="bold color(67)")
    lines.append_text(info)
    lines.append("\n\n")

    # Prompt line
    lines.append("  Press Enter to stop recording...", style="dim")

    return Panel(Padding(lines, (0, 1)), title="SuperVoxtral", border_style="color(237)")


def _log_scale(rms: float) -> float:
    """Map RMS [0, 1] to a log-scaled display level [0, 1] over a 50 dB range."""
    if rms < 1e-5:
        return 0.0
    return max(0.0, min(1.0, (20.0 * math.log10(rms) + 50.0) / 50.0))


def _record_with_live_display(
    cfg: Config,
    stop_event: threading.Event,
    monitor: object,
) -> None:
    """
    Show an animated Rich Live panel with audio level meters during recording.

    The monitor is fed by the recording pipeline's own callbacks (push mode) —
    no separate audio streams are opened here.

    Falls back to a static panel when stdout is not a TTY (e.g. piped output).
    Blocks until stop_event is set (user pressed Enter or stdin reached EOF).

    The caller is responsible for starting the pipeline thread before calling this
    function, and for joining it afterward. All output (logging + progress_cb) must
    be routed through the same Console instance used by this function — the caller
    should install a RichHandler on the root logger before calling this.

    Args:
        cfg: Current Config instance.
        stop_event: Event to set when the user signals stop.
        monitor: AudioLevelMonitor instance shared with the pipeline.
    """
    if not sys.stdout.isatty():
        # Non-TTY fallback: static message + background Enter-waiter thread.
        # Returns immediately; caller blocks on pipeline_thread.join().
        console.print(Panel.fit("Recording... Press Enter to stop.", title="SuperVoxtral"))

        def _wait_static() -> None:
            try:
                Prompt.ask("Press Enter to stop", default="", show_default=False)
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                stop_event.set()

        threading.Thread(target=_wait_static, daemon=True).start()
        return

    from rich.live import Live

    # Resolve mic name once — sd.query_devices is not cheap.
    try:
        import sounddevice as sd

        mic_name = str(sd.query_devices(kind="input").get("name", "default"))
    except Exception:
        mic_name = "default"

    mic_display: float = 0.0
    mic_peak: float = 0.0
    loop_display: float = 0.0
    loop_peak: float = 0.0
    start_time = time.monotonic()
    get_peaks = getattr(monitor, "get_and_reset_peaks", None)

    # Background thread: waits for Enter, then sets stop_event.
    def _wait_enter() -> None:
        try:
            sys.stdin.readline()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            stop_event.set()

    threading.Thread(target=_wait_enter, daemon=True).start()

    with Live(
        _make_live_renderable(cfg, mic_name, 0.0, 0.0, 0.0, 0.0, 0.0),
        refresh_per_second=20,
        transient=True,
        console=console,
    ) as live:
        while not stop_event.is_set():
            mic_rms, loop_rms = get_peaks() if get_peaks else (0.0, -1.0)

            mic_display = max(_log_scale(mic_rms), mic_display * 0.82)
            if mic_display > mic_peak:
                mic_peak = mic_display
            mic_peak = max(0.0, mic_peak - 0.018)

            if loop_rms >= 0.0:
                loop_display = max(_log_scale(loop_rms), loop_display * 0.82)
                if loop_display > loop_peak:
                    loop_peak = loop_display
                loop_peak = max(0.0, loop_peak - 0.018)

            elapsed = time.monotonic() - start_time
            live.update(
                _make_live_renderable(
                    cfg, mic_name, mic_display, mic_peak, loop_display, loop_peak, elapsed
                )
            )
            time.sleep(0.05)  # 20 Hz


@app.command()
def record(
    user_prompt: str | None = typer.Option(
        None,
        "--user-prompt",
        "--prompt",
        help="User prompt text (inline) to use for this run.",
    ),
    user_prompt_file: Path | None = typer.Option(
        None,
        "--user-prompt-file",
        "--prompt-file",
        help="Path to a text file containing the user prompt for this run.",
    ),
    transcribe: bool = typer.Option(
        False,
        "--transcribe",
        help="Use pure transcription mode (no prompt, dedicated endpoint).",
    ),
    outfile_prefix: str | None = typer.Option(
        None,
        "--outfile-prefix",
        help="Custom output file prefix (default uses timestamp).",
    ),
    gui: bool = typer.Option(
        False,
        "--gui/--no-gui",
        help="Launch the GUI frontend instead of the CLI recording flow.",
    ),
    save_all: bool = typer.Option(
        False,
        "--save-all",
        help="Override config to keep all files (audio, transcripts, logs) for this run.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
):
    """
    Record audio from the microphone and process it via a 2-step pipeline.

    Pipeline:
    1. Transcription: audio -> text via dedicated transcription endpoint (always).
    2. Transformation: text + prompt -> text via text-based LLM (when a prompt is provided).

    This CLI accepts only a small set of runtime flags. Most defaults (provider, format,
    model, chat_model, language, sample rate, channels, device,
    file retention, copy-to-clipboard)
    must be configured in the user's `config.toml` under [defaults].

    Priority for option resolution:
    1) CLI explicit (only for --prompt/--prompt-file, --log-level,
       --outfile-prefix, --gui, --transcribe)
    2) defaults in user config (config.toml)
    3) coded CLI defaults (used when user config is absent)

    Flow:
    - Records WAV until you press Enter (CLI mode).
    - Optionally converts to MP3/Opus depending on config.
    - Transcribes via dedicated endpoint (step 1).
    - If a prompt is provided, transforms the transcript via LLM (step 2).
    - Prints and saves the result.

    Note: In --transcribe mode, prompts (--user-prompt or --user-prompt-file) are ignored,
    and only step 1 (transcription) is performed.
    """
    cfg = Config.load(log_level=log_level)

    if transcribe and (user_prompt or user_prompt_file):
        console.print("[yellow]Transcribe mode: prompt is ignored.[/yellow]")
        user_prompt = None
        user_prompt_file = None

    if gui and transcribe:
        console.print("[yellow]Warning: --transcribe has no effect in GUI mode.[/yellow]")
        console.print("[yellow]Use the 'Transcribe' or 'Prompt' buttons in the interface.[/yellow]")
        transcribe = False

    # If GUI requested, launch GUI with the resolved parameters and exit.
    if gui:
        from svx.ui.qt_app import run_gui

        # Pass config object to the GUI call
        run_gui(
            cfg=cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
        )
        return

    # Replace the root logger's stdout StreamHandler with RichHandler so that
    # logging.info() calls from pipeline threads go through the same Console
    # instance as the Rich Live display. Without this, raw text written directly
    # to stdout by the logging StreamHandler desynchronises Live's cursor tracking
    # and produces ghost/duplicate panel lines.
    from rich.logging import RichHandler

    _root_logger = logging.getLogger()
    _old_handlers = [
        h
        for h in list(_root_logger.handlers)
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    _rich_handler = RichHandler(console=console, show_path=False, markup=False)
    # Mirror the level of the handler being replaced, not the root logger level.
    # The two can differ when config.toml's log_level overrides the CLI --log-level arg.
    _rich_handler.setLevel(_old_handlers[0].level if _old_handlers else _root_logger.level)
    for h in _old_handlers:
        _root_logger.removeHandler(h)
    _root_logger.addHandler(_rich_handler)

    try:

        def progress_cb(msg: str) -> None:
            console.print(f"[bold cyan]{msg}[/bold cyan]")

        stop_event = threading.Event()

        from typing import Any

        from svx.core.level_monitor import AudioLevelMonitor as _CoreMonitor

        # Shared monitor: pipeline pushes RMS values via its recording callbacks;
        # the live display reads them. No extra audio streams are opened.
        _monitor = _CoreMonitor(loopback_device=cfg.defaults.loopback_device)

        pipeline = RecordingPipeline(
            cfg=cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
            transcribe_mode=transcribe,
            progress_callback=progress_cb,
            level_monitor=_monitor,
        )

        # Run the pipeline in a background thread so the live display can run
        # concurrently in the foreground (mirrors the GUI's RecorderWorker pattern).
        _pipeline_result: list[dict[str, Any]] = []
        _pipeline_error: list[BaseException] = []

        def _run_pipeline() -> None:
            try:
                _pipeline_result.append(pipeline.run(stop_event=stop_event))
            except BaseException as exc:  # noqa: BLE001
                _pipeline_error.append(exc)

        _pipeline_thread = threading.Thread(target=_run_pipeline, daemon=True)
        _pipeline_thread.start()

        # Show animated live display (TTY) or static panel (non-TTY).
        # Blocks until stop_event is set (user pressed Enter).
        _record_with_live_display(cfg, stop_event, _monitor)

        # Wait for pipeline to finish processing after recording stopped.
        _pipeline_thread.join()

        if _pipeline_error:
            raise _pipeline_error[0]

        result = _pipeline_result[0]

        text = result["text"]
        duration = result["duration"]
        paths = result["paths"]

        console.print(f"Recording completed in {duration:.1f}s")
        logging.debug("Audio path: %s", paths.get("wav"))

        console.print(Panel.fit(text, title=f"{cfg.defaults.provider.capitalize()} Response"))

        if paths.get("wav") and paths["wav"].exists():
            console.print(f"Saved audio: {paths['wav']}")
        if paths.get("converted") and paths["converted"].exists():
            console.print(f"Saved audio: {paths['converted']}")
        if paths.get("txt"):
            console.print(f"Saved transcript: {paths['txt']}")
        if paths.get("json"):
            console.print(f"Saved raw JSON: {paths['json']}")
        if cfg.defaults.copy:
            console.print("[green]Copied to clipboard.[/green]")

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    finally:
        # Restore original stdout handlers (removed in favour of RichHandler above).
        _root_logger.removeHandler(_rich_handler)
        for h in _old_handlers:
            _root_logger.addHandler(h)


if __name__ == "__main__":
    app()
