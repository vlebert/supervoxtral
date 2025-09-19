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
- TUI (planned Phase 2):
  ```
  pip install -e ".[tui]"
  ```
- Dev tools:
  ```
  pip install -e ".[dev]"
  ```

---

## Configuration (API keys)

API keys are configured only in your user configuration file (config.toml), not via environment variables.

- Location of the user config:
  - macOS: ~/Library/Application Support/SuperVoxtral/config.toml
  - Linux: ${XDG_CONFIG_HOME:-~/.config}/supervoxtral/config.toml
  - Windows: %APPDATA%/SuperVoxtral/config.toml

- Configure the Mistral key:
  ```
  [providers.mistral]
  api_key = "your_mistral_key_here"
  ```

No `.env` or shell environment variables are used for API keys.

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
svx record [OPTIONS]
```

Note: the CLI now exposes a single recording entrypoint. Use `svx record --gui` to launch the GUI frontend. Most defaults (provider, format, model, language, rate, channels, device, keep_audio_files, copy) are configured via your user config (config.toml). The CLI only supports one-off overrides for: --prompt/--prompt-file, --log-level, --outfile-prefix, and --gui.

Planned MVP commands:

- Record with Mistral Voxtral (chat with audio) and a prompt (provider/format come from config):
  ```
  svx record --prompt "What's in this file?"
  ```
  Tip: to automatically copy the final transcript to your system clipboard, set `copy = true` in your user `config.toml`.
  Example:
  ```
  svx record --prompt "What's in this file?"
  ```

  To start the GUI frontend:
  ```
  svx record --gui
  ```
  The CLI defaults have been unified to favour the previous GUI defaults (e.g. `--format opus`, `--copy` enabled, and `--no-keep-audio-files` by default). The final effective values still respect the precedence: CLI explicit > user config defaults (config.toml) > built-in defaults.

### Advanced prompt management

You can provide a user prompt, either inline or via a file:

#### User prompt (inline)
```
svx record --user-prompt "Transcris puis résume ce qui est dit dans l'audio."
```

#### User prompt from file
```
svx record --user-prompt-file prompt/user.md
```

#### No concatenation
Priority: inline (--user-prompt) > file (--user-prompt-file) > prompt/user.md (if present) > default ("What's in this audio?"). The file and inline prompts are not concatenated.

#### Auto-detection from `prompt/` directory
If no prompt options are provided, the tool will automatically use:

- `prompt/user.md` (if present and non-empty) as the user prompt

If no user prompt is provided (inline or file), it defaults to "What's in this audio?".

A single user message is sent containing the audio and (optionally) text.
  Flow:
  - Starts recording WAV immediately.
  - Press Enter (or Ctrl+C) to stop recording.
  - Converts WAV to MP3 (if `--format mp3`).
  - Sends the audio to Mistral Voxtral as base64 input_audio plus your text prompt.
  - Prints and saves the response to `transcripts/`.

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
- `--user-prompt-file` (alias: `--prompt-file`; path to user prompt markdown file, e.g., prompt/user.md)

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
- Mistral: key is read from user config at `providers.mistral.api_key` in `config.toml`.

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
  - [ ] CLI: recording command (manual stop)
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
- “401/403 from provider”: check that `providers.mistral.api_key` is set and valid in your `config.toml`.
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

## Progress checklist
```markdown
- [x] Initialiser projet (Typer CLI, config.toml)
- [x] Implémenter l'enregistrement WAV (start/stop par commande)
- [x] Ajouter conversion optionnelle via ffmpeg (MP3/Opus)
- [x] Intégrer provider Mistral Voxtral (chat with audio + prompt)
- [ ] Intégrer provider OpenAI Whisper (optionnel)
- [x] Stocker résultats dans transcripts/ + logs
- [x] Ajouter structure projet dans AGENTS.md
- [ ] Ajouter TUI Textual avec bouton STOP (phase 2)
- [ ] Préparer prompts/ pour post-processing (phase ultérieure)
- [x] Rédiger doc d'installation/usage (incl. ffmpeg)
- [x] Créer AGENTS.md court pour les agents
- [x] Ajouter gestion des prompts système et utilisateur (inline/fichier)
```
