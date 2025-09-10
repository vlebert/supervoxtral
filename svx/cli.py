import base64
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import cast

import sounddevice as sd
import soundfile as sf
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

app = typer.Typer(help="SuperVoxtral CLI: record audio and send to transcription/chat providers.")
console = Console()

# Default directories (relative to current working directory)
ROOT_DIR = Path.cwd()
RECORDINGS_DIR = ROOT_DIR / "recordings"
TRANSCRIPTS_DIR = ROOT_DIR / "transcripts"
LOGS_DIR = ROOT_DIR / "logs"
PROMPT_DIR = ROOT_DIR / "prompt"


def setup_environment(log_level: str = "INFO") -> None:
    """Load environment variables and configure logging."""
    load_dotenv()  # load from .env if present

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOGS_DIR / "app.log", encoding="utf-8"),
        ],
    )

    # Ensure output directories exist
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    # Initialize default prompt files if missing
    for _prompt_file in (PROMPT_DIR / "system.txt", PROMPT_DIR / "user.txt"):
        try:
            if not _prompt_file.exists():
                _prompt_file.write_text("", encoding="utf-8")
        except Exception as _e:
            logging.debug("Could not initialize prompt file %s: %s", _prompt_file, _e)


def timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def detect_ffmpeg() -> str | None:
    """Return 'ffmpeg' if available on PATH, otherwise None."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return "ffmpeg"
    except Exception:
        return None


def convert_audio(input_wav: Path, fmt: str) -> Path:
    """Convert WAV to MP3 or Opus using ffmpeg. Returns output path."""
    assert fmt in {"mp3", "opus"}
    ffmpeg_bin = detect_ffmpeg()
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg (e.g., brew install ffmpeg).")

    output_path = input_wav.with_suffix(f".{fmt}")
    if fmt == "mp3":
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "3",
            str(output_path),
        ]
    else:  # opus
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_wav),
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            str(output_path),
        ]

    logging.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logging.error("ffmpeg failed: %s", proc.stderr.strip())
        raise RuntimeError(f"ffmpeg conversion failed with code {proc.returncode}")
    return output_path


def record_wav(
    output_path: Path,
    samplerate: int = 16000,
    channels: int = 1,
    device: int | str | None = None,
) -> float:
    """Record audio to a WAV file until the user presses Enter. Returns duration seconds."""
    q: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    start_time = time.time()

    def audio_callback(indata, frames, time_info, status):
        if status:
            logging.warning("SoundDevice status: %s", status)
        q.put(indata.copy())

    console.print(Panel.fit("Recording... Press Enter to stop.", title="SuperVoxtral"))

    with sf.SoundFile(
        str(output_path),
        mode="w",
        samplerate=samplerate,
        channels=channels,
        subtype="PCM_16",
    ) as wav_file:
        with sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            device=device,
            callback=audio_callback,
        ):

            def writer():
                while not stop_event.is_set():
                    try:
                        data = q.get(timeout=0.1)
                        wav_file.write(data)
                    except queue.Empty:
                        continue

            writer_thread = threading.Thread(target=writer, daemon=True)
            writer_thread.start()

            try:
                # Wait for Enter (or Ctrl+C) to stop
                Prompt.ask("Press Enter to stop", default="", show_default=False)
            except (KeyboardInterrupt, EOFError):
                pass
            finally:
                stop_event.set()
                writer_thread.join()

    duration = time.time() - start_time
    console.print(f"Stopped. Recorded {duration:.1f}s to {output_path}")
    logging.info(
        "Recorded WAV %s (%.2fs @ %d Hz, %d ch)", output_path, duration, samplerate, channels
    )
    return duration


def read_file_as_base64(path: Path) -> str:
    with open(path, "rb") as f:
        content = f.read()
    return base64.b64encode(content).decode("utf-8")


def read_text_file(path: Path) -> str:
    """Read a UTF-8 text file and return its content."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        logging.warning("Failed to read text file %s: %s", path, e)
        return ""


def resolve_prompt(inline: str | None, file_path: Path | None) -> str | None:
    """Combine file content and inline prompt (file first), separated by a blank line."""
    parts: list[str] = []
    if file_path and Path(file_path).exists():
        file_text = read_text_file(Path(file_path)).strip()
        if file_text:
            parts.append(file_text)
    if inline:
        inline_text = inline.strip()
        if inline_text:
            parts.append(inline_text)
    combined = "\n\n".join(parts).strip()
    return combined if combined else None


def mistral_chat_with_audio(
    audio_path: Path,
    user_prompt: str | None,
    system_prompt: str | None = None,
    model: str = "voxtral-mini-latest",
) -> tuple[str, dict]:
    """Send audio to Mistral Voxtral 'chat with audio' and return (text, raw_response_dict)."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is not set in environment.")

    try:
        from mistralai import Mistral
        from mistralai.models import MessagesTypedDict
    except Exception as e:
        raise RuntimeError("Failed to import 'mistralai'. Ensure it is installed.") from e

    client = Mistral(api_key=api_key)
    audio_b64 = read_file_as_base64(audio_path)

    messages: list[MessagesTypedDict] = []

    if system_prompt:
        messages.append(
            cast(
                MessagesTypedDict,
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": system_prompt},
                    ],
                },
            )
        )

    user_content: list[dict] = [
        {"type": "input_audio", "input_audio": audio_b64},
    ]
    if user_prompt:
        user_content.append({"type": "text", "text": user_prompt})

    messages.append(cast(MessagesTypedDict, {"role": "user", "content": user_content}))

    logging.info(
        "Calling Mistral model=%s with audio=%s (%s)", model, audio_path.name, audio_path.suffix
    )
    resp = client.chat.complete(model=model, messages=messages)

    # Extract text robustly
    text = ""
    try:
        # SDK returns objects with attributes; handle both dict-like and attr-like
        choice0 = resp["choices"][0] if isinstance(resp, dict) else resp.choices[0]
        message = choice0["message"] if isinstance(choice0, dict) else choice0.message
        content = message["content"] if isinstance(message, dict) else message.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Concatenate textual parts if list of segments
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text", ""))
            text = "\n".join(p for p in parts if p)
        else:
            text = str(content)
    except Exception:
        # Fallback to string conversion
        text = str(resp)

    # Normalize raw response to dict for saving
    try:
        raw_dict = resp if isinstance(resp, dict) else json.loads(resp.model_dump_json())
    except Exception:
        try:
            raw_dict = json.loads(str(resp))
        except Exception:
            raw_dict = {"raw": str(resp)}

    return text, raw_dict


def save_transcript(
    base_name: str, provider: str, text: str, raw: dict | None = None
) -> tuple[Path, Path | None]:
    """Save transcript text and optionally raw JSON. Returns (text_path, json_path_or_None)."""
    text_path = TRANSCRIPTS_DIR / f"{base_name}_{provider}.txt"
    text_path.write_text(text or "", encoding="utf-8")
    json_path = None
    if raw is not None:
        json_path = TRANSCRIPTS_DIR / f"{base_name}_{provider}.json"
        json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return text_path, json_path


@app.command()
def record(
    provider: str = typer.Option(
        "mistral",
        "--provider",
        "-p",
        help="Provider to use: 'mistral' (chat with audio) or 'whisper' (optional).",
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
        help="Language hint (used by some providers, e.g., Whisper).",
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
    setup_environment(log_level=log_level)

    if channels not in (1, 2):
        raise typer.BadParameter("channels must be 1 or 2")
    if rate <= 0:
        raise typer.BadParameter("rate must be > 0")
    if audio_format not in {"wav", "mp3", "opus"}:
        raise typer.BadParameter("--format must be one of wav|mp3|opus")

    base = outfile_prefix or f"rec_{timestamp()}"
    wav_path = RECORDINGS_DIR / f"{base}.wav"

    try:
        # Recording
        record_wav(wav_path, samplerate=rate, channels=channels, device=device)

        # Optional conversion
        to_send_path = wav_path
        if audio_format in {"mp3", "opus"}:
            to_send_path = convert_audio(wav_path, audio_format)
            if not keep_wav:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    logging.warning("Failed to remove WAV after conversion: %s", wav_path)

        # Provider handling
        provider = provider.lower().strip()
        if provider == "mistral":
            # Resolve prompts (auto-detect prompt/*.txt if not provided)
            sys_file = (
                sys_prompt_file
                if sys_prompt_file
                else (PROMPT_DIR / "system.txt" if (PROMPT_DIR / "system.txt").exists() else None)
            )
            user_file = (
                user_prompt_file
                if user_prompt_file
                else (PROMPT_DIR / "user.txt" if (PROMPT_DIR / "user.txt").exists() else None)
            )

            resolved_system = resolve_prompt(sys_prompt, sys_file)
            resolved_user = resolve_prompt(user_prompt, user_file) or "What's in this audio?"

            text, raw = mistral_chat_with_audio(
                to_send_path,
                user_prompt=resolved_user,
                system_prompt=resolved_system,
                model=model,
            )
            console.print(Panel.fit(text, title="Mistral Voxtral Response"))
            txt_path, json_path = save_transcript(base, "mistral", text, raw)
            console.print(f"Saved transcript: {txt_path}")
            if json_path:
                console.print(f"Saved raw JSON: {json_path}")
        elif provider == "whisper":
            # Placeholder for future Whisper integration
            console.print("[yellow]Whisper provider not yet implemented in this MVP.[/yellow]")
            console.print("You recorded: ", to_send_path)
            _txt, _json = save_transcript(
                base, "whisper", "TODO: Whisper transcription not implemented yet.", None
            )
        else:
            raise typer.BadParameter("Unknown provider. Use 'mistral' or 'whisper'.")

    except Exception as e:
        logging.exception("Error in record command")
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
