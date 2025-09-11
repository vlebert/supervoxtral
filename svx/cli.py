from __future__ import annotations

import logging
import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from svx.core.audio import convert_audio, record_wav, timestamp
from svx.core.clipboard import copy_to_clipboard
from svx.core.config import (
    PROMPT_DIR,
    RECORDINGS_DIR,
    TRANSCRIPTS_DIR,
    setup_environment,
)
from svx.core.prompt import init_default_prompt_files
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
    keep_audio_files: bool = typer.Option(
        True,
        "--keep-audio-files/--no-keep-audio-files",
        help="Keep all audio files (WAV and converted format).",
    ),
    outfile_prefix: str | None = typer.Option(
        None,
        "--outfile-prefix",
        help="Custom output file prefix (default uses timestamp).",
    ),
    copy: bool = typer.Option(
        False,
        "--copy/--no-copy",
        help="Copy the final transcript text to the system clipboard.",
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
            logging.info("Converted %s -> %s", wav_path, to_send_path)

        # Resolve user prompt without concatenation:
        # Priority: inline (--user-prompt) > file (--user-prompt-file) > prompt/user.md > default
        final_user_prompt: str | None = None

        if user_prompt and user_prompt.strip():
            final_user_prompt = user_prompt.strip()
        elif user_prompt_file:
            try:
                text = Path(user_prompt_file).read_text(encoding="utf-8").strip()
                if text:
                    final_user_prompt = text
            except Exception:
                logging.warning("Failed to read user prompt file: %s", user_prompt_file)
        else:
            fallback_file = PROMPT_DIR / "user.md"
            if fallback_file.exists():
                try:
                    text = fallback_file.read_text(encoding="utf-8").strip()
                    if text:
                        final_user_prompt = text
                except Exception:
                    logging.debug(
                        "Could not read fallback prompt file %s: error ignored", fallback_file
                    )

        if not final_user_prompt:
            final_user_prompt = "What's in this audio?"

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


@app.command()
def gui(
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
        help="Target format to send: wav|mp3|opus.",
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
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    ),
):
    """
    Launch minimal GUI and start recording immediately.
    Defaults: --provider mistral --format opus --copy --no-keep-audio-files
    """
    from svx.ui.qt_app import run_gui

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


if __name__ == "__main__":
    app()
