# SuperVoxtral — Agent Guide

## Project overview
Python CLI/GUI for audio recording + transcription via APIs (Mistral Voxtral). MVP: manual stop, API-based, zero-footprint defaults (temp files, no persistent dirs unless overridden), results in `transcripts/` when persisted.

### Core Design Principles

1. **Centralized Pipeline**: All recording/transcription flows through `RecordingPipeline` (svx/core/pipeline.py) for consistency between CLI and GUI
2. **Config-driven**: Structured `Config` dataclass (svx/core/config.py) loaded from user's config.toml; CLI args override specific values
3. **Zero-footprint defaults**: Temp files auto-deleted unless `keep_*` flags or `--save-all` enabled; no project directories created by default
4. **Provider abstraction**: `Provider` protocol (svx/providers/base.py) for pluggable transcription services

### Module Structure

- **svx/cli.py**: Typer CLI entrypoint; orchestration only, delegates to Config and Pipeline
- **svx/core/**:
  - `config.py`: Config dataclasses, TOML loading, prompt resolution (supports multiple prompts via [prompt.key] sections), logging setup
  - `pipeline.py`: RecordingPipeline class - records, converts (ffmpeg), transcribes, saves conditionally, copies to clipboard
  - `audio.py`: WAV recording (sounddevice), ffmpeg detection/conversion to MP3/Opus
  - `prompt.py`: Multi-prompt resolution from config dict (key-based: "default", "test", etc.)
  - `storage.py`: Save transcripts/JSON conditionally based on keep_transcript_files
  - `clipboard.py`: Cross-platform clipboard copy
- **svx/providers/**:
  - `base.py`: Provider protocol, TranscriptionResult TypedDict, ProviderError
  - `mistral.py`: Mistral Voxtral implementation (dedicated transcription endpoint + text-based LLM chat)
  - `openai.py`: OpenAI Whisper implementation
  - `__init__.py`: Provider registry (get_provider)
- **svx/ui/**:
  - `qt_app.py`: PySide6 GUI (RecorderWindow/Worker) using Pipeline; dynamic buttons per prompt key

### Execution Flow

1. **Entry**: CLI parses args (--prompt, --save-all, --gui, --transcribe)
2. **Config Load**: Config.load() reads config.toml (supports [prompt.default], [prompt.other], etc.); `chat_model` for text LLM; API keys in [providers.mistral] or [providers.openai]
3. **Prompt Resolution**:
   - CLI: Uses "default" prompt key unless --prompt/--prompt-file overrides
   - GUI: Dynamic buttons for each [prompt.key]; "Transcribe" button bypasses prompt
   - Priority: CLI arg > config [prompt.key] > user prompt file > fallback
4. **Pipeline Execution** (RecordingPipeline) — 2-step pipeline:
   - record(): WAV recording via sounddevice, temp file if keep_audio_files=false
   - process(): Optional ffmpeg conversion, then:
     - Step 1 (Transcription): audio → text via provider.transcribe() (dedicated endpoint, always)
     - Step 2 (Transformation): text + prompt → text via provider.chat() (text LLM, only when prompt provided)
   - Uses `cfg.defaults.model` for transcription, `cfg.defaults.chat_model` for transformation
   - Conditional save_transcript (+ raw transcript file when transformation applied), clipboard copy
   - clean(): Temp file cleanup
5. **Transcribe Mode** (CLI only):
   - --transcribe flag: No prompt, step 1 only (dedicated transcription endpoint)
   - GUI: --transcribe ignored (warning); use "Transcribe" button instead
6. **Output**: CLI prints result; GUI emits via callback; temp files auto-deleted unless keep_* enabled

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
