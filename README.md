# supervoxtral

A simple Python CLI/TUI tool to record audio from your microphone, optionally convert it (WAV/MP3/Opus), and send it to transcription/chat APIs such as Mistral Voxtral (chat with audio) or OpenAI Whisper.

MVP scope:
- Manual stop only (no auto-stop on silence, for now).
- API-based transcription only (no on-device models).
- Primary provider: Mistral Voxtral using “chat with audio” (input_audio + text prompt).
- Optional provider: OpenAI Whisper for plain transcription.
- Results saved to `transcripts/`, audio saved to `recordings/`.

---

## Requirements

- Python 3.10+
- macOS (primary target for now; Linux/Windows should be fine but not tested yet)
- ffmpeg (for MP3/Opus conversions)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install ffmpeg`
  - Windows: https://ffmpeg.org/download.html

---

## Installation

1) Create and activate a virtual environment (example with venv):

- macOS/Linux:
  ```
  python -m venv .venv
  source .venv/bin/activate
  ```

- Windows (PowerShell):
  ```
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```

2) Install the package (editable mode during development is convenient):
```
pip install -e .
```

Optional extras:
- OpenAI Whisper provider:
  ```
  pip install -e ".[openai]"
  ```
- TUI (planned Phase 2):
  ```
  pip install -e ".[tui]"
  ```
- Dev tools:
  ```
  pip install -e ".[dev]"
  ```

---

## Environment variables

The app expects API keys via environment variables. You can set them in your shell or place them in a `.env` file at the project root.

Supported variables:
- `MISTRAL_API_KEY` (required for Mistral Voxtral)
- `OPENAI_API_KEY` (required if you use the OpenAI provider)

Examples:

- macOS/Linux:
  ```
  export MISTRAL_API_KEY="your_mistral_key_here"
  export OPENAI_API_KEY="your_openai_key_here"
  ```

- Windows (PowerShell):
  ```
  setx MISTRAL_API_KEY "your_mistral_key_here"
  setx OPENAI_API_KEY "your_openai_key_here"
  ```

- .env file (optional, loaded via python-dotenv):
  ```
  MISTRAL_API_KEY=your_mistral_key_here
  OPENAI_API_KEY=your_openai_key_here
  ```

Note: Restart your terminal (or reload the environment) after using `setx` on Windows.

---

## Project layout

- `svx/` — package source
  - `core/` — audio capture, encoding, config, storage
  - `providers/` — API providers (Mistral Voxtral, OpenAI Whisper)
  - `ui/` — TUI (Phase 2)
- `recordings/` — captured and converted audio files
- `transcripts/` — API responses (text/JSON)
- `logs/` — application logs
- `pyproject.toml` — project metadata and dependencies

---

## Usage (CLI)

Make sure your virtual environment is activated and the project is installed (`pip install -e .`).

General command form:
```
svx <command> [OPTIONS]
```

Planned MVP commands:

- Record with Mistral Voxtral (chat with audio) and a prompt:
  ```
  svx record --provider mistral --format mp3 --prompt "What's in this file?"
  ```
  Flow:
  - Starts recording WAV immediately.
  - Press Enter (or Ctrl+C) to stop recording.
  - Converts WAV to MP3 (if `--format mp3`).
  - Sends the audio to Mistral Voxtral as base64 input_audio plus your text prompt.
  - Prints and saves the response to `transcripts/`.

- Record with OpenAI Whisper (optional):
  ```
  svx record --provider whisper --format wav --language fr
  ```
  Flow:
  - Starts recording WAV.
  - Press Enter to stop.
  - Sends the audio to Whisper (transcription).
  - Prints and saves the transcript.

Additional useful options (to be implemented as flags):
- `--rate 16000` (sample rate; default 16k or 32k)
- `--channels 1` (mono)
- `--keep-wav` (keep the raw WAV after conversion)
- `--outfile-prefix mynote_2025-09-09` (custom file prefix)
- `--log-level debug` (verbose logs)

Alternative invocation (without console script):
```
python -m svx.cli record --provider mistral --format mp3 --prompt "..."
```

---

## Provider details

### Mistral Voxtral (chat with audio)
- Model: `voxtral-mini-latest` by default (configurable)
- API: `mistralai` Python client
- Request structure:
  - Messages with `content` array containing:
    - `{ "type": "input_audio", "input_audio": "<base64>" }`
    - `{ "type": "text", "text": "<prompt>" }`
- Output: text content from the chat response; saved to `transcripts/`.

Recommended formats:
- MP3 or WAV work well. MP3 reduces file size and upload time.

Environment:
- `MISTRAL_API_KEY` required.

### OpenAI Whisper (optional)
- Plain transcription from audio file (WAV recommended).
- `OPENAI_API_KEY` required.

---

## Recording formats and conversion

- Recording happens in WAV (PCM 16-bit, mono, 16k/32k Hz).
- Optional conversion via ffmpeg:
  - WAV -> MP3:
    ```
    ffmpeg -y -i input.wav -codec:a libmp3lame -q:a 3 output.mp3
    ```
  - WAV -> Opus:
    ```
    ffmpeg -y -i input.wav -c:a libopus -b:a 64k output.opus
    ```

The tool will send the converted file if you set `--format mp3` or `--format opus`; otherwise it sends the raw WAV.

---

## macOS notes

- Microphone permission: on first run, macOS will ask for microphone access. Approve it in System Settings > Privacy & Security > Microphone if needed.
- If you face issues with device selection, we will add a `--device` flag to choose a specific input device.

---

## Roadmap

- Phase 1 (MVP):
  - [x] Project skeleton, dependencies, README
  - [ ] CLI: `record` command (manual stop)
  - [ ] WAV capture (sounddevice/soundfile)
  - [ ] Conversion via ffmpeg (MP3/Opus)
  - [ ] Provider: Mistral Voxtral (chat with audio + prompt)
  - [ ] Provider: OpenAI Whisper (optional)
  - [ ] Store outputs in `transcripts/` + logs

- Phase 2:
  - [ ] Minimal TUI (Textual) with a STOP button and keybinding
  - [ ] Config file and prompts directory
  - [ ] Better device selection, meter display, progress UI

---

## Troubleshooting

- “ffmpeg not found”: install via your OS package manager (see Requirements).
- “PermissionError: Microphone”: grant mic permission in OS settings.
- “401/403 from provider”: check that `MISTRAL_API_KEY` or `OPENAI_API_KEY` is set and valid.
- “Module not found”: ensure your venv is active and `pip install -e .` ran successfully.

---

## Development

- Code style:
  ```
  ruff check .
  black .
  ```
- Tests:
  ```
  pytest -q
  ```

---

## License

MIT