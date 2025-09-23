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
    Record audio from the microphone and send it to the selected provider.

    This CLI accepts only a small set of runtime flags. Most defaults (provider, format,
    model, language, sample rate, channels, device,
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
    - Sends the file per provider rules.
    - Prints and saves the result.

    Note: In --transcribe mode, prompts (--user-prompt or --user-prompt-file) are ignored,
    as it uses a dedicated transcription endpoint without prompting.
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

    try:

        def progress_cb(msg: str) -> None:
            console.print(f"[bold cyan]{msg}[/bold cyan]")

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

        pipeline = RecordingPipeline(
            cfg=cfg,
            user_prompt=user_prompt,
            user_prompt_file=user_prompt_file,
            save_all=save_all,
            outfile_prefix=outfile_prefix,
            transcribe_mode=transcribe,
            progress_callback=progress_cb,
        )
        result = pipeline.run(stop_event=stop_event)
        waiter.join()

        text = result["text"]
        duration = result["duration"]
        paths = result["paths"]

        console.print(f"Recording completed in {duration:.1f}s")
        if paths.get("wav"):
            console.print(f"Audio saved to {paths['wav']}")
        else:
            console.print("Audio file (temporary) deleted after processing.")

        console.print(Panel.fit(text, title=f"{cfg.defaults.provider.capitalize()} Response"))

        if paths.get("txt"):
            console.print(f"Saved transcript: {paths['txt']}")
        if paths.get("json"):
            console.print(f"Saved raw JSON: {paths['json']}")

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
