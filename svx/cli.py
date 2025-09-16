from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

import svx.core.config as config
from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import copy_to_clipboard
from svx.core.config import PROMPT_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, setup_environment
from svx.core.prompt import init_default_prompt_files, resolve_user_prompt
from svx.core.storage import save_transcript
from svx.providers import get_provider

app = typer.Typer(help="SuperVoxtral CLI: record audio and send to transcription/chat providers.")
console = Console()

# Config subcommands (open config dir, show effective configuration)
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

    # Load and apply user config (non-destructive to existing environment)
    user_cfg = config.load_user_config() or {}
    config.apply_user_env(user_cfg)

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

    os_mod = __import__("os")

    # Gather info
    user_config_file = config.USER_CONFIG_FILE
    user_prompt_file = config.USER_PROMPT_DIR / "user.md"
    project_prompt_file = config.PROMPT_DIR / "user.md"

    mistral_key = os_mod.environ.get("MISTRAL_API_KEY")
    openai_key = os_mod.environ.get("OPENAI_API_KEY")

    defaults_section = user_cfg.get("defaults") or {}
    prompt_section = user_cfg.get("prompt") or {}

    # Resolve prompt source (same logic as record command, but read-only)
    resolved_prompt_source = None
    resolved_prompt_excerpt = None
    # 1) config prompt.text
    if isinstance(prompt_section, dict) and prompt_section.get("text"):
        resolved_prompt_source = "user config [prompt].text"
        resolved_prompt_excerpt = str(prompt_section.get("text")).strip()
    # 2) config prompt.file
    if (
        not resolved_prompt_source
        and isinstance(prompt_section, dict)
        and prompt_section.get("file")
    ):
        try:
            p = Path(str(prompt_section.get("file"))).expanduser()
            if p.exists():
                resolved_prompt_source = f"user config file: {p}"
                resolved_prompt_excerpt = p.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    # 3) user prompt dir
    if not resolved_prompt_source and user_prompt_file.exists():
        try:
            t = user_prompt_file.read_text(encoding="utf-8").strip()
            if t:
                resolved_prompt_source = f"user prompt file: {user_prompt_file}"
                resolved_prompt_excerpt = t
        except Exception:
            pass
    # 4) project prompt
    if not resolved_prompt_source and project_prompt_file.exists():
        try:
            t = project_prompt_file.read_text(encoding="utf-8").strip()
            if t:
                resolved_prompt_source = f"project prompt file: {project_prompt_file}"
                resolved_prompt_excerpt = t
        except Exception:
            pass
    if not resolved_prompt_source:
        resolved_prompt_source = "fallback (builtin)"
        resolved_prompt_excerpt = "What's in this audio?"

    # Short excerpt
    excerpt = (resolved_prompt_excerpt or "").replace("\n", " ")[:200]
    if len(resolved_prompt_excerpt or "") > 200:
        excerpt += "..."

    # Print summary
    console.print("[bold underline]SuperVoxtral - Configuration (effective)[/bold underline]")
    console.print(
        f"[cyan]User config file:[/cyan] {user_config_file} (exists={user_config_file.exists()})"
    )
    console.print(
        f"[cyan]User prompt file:[/cyan] {user_prompt_file} (exists={user_prompt_file.exists()})"
    )
    console.print(
        f"[cyan]Project prompt file:[/cyan] {project_prompt_file} "
        f"(exists={project_prompt_file.exists()})"
    )
    console.print()
    console.print("[bold]Environment variables[/bold]")
    console.print(f"  MISTRAL_API_KEY: {_mask_secret(mistral_key)}")
    console.print(f"  OPENAI_API_KEY: {_mask_secret(openai_key)}")
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
    user_dir = config.USER_CONFIG_DIR
    user_prompt_dir = config.USER_PROMPT_DIR
    user_dir.mkdir(parents=True, exist_ok=True)
    user_prompt_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = config.USER_CONFIG_FILE
    prompt_path = user_prompt_dir / "user.md"

    example_toml = (
        "[env]\n"
        'MISTRAL_API_KEY = ""\n\n'
        "[defaults]\n"
        'provider = "mistral"\n'
        'format = "mp3"\n'
        'model = "voxtral-small-latest"\n'
        'language = "fr"\n'
        "rate = 16000\n"
        "channels = 1\n"
        'device = ""\n'
        "keep_audio_files = false\n"
        "copy = true\n"
        'log_level = "INFO"\n\n'
        "[prompt]\n"
        "# prefer the packed user prompt file in the user config dir\n"
        'file = "' + str(prompt_path) + '"\n'
    )

    example_prompt = (
        "# SuperVoxtral user prompt\\n"
        "Please transcribe the audio and provide a short summary in French.\\n"
    )

    wrote_any = False

    if not cfg_path.exists() or force:
        try:
            cfg_path.write_text(example_toml, encoding="utf-8")
            console.print(f"Wrote user config: {cfg_path}")
            wrote_any = True
        except Exception as e:
            console.print(f"Failed to write config file {cfg_path}: {e}")

    if not prompt_path.exists() or force:
        try:
            prompt_path.write_text(example_prompt, encoding="utf-8")
            console.print(f"Wrote user prompt: {prompt_path}")
            wrote_any = True
        except Exception as e:
            console.print(f"Failed to write prompt file {prompt_path}: {e}")

    if not wrote_any:
        console.print("User config already exists. Use --force to overwrite.")


@app.command()
def record(
    provider: str = typer.Option(
        "mistral",
        "--provider",
        "-p",
        help="Provider to use (e.g., 'mistral').",
    ),
    audio_format: str = typer.Option(
        "opus",
        "--format",
        "-f",
        help="Output format to send: wav|mp3|opus. Recording is always WAV, conversion optional.",
    ),
    user_prompt: str | None = typer.Option(
        None,
        "--user-prompt",
        "--prompt",
        help="User prompt text (inline).",
    ),
    user_prompt_file: Path | None = typer.Option(
        None,
        "--user-prompt-file",
        "--prompt-file",
        help="Path to a text file containing the user prompt.",
    ),
    model: str = typer.Option(
        "voxtral-small-latest",
        "--model",
        help="Model name for the provider (for Mistral Voxtral).",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Language hint (used by certain providers).",
    ),
    rate: int = typer.Option(16000, "--rate", help="Sample rate (Hz), e.g., 16000 or 32000."),
    channels: int = typer.Option(1, "--channels", help="Number of channels (1=mono, 2=stereo)."),
    device: str | None = typer.Option(
        None,
        "--device",
        help="Input device (index or name). Leave empty for default.",
    ),
    keep_audio_files: bool = typer.Option(
        False,
        "--keep-audio-files/--no-keep-audio-files",
        help="Keep all audio files (WAV and converted format).",
    ),
    outfile_prefix: str | None = typer.Option(
        None,
        "--outfile-prefix",
        help="Custom output file prefix (default uses timestamp).",
    ),
    copy: bool = typer.Option(
        True,
        "--copy/--no-copy",
        help="Copy the final transcript text to the system clipboard.",
    ),
    gui: bool = typer.Option(
        False,
        "--gui/--no-gui",
        help="Launch the GUI frontend instead of the CLI recording flow.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
):
    """
    Record audio from the microphone and send it to the selected provider.

    Use `--gui` to launch the GUI frontend. Priority for option resolution:
    1) CLI explicit > 2) defaults in user config (config.toml) >
    3) unified CLI defaults (which prefer GUI defaults).
    Flow:
    - Records WAV until you press Enter (CLI mode).
    - Optionally converts to MP3/Opus.
    - Sends the file per provider rules.
    - Prints and saves the result.
    """
    # Environment and directories
    setup_environment(log_level=log_level)

    # Ensure both project and user prompt locations exist
    init_default_prompt_files(PROMPT_DIR)
    # Ensure user config and prompt directories exist (create if missing).
    config.USER_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    config.USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load user config and apply any environment vars it defines (without overwriting existing env)
    user_config = config.load_user_config() or {}
    config.apply_user_env(user_config)

    # Allow user defaults to override the project's coded defaults, but only when the CLI
    # value still equals the coded default. This preserves CLI precedence.
    user_defaults: dict[str, Any] = {}
    try:
        user_defaults = user_config.get("defaults") or {}
        if not isinstance(user_defaults, dict):
            user_defaults = {}
    except Exception:
        user_defaults = {}

    # Only override parameters that still equal the project's default values.
    # (These literals must match the function signature defaults.)
    if provider == "mistral" and "provider" in user_defaults:
        provider = user_defaults["provider"]
    if audio_format == "wav" and "format" in user_defaults:
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
    if keep_audio_files is True and "keep_audio_files" in user_defaults:
        keep_audio_files = bool(user_defaults["keep_audio_files"])
    if outfile_prefix is None and "outfile_prefix" in user_defaults:
        outfile_prefix = user_defaults["outfile_prefix"] or None
    if copy is False and "copy" in user_defaults:
        copy = bool(user_defaults["copy"])
    if log_level == "INFO" and "log_level" in user_defaults:
        log_level = str(user_defaults["log_level"])
        # Reconfigure logging if user changed it
        logging.getLogger().setLevel(logging.getLevelName(log_level))

    # Validate basic options
    if channels not in (1, 2):
        raise typer.BadParameter("channels must be 1 or 2")
    if rate <= 0:
        raise typer.BadParameter("rate must be > 0")
    if audio_format not in {"wav", "mp3", "opus"}:
        raise typer.BadParameter("--format must be one of wav|mp3|opus")

    # If GUI requested, launch GUI with the resolved parameters and exit.
    if gui:
        from svx.ui.qt_app import run_gui

        # Map current parameters to the GUI call (do_copy maps to copy)
        run_gui(
            provider=provider,
            audio_format=audio_format,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            model=model,
            language=language,
            rate=rate,
            channels=channels,
            device=device,
            keep_audio_files=keep_audio_files,
            outfile_prefix=outfile_prefix,
            do_copy=copy,
            log_level=log_level,
        )
        return

    # Prepare paths
    base = outfile_prefix or f"rec_{timestamp()}"
    wav_path = RECORDINGS_DIR / f"{base}.wav"

    try:
        # Recording (press Enter to stop)
        stop_event = threading.Event()
        console.print(Panel.fit("Recording... Press Enter to stop.", title="SuperVoxtral"))

        def _wait_for_enter():
            try:
                Prompt.ask("Press Enter to stop", default="", show_default=False)
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                stop_event.set()

        waiter = threading.Thread(target=_wait_for_enter, daemon=True)
        waiter.start()
        duration = record_wav(
            wav_path, samplerate=rate, channels=channels, device=device, stop_event=stop_event
        )
        waiter.join()
        console.print(f"Stopped. Recorded {duration:.1f}s to {wav_path}")

        # Optional conversion
        to_send_path = wav_path
        if audio_format in {"mp3", "opus"}:
            to_send_path = convert_audio(wav_path, audio_format)
            logging.info("Converted %s -> %s", wav_path, to_send_path)

        final_user_prompt: str = resolve_user_prompt(
            user_config,
            user_prompt,
            user_prompt_file,
            config.USER_PROMPT_DIR,
            PROMPT_DIR,
        )

        # Provider handling via registry
        try:
            prov = get_provider(provider)
        except KeyError as e:
            raise typer.BadParameter(str(e))

        result = prov.transcribe(
            to_send_path,
            user_prompt=final_user_prompt,
            model=model,
            language=language,
        )

        # Output and persistence
        text = result["text"]
        raw = result["raw"]
        console.print(Panel.fit(text, title=f"{provider.capitalize()} Response"))
        txt_path, json_path = save_transcript(TRANSCRIPTS_DIR, base, provider, text, raw)
        console.print(f"Saved transcript: {txt_path}")
        if json_path:
            console.print(f"Saved raw JSON: {json_path}")

        # Optional: copy the transcript text to the system clipboard
        if copy:
            try:
                copy_to_clipboard(text)
                console.print("Transcription copied to clipboard.")
            except Exception as e:
                logging.warning("Failed to copy transcript to clipboard: %s", e)
                console.print("Warning: failed to copy transcription to clipboard.")

        # Post-processing deletion policy (only --keep-audio-files)
        try:
            if not keep_audio_files:
                # Remove WAV
                try:
                    if wav_path.exists():
                        wav_path.unlink()
                        logging.info("Removed WAV (--no-keep-audio-files): %s", wav_path)
                except Exception:
                    logging.warning("Failed to remove WAV: %s", wav_path)
                # Remove converted file if present and distinct
                if to_send_path != wav_path:
                    try:
                        if Path(to_send_path).exists():
                            Path(to_send_path).unlink()
                            logging.info(
                                "Removed converted audio (--no-keep-audio-files): %s",
                                to_send_path,
                            )
                    except Exception:
                        logging.warning("Failed to remove converted audio: %s", to_send_path)
            else:
                logging.info("Keeping audio files (--keep-audio-files)")
        except Exception:
            logging.debug("Audio file cleanup encountered a non-fatal error.", exc_info=True)

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
