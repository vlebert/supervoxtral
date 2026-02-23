# SuperVoxtral — Agent Guide

## Project overview
Python CLI/GUI for audio recording + transcription via APIs (Mistral Voxtral). MVP: manual stop, API-based, zero-footprint defaults (temp files, no persistent dirs unless overridden), results in `transcripts/` when persisted.

### Core Design Principles

1. **Centralized Pipeline**: All recording/transcription flows through `RecordingPipeline` (svx/core/pipeline.py) for consistency between CLI and GUI
2. **Config-driven**: Structured `Config` dataclass (svx/core/config.py) loaded from user's config.toml; CLI args override specific values
3. **Zero-footprint defaults**: Temp files auto-deleted unless `keep_*` flags or `--save-all` enabled; no project directories created by default. Long recordings (> chunk_duration) auto-activate save_all for data protection.
4. **Provider abstraction**: `Provider` protocol (svx/providers/base.py) for pluggable transcription services
5. **User-standard paths**: Data files (recordings, transcripts, logs) stored in platform-standard data directories (`USER_DATA_DIR`), not cwd. Config in `USER_CONFIG_DIR`.

### Module Structure

- **svx/cli.py**: Typer CLI entrypoint; orchestration only, delegates to Config and Pipeline. During recording, runs the pipeline in a background thread while the main thread drives a Rich `Live` animated panel (level meters + elapsed time + config info). The root logger's StreamHandler is temporarily replaced with a `RichHandler` tied to the same `Console` instance to prevent cursor-tracking desync.
- **svx/core/**:
  - `config.py`: Config dataclasses, TOML loading, prompt resolution (supports multiple prompts via [prompt.key] sections), logging setup. `get_user_data_dir()` / `get_user_config_dir()` for platform-standard paths. `keep_raw_audio` / `keep_compressed_audio` control WAV and compressed file retention independently.
  - `pipeline.py`: RecordingPipeline class - records (single or dual device), auto-chunks long recordings, transcribes with diarization, saves conditionally, copies to clipboard. Accepts an optional `level_monitor` (AudioLevelMonitor) and calls `push_mic`/`push_loop` from its recording callbacks.
  - `audio.py`: WAV recording (sounddevice), ffmpeg detection/conversion to MP3/Opus
  - `chunking.py`: Split long WAV files into overlapping chunks (`split_wav`), merge transcription segments (`merge_segments`) with crossfade deduplication, merge texts (`merge_texts`)
  - `meeting_audio.py`: Dual-device recording (`record_dual_wav`) — mic + loopback mixed to mono with configurable per-source gain. `find_loopback_device()` for device discovery.
  - `formatting.py`: Format diarized transcription segments with speaker labels and timestamps (`format_diarized_transcript`)
  - `level_monitor.py`: `AudioLevelMonitor` — framework-agnostic, push-based peak accumulator (no sounddevice streams). Pipeline feeds RMS values via `push_mic()`/`push_loop()` from its recording callbacks; consumers call `get_and_reset_peaks()` at their own cadence. Shared between CLI and GUI.
  - `prompt.py`: Multi-prompt resolution from config dict (key-based: "default", "test", etc.)
  - `storage.py`: Save transcripts/JSON conditionally based on keep_transcript_files
  - `clipboard.py`: Cross-platform clipboard copy
- **svx/providers/**:
  - `base.py`: Provider protocol, TranscriptionResult/TranscriptionSegment TypedDicts, ProviderError
  - `mistral.py`: Mistral Voxtral implementation (dedicated transcription endpoint + text-based LLM chat, diarization support)
  - `openai.py`: OpenAI Whisper implementation
  - `__init__.py`: Provider registry (get_provider)
- **svx/ui/**:
  - `qt_app.py`: PySide6 GUI (RecorderWindow/Worker) using Pipeline; dynamic buttons per prompt key; persistent checkboxes for `keep_raw_audio` / `keep_compressed_audio` via QSettings (override TOML without editing it). `AudioLevelMonitor` Qt adapter wraps `_CoreMonitor` (push mode) and emits `levels(mic, loop)` at 20 Hz via QTimer.

### Execution Flow

1. **Entry**: CLI parses args (--prompt, --save-all, --gui, --transcribe)
2. **Config Load**: Config.load() reads config.toml (supports [prompt.default], [prompt.other], etc.); `chat_model` for text LLM; API keys in [providers.mistral] or [providers.openai]
3. **Context Bias**: Optional `context_bias` list in `[defaults]` (up to 100 items) — passed to Mistral's transcription endpoint to improve recognition of specific vocabulary (proper nouns, technical terms). Stored in `DefaultsConfig`, read by `MistralProvider.__init__`.
4. **Prompt Resolution**:
   - CLI: Uses "default" prompt key unless --prompt/--prompt-file overrides
   - GUI: Dynamic buttons for each [prompt.key]; "Transcribe" button bypasses prompt
   - Priority: CLI arg > config [prompt.key] > user prompt file > fallback
5. **CLI Live Display** (TTY only): While the pipeline runs in a background thread, the main thread drives a Rich `Live` animated panel refreshed at 20 Hz:
   - MIC bar always shown; LOOP bar shown when `loopback_device` is configured
   - Segmented meter: cyan → amber → red zones with peak-hold marker
   - Info line: model, llm, audio format, language, elapsed MM:SS counter
   - Falls back to a static panel when stdout is not a TTY
   - `AudioLevelMonitor` (push mode) accumulates RMS values pushed by the pipeline; no extra audio streams opened
6. **Pipeline Execution** (RecordingPipeline) — 2-step pipeline:
   - record(): WAV recording via sounddevice (or dual-device via meeting_audio if `loopback_device` configured), temp file if keep_raw_audio=false
   - process(): Optional ffmpeg conversion, then:
     - Auto-chunks if audio duration > `chunk_duration` (default 300s/5min): splits with `chunk_overlap` (default 30s), transcribes each chunk, merges results
     - Step 1 (Transcription): audio → text via provider.transcribe() with `diarize=True` by default (speaker identification). Segments deduplicated across chunks via crossfade-at-midpoint.
     - Step 2 (Transformation): text + prompt → text via provider.chat() (text LLM, only when prompt provided)
     - When diarize=True and segments available: output formatted with speaker labels and timestamps via `format_diarized_transcript()`
   - Uses `cfg.defaults.model` for transcription, `cfg.defaults.chat_model` for transformation
   - Long recordings (> chunk_duration) auto-activate save_all (keep audio/transcripts/logs) for data protection
   - Conditional save_transcript (+ raw transcript file when transformation applied), clipboard copy
   - clean(): Temp file + chunk temp dir cleanup
7. **Transcribe Mode** (CLI only):
   - --transcribe flag: No prompt, step 1 only (dedicated transcription endpoint)
   - GUI: --transcribe ignored (warning); use "Transcribe" button instead
8. **Output**: CLI prints result; GUI emits via callback; temp files auto-deleted unless keep_* enabled
9. **Dual Audio Capture** (optional): When `loopback_device` is set in config, records mic + system audio via two `sd.InputStream` into a single mono WAV. Per-source gain adjustment via `mic_gain` / `loopback_gain` config (default 1.0). Requires a system loopback driver (BlackHole on macOS, native on Linux/Windows).

## Build
```bash
# Setup (creates .venv, editable install, lockfile-based)
uv sync --extra dev --extra gui
```

## Linting and Type Checking
```bash
# Lint & format
uv run ruff check svx/

# Type checker
uv run basedpyright svx
```

## Running the Application
```bash
# CLI: Record with prompt
svx record --prompt "Transcribe this audio"

# CLI: Pure transcription (no prompt)
svx record --transcribe

# GUI: Launch interactive recorder
svx record --gui

# Config management
svx config init    # Create default config.toml
svx config open    # Open config directory
svx config show    # Display current config
```

## Maintenance

- use `uv sync --extra dev --extra gui` to install/update dependencies
- after updating `pyproject.toml`, run `uv sync --extra dev --extra gui` to refresh the environment
- When adding modules: Propagate Config instance; use RecordingPipeline for recording flows; handle temp files via keep_* flags.
- Test temp cleanup: Verify no leftovers in default mode (keep_*=false).


## Code style
- **Imports**: `from __future__ import annotations` first, then stdlib, third-party, local
- **Formatting**: Black (100 line length), ruff for linting (E, F, I, UP rules)
- **Types**: Full type hints required, use `TypedDict` for data structures, `Protocol` for interfaces (e.g., Provider protocol, Config dataclasses with type hints)
- **Naming**: snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants
- **Docstrings**: Google-style with clear purpose/dependencies/`__all__` exports

## Security
- API keys are configured in the user config file (`config.toml`), under provider-specific sections.
- Mistral: define `[providers.mistral].api_key`
- Environment variables are not used for API keys.
- Validate user inputs (e.g., paths in Config, prompt resolution).
