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

    # Load and apply user config (non-destructive to existing environment)
    user_cfg = config.load_user_config() or {}

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

    providers_section = user_cfg.get("providers") or {}
    mistral_section = providers_section.get("mistral") or {}
    mistral_key = str(mistral_section.get("api_key") or "")

    # Gather info
    user_config_file = config.USER_CONFIG_FILE
    user_prompt_file = config.USER_PROMPT_DIR / "user.md"
    project_prompt_file = config.PROMPT_DIR / "user.md"

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
    user_dir = config.USER_CONFIG_DIR
    user_prompt_dir = config.USER_PROMPT_DIR
    user_dir.mkdir(parents=True, exist_ok=True)
    user_prompt_dir.mkdir(parents=True, exist_ok=True)

    cfg_path = config.USER_CONFIG_FILE
    prompt_path = user_prompt_dir / "user.md"

    example_toml = (
        "# SuperVoxtral - User configuration\n"
        "#\n"
        "# Basics:\n"
        "# - This configuration controls the default behavior of `svx record`.\n"
        "# - The parameters below override the binary's built-in defaults.\n"
        "# - You can override a few options at runtime via the CLI:\n"
        "#     --prompt / --prompt-file (set a one-off prompt for this run)\n"
        "#     --log-level (debugging)\n"
        "#     --outfile-prefix (one-off output naming)\n"
        "#\n"
        "# Authentication:\n"
        "# - API keys are defined in provider-specific sections in this file.\n"
        "[providers.mistral]\n"
        '# api_key = ""\n\n'
        "[defaults]\n"
        '# Provider to use (currently supported: "mistral")\n'
        'provider = "mistral"\n\n'
        '# File format sent to the provider: "wav" | "mp3" | "opus"\n'
        '# Recording is always WAV; conversion is applied if "mp3" or "opus"\n'
        'format = "opus"\n\n'
        "# Model to use on the provider side (example for Mistral Voxtral)\n"
        'model = "voxtral-mini-latest"\n\n'
        "# Language hint (may help the provider)\n"
        'language = "fr"\n\n'
        "# Audio recording parameters\n"
        "rate = 16000\n"
        "channels = 1\n"
        'device = ""\n\n'
        "# Temporary audio files handling:\n"
        "# - false: delete WAV/converted files after transcription\n"
        "# - true: keep files on disk\n"
        "keep_audio_files = false\n\n"
        "# Automatically copy the transcribed text to the system clipboard\n"
        "copy = true\n\n"
        '# Log level: "DEBUG" | "INFO" | "WARNING" | "ERROR"\n'
        'log_level = "INFO"\n\n'
        "[prompt]\n"
        "# Default user prompt source:\n"
        "# - Option 1: Use a file (recommended)\n"
        f'file = "{str(prompt_path)}"\n'
        "#\n"
        "# - Option 2: Inline prompt (less recommended for long text)\n"
        '# text = "Please transcribe the audio and provide a concise summary in French."\n'
    )

    example_prompt = (
        "# SuperVoxtral user prompt\n"
        "Please transcribe the audio and provide a short summary in French.\n"
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

    # Ensure both project and user prompt locations exist
    init_default_prompt_files(PROMPT_DIR)
    # Ensure user config and prompt directories exist (create if missing).
    config.USER_PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    config.USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Load user config and apply any environment vars it defines (without overwriting existing env)
    user_config = config.load_user_config() or {}

    # Read defaults from user config (now authoritative for most runtime options)
    user_defaults: dict[str, Any] = {}
    try:
        user_defaults = user_config.get("defaults") or {}
        if not isinstance(user_defaults, dict):
            user_defaults = {}
    except Exception:
        user_defaults = {}

    # Resolve effective runtime parameters from user config with sensible fallbacks
    provider = str(user_defaults.get("provider", "mistral"))
    audio_format = str(user_defaults.get("format", "opus"))
    model = str(user_defaults.get("model", "voxtral-mini-latest"))
    language = user_defaults.get("language") or None
    try:
        rate = int(user_defaults.get("rate", 16000))
    except Exception:
        rate = 16000
    try:
        channels = int(user_defaults.get("channels", 1))
    except Exception:
        channels = 1
    device = user_defaults.get("device") or None
    keep_audio_files = bool(user_defaults.get("keep_audio_files", False))
    copy = bool(user_defaults.get("copy", True))
    if outfile_prefix is None:
        outfile_prefix = user_defaults.get("outfile_prefix") or None

    # If user_config specifies a log_level, apply it (this overrides the CLI-provided one)
    if "log_level" in user_defaults:
        configured_level = str(user_defaults["log_level"])
        logging.getLogger().setLevel(logging.getLevelName(configured_level))
        log_level = configured_level

    # Basic validation (fail fast for obvious misconfiguration)
    if channels not in (1, 2):
        raise typer.BadParameter("channels must be 1 or 2 (configured in config.toml)")
    if rate <= 0:
        raise typer.BadParameter("rate must be > 0 (configured in config.toml)")
    if audio_format not in {"wav", "mp3", "opus"}:
        raise typer.BadParameter("format must be one of wav|mp3|opus (configured in config.toml)")

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

        # Post-processing deletion policy (controlled by config.toml keep_audio_files)
        try:
            if not keep_audio_files:
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
