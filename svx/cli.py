from __future__ import annotations

import logging
import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.config import (
    PROMPT_DIR,
    RECORDINGS_DIR,
    TRANSCRIPTS_DIR,
    setup_environment,
)
from svx.core.prompt import init_default_prompt_files, resolve_prompt
from svx.core.storage import save_transcript
from svx.providers import get_provider

app = typer.Typer(help="SuperVoxtral CLI: record audio and send to transcription/chat providers.")
console = Console()


@app.command()
def record(
    provider: str = typer.Option(
        "mistral",
        "--provider",
        "-p",
        help="Provider to use (e.g., 'mistral').",
    ),
    audio_format: str = typer.Option(
        "wav",
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
    sys_prompt: str | None = typer.Option(
        None,
        "--sys-prompt",
        help="System prompt text (inline).",
    ),
    sys_prompt_file: Path | None = typer.Option(
        None,
        "--sys-prompt-file",
        help="Path to a text file containing the system prompt.",
    ),
    model: str = typer.Option(
        "voxtral-mini-latest",
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
    keep_wav: bool = typer.Option(
        True,
        "--keep-wav/--no-keep-wav",
        help="Keep the raw WAV file after conversion.",
    ),
    outfile_prefix: str | None = typer.Option(
        None,
        "--outfile-prefix",
        help="Custom output file prefix (default uses timestamp).",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
):
    """
    Record audio from the microphone and send it to the selected provider.

    Flow:
    - Records WAV until you press Enter.
    - Optionally converts to MP3/Opus.
    - Sends the file per provider rules.
    - Prints and saves the result.
    """
    # Environment and directories
    setup_environment(log_level=log_level)
    init_default_prompt_files(PROMPT_DIR)

    # Validate basic options
    if channels not in (1, 2):
        raise typer.BadParameter("channels must be 1 or 2")
    if rate <= 0:
        raise typer.BadParameter("rate must be > 0")
    if audio_format not in {"wav", "mp3", "opus"}:
        raise typer.BadParameter("--format must be one of wav|mp3|opus")

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
            if not keep_wav:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    logging.warning("Failed to remove WAV after conversion: %s", wav_path)

        # Resolve prompts (auto-detect prompt/*.txt if not provided)
        sys_file = sys_prompt_file if sys_prompt_file else (PROMPT_DIR / "system.txt")
        user_file = user_prompt_file if user_prompt_file else (PROMPT_DIR / "user.txt")

        resolved_system = resolve_prompt(sys_prompt, sys_file if sys_file.exists() else None)
        resolved_user = resolve_prompt(user_prompt, user_file if user_file.exists() else None)
        if not resolved_user:
            resolved_user = "What's in this audio?"

        # Provider handling via registry
        try:
            prov = get_provider(provider)
        except KeyError as e:
            raise typer.BadParameter(str(e))

        result = prov.transcribe(
            to_send_path,
            user_prompt=resolved_user,
            system_prompt=resolved_system,
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

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
