# supervoxtral

A simple Python CLI/GUI tool to record audio from your microphone, optionally convert it (WAV/MP3/Opus), and send it to transcription/chat APIs such as Mistral Voxtral (chat with audio) or OpenAI Whisper.

MVP scope:
- Manual stop only (no auto-stop on silence, for now).
- API-based transcription only (no on-device models).
- Primary provider: Mistral Voxtral using “chat with audio” (input_audio + text prompt).
- Optional provider: OpenAI Whisper for plain transcription.
- Zero-footprint defaults: No persistent directories (`recordings/`, `transcripts/`, `logs/`) created unless overridden via config or `--save-all`. Results printed to console and optionally copied to clipboard.
- Unified CLI/GUI pipeline for consistent recording, transcription, and output handling.

---

## Requirements

- Python 3.11+
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
- Dev tools:
  ```
  pip install -e ".[dev]"
  ```

---

## Configuration (API keys and prompts)

API keys and default behavior are configured only in your user configuration file (config.toml), not via environment variables.

- Location of the user config:
  - macOS: ~/Library/Application Support/SuperVoxtral/config.toml
  - Linux: ${XDG_CONFIG_HOME:-~/.config}/supervoxtral/config.toml
  - Windows: %APPDATA%/SuperVoxtral/config.toml

- Initialize your user config and user prompt file:
  ```
  svx config init
  ```
  This creates:
  - config.toml (with sensible defaults, including zero-footprint mode)
  - a user prompt file at: ~/Library/Application Support/SuperVoxtral/prompt/user.md (macOS)
    - Linux: ${XDG_CONFIG_HOME:-~/.config}/supervoxtral/prompt/user.md
    - Windows: %APPDATA%/SuperVoxtral/prompt/user.md

**Key config sections (edit `config.toml`):**
- **[defaults]**: provider (e.g., "mistral"), model, format (e.g., "opus"), language, rate, channels, device, copy (clipboard), keep_audio_files = false, keep_transcript_files = false, keep_log_files = false.
  - Zero-footprint mode (defaults): When `keep_* = false`, files are handled in OS temporary directories (auto-cleaned, no project dirs created). Set to `true` for persistence (creates `recordings/`, etc.).
- **[providers.mistral]**: api_key = "your_mistral_key_here", model (e.g., "voxtral-small-latest").
- **[prompt]**: text (inline prompt), file (path to prompt.md).
  - Resolution priority: CLI `--prompt`/`--prompt-file` > config.toml [prompt] > user.md fallback > "What's in this audio?".

**Configuration is centralized via a structured `Config` object loaded from your user configuration file (`config.toml`). CLI arguments override select values (e.g., prompt, log level), but most defaults (provider, model, keep flags) come from `config.toml`. No environment variables are used for API keys or settings.**

No `.env` or shell environment variables are used for API keys.

---

## Project layout

- `svx/` — package source
  - `core/` — audio capture, encoding, config, storage, unified recording/transcription pipeline
  - `providers/` — API providers (Mistral Voxtral, OpenAI Whisper)
  - `ui/` — GUI (Qt-based GUI)
- recordings/ — captured and converted audio files (created only if keep_audio_files = true or --save-all)
- transcripts/ — API responses (text/JSON) (created only if keep_transcript_files = true or --save-all)
- logs/ — application logs (created only if keep_log_files = true or --save-all)
- pyproject.toml — project metadata and dependencies

---

## Usage (CLI)

Make sure your virtual environment is activated and the project is installed (`pip install -e .`).

General command form:
```
svx record [OPTIONS]
```

**Unified entrypoint**: `svx record` handles both CLI and GUI modes via a centralized pipeline (`svx.core.pipeline.RecordingPipeline`). This ensures consistent behavior for recording, conversion, transcription, saving, clipboard copy, and logging across CLI and GUI.

**Zero-footprint defaults**: No directories created; outputs to console/clipboard. Use `--save-all` or config `keep_* = true` for persistence.

Note: the CLI now exposes a single recording entrypoint. Use `svx record --gui` to launch the GUI frontend. Most defaults (provider, format, model, language, rate, channels, device, keep_audio_files, copy) are configured via your user config (config.toml). The CLI only supports one-off overrides for: --prompt/--prompt-file, --log-level, --outfile-prefix, --gui, --save-all, --transcribe.

Planned MVP commands:

- Record with Mistral Voxtral (chat with audio) and a prompt (provider/format from config):
  ```
  svx record --prompt "What's in this file?"
  ```
  Tip: Outputs to console and clipboard (if copy=true in config). No files saved unless overridden.

  Persist all outputs (one-off override):
  ```
  svx record --save-all --prompt "What's in this file?"
  ```
  Creates `recordings/`, `transcripts/`, `logs/` and saves files/logs.

- Pure transcription mode with Mistral Voxtral (no prompt, dedicated endpoint):
  ```
  svx record --transcribe
  ```
  Note: Prompts are ignored in this mode. Combine with --save-all for persistence:
  ```
  svx record --transcribe --save-all
  ```

  To start the GUI frontend:
  ```
  svx record --gui
  ```
  The GUI uses the same pipeline and respects config + CLI overrides (e.g., `--gui --save-all` propagates persistence).

  The CLI defaults have been unified to favour the previous GUI defaults (e.g. `--format opus`, `--copy` enabled, and `--no-keep-audio-files` by default). The final effective values still respect the precedence: CLI explicit > user config defaults (config.toml) > built-in defaults.

### Advanced prompt management

You can provide a user prompt, either inline or via a file:

#### User prompt (inline)
```
svx record --user-prompt "Transcris puis résume ce qui est dit dans l'audio."
```

#### User prompt from file
```
svx record --user-prompt-file ~/Library/Application\ Support/SuperVoxtral/prompt/user.md
```
(Adjust the path for your OS; see “Configuration” for locations.)

#### Resolution priority (no concatenation)
Order of precedence for determining the final prompt:
1) `--user-prompt` (inline)
2) `--user-prompt-file` (explicit file)
3) `config.toml` → `[prompt].text`
4) `config.toml` → `[prompt].file`
5) User prompt file in your user config dir (`.../SuperVoxtral/prompt/user.md`)
6) Default fallback: "What's in this audio?"

Note: the file and inline prompts are not concatenated; the first non-empty source wins. Uses `Config.resolve_prompt()` for unified resolution across CLI/GUI.

If no user prompt is provided (by any of the above), it defaults to "What's in this audio?".

A single user message is sent containing the audio and (optionally) text.
  Flow:
  - Starts recording WAV immediately.
  - Press Enter (or Ctrl+C) to stop recording.
  - Converts WAV to MP3 (if `--format mp3`) or Opus (if `--format opus`).
  - Sends the audio to Mistral Voxtral as base64 input_audio plus your text prompt.
  - Prints and saves the response to `transcripts/` (if keep_transcript_files=true or --save-all).

- Record with OpenAI Whisper (optional):
  ```
  svx --provider whisper --format wav --language fr
  ```
  Flow:
  - Starts recording WAV.
  - Press Enter to stop.
  - Sends the audio to Whisper (transcription).
  - Prints and saves the transcript.

Config-driven options (set these in config.toml under [defaults]):
- rate, channels, device
- provider, model, format, language
- keep_audio_files, copy

One-off CLI overrides:
- `--outfile-prefix mynote_2025-09-09` (custom file prefix)
- `--log-level debug` (verbose logs)
- `--user-prompt` (alias: `--prompt`; user prompt text, inline)
- `--user-prompt-file` (alias: `--prompt-file`; path to user prompt markdown file in your user config dir)
- `--transcribe` (pure transcription mode, ignores prompts)

Alternative invocation (without console script):
```
python -m svx.cli record --prompt "..."
```

---

## Provider details

### Mistral Voxtral (chat with audio)
- Model: `voxtral-small-latest` by default (configurable)
- API: `mistralai` Python client
- Request structure:
  - Messages with `content` array containing:
    - `{ "type": "input_audio", "input_audio": "<base64>" }`
    - `{ "type": "text", "text": "<prompt>" }`
- Output: text content from the chat response; saved to `transcripts/`.

Recommended formats:
- MP3 or WAV work well. MP3 reduces file size and upload time.

Authentication:
- Mistral: key read from `Config` (user config at `providers.mistral.api_key`).

### OpenAI Whisper (optional)
- Plain transcription from audio file (WAV recommended).
- Not applicable yet; OpenAI provider configuration will be documented separately.

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
  - [x] CLI: recording command (manual stop)
  - [x] WAV capture (sounddevice/soundfile)
  - [x] Conversion via ffmpeg (MP3/Opus)
  - [x] Provider: Mistral Voxtral (chat with audio + prompt)
  - [ ] Provider: OpenAI Whisper (optional)
  - [x] Store outputs in `transcripts/` + logs (conditional on config/overrides)
  - [x] Unified CLI/GUI pipeline
  - [x] Centralized Config object
  - [x] Zero-footprint temp file handling

- Phase 2:
  - [ ] Minimal TUI (Textual) with a STOP button and keybinding
  - [ ] Config file and prompts directory
  - [ ] Better device selection, meter display, progress UI

---

## Troubleshooting

- “ffmpeg not found”: install via your OS package manager (see Requirements).
- “PermissionError: Microphone”: grant mic permission in OS settings.
- “401/403 from provider”: check that `providers.mistral.api_key` is set and valid in your `config.toml`.
- “API key missing”: Ensure `providers.mistral.api_key` in config.toml; Config.load() validates.
- No directories created: Normal in zero-footprint mode (keep_*=false). Use `--save-all` or edit config.toml.
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
