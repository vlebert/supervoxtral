from __future__ import annotations

import logging
import threading
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

import svx.core.config as config
from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import copy_to_clipboard
from svx.core.config import (
    Config,
    ProviderConfig,
    setup_environment,
)
from svx.core.prompt import init_user_prompt_file
from svx.core.storage import save_transcript
from svx.providers import get_provider

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
    prompt_section = asdict(cfg.prompt)

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
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
):
    """
    Record audio from the microphone and send it to the selected provider.

    This CLI accepts only a small set of runtime flags. Most defaults (provider, format,
    model, language, sample rate, channels, device, file retention, copy-to-clipboard)
    must be configured in the user's `config.toml` under [defaults].

    Priority for option resolution:
    1) CLI explicit (only for --prompt/--prompt-file, --log-level, --outfile-prefix, --gui)
    2) defaults in user config (config.toml)
    3) coded CLI defaults (used when user config is absent)

    Flow:
    - Records WAV until you press Enter (CLI mode).
    - Optionally converts to MP3/Opus depending on config.
    - Sends the file per provider rules.
    - Prints and saves the result.
    """
    # Initial environment + logging according to CLI-provided log_level
    setup_environment(log_level=log_level)

    cfg = Config.load(log_level=log_level)

    # Resolve effective runtime parameters from config object
    provider = cfg.defaults.provider
    audio_format = cfg.defaults.format
    model = cfg.defaults.model
    language = cfg.defaults.language
    rate = cfg.defaults.rate
    channels = cfg.defaults.channels
    device = cfg.defaults.device
    if outfile_prefix is None:
        outfile_prefix = cfg.defaults.outfile_prefix

    # Basic validation (fail fast for obvious misconfiguration)
    if cfg.defaults.channels not in (1, 2):
        raise typer.BadParameter("channels must be 1 or 2 (configured in config.toml)")
    if cfg.defaults.rate <= 0:
        raise typer.BadParameter("rate must be > 0 (configured in config.toml)")
    if cfg.defaults.format not in {"wav", "mp3", "opus"}:
        raise typer.BadParameter("format must be one of wav|mp3|opus (configured in config.toml)")

    # If GUI requested, launch GUI with the resolved parameters and exit.
    if gui:
        from svx.ui.qt_app import run_gui

        # Pass config object to the GUI call
        run_gui(
            cfg=cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
        )
        return

    # Prepare paths
    base = outfile_prefix or f"rec_{timestamp()}"
    wav_path = cfg.recordings_dir / f"{base}.wav"

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

        final_user_prompt: str = cfg.resolve_prompt(user_prompt, user_prompt_file)

        # Provider handling via registry
        try:
            prov = get_provider(provider, cfg=cfg)
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
        txt_path, json_path = save_transcript(cfg.transcripts_dir, base, provider, text, raw)
        console.print(f"Saved transcript: {txt_path}")
        if json_path:
            console.print(f"Saved raw JSON: {json_path}")

        # Optional: copy the transcript text to the system clipboard
        if cfg.defaults.copy:
            try:
                copy_to_clipboard(text)
                console.print("Transcription copied to clipboard.")
            except Exception as e:
                logging.warning("Failed to copy transcript to clipboard: %s", e)
                console.print("Warning: failed to copy transcription to clipboard.")

        # Post-processing deletion policy (controlled by config.toml keep_audio_files)
        try:
            if not cfg.defaults.keep_audio_files:
                # Remove WAV
                try:
                    if wav_path.exists():
                        wav_path.unlink()
                        logging.info("Removed WAV (config.keep_audio_files=false): %s", wav_path)
                except Exception:
                    logging.warning("Failed to remove WAV: %s", wav_path)
                # Remove converted file if present and distinct
                if to_send_path != wav_path:
                    try:
                        if Path(to_send_path).exists():
                            Path(to_send_path).unlink()
                            logging.info(
                                "Removed converted audio (config.keep_audio_files=false): %s",
                                to_send_path,
                            )
                    except Exception:
                        logging.warning("Failed to remove converted audio: %s", to_send_path)
            else:
                logging.info("Keeping audio files (config.keep_audio_files=true)")
        except Exception:
            logging.debug("Audio file cleanup encountered a non-fatal error.", exc_info=True)

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
