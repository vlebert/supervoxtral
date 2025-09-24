# SuperVoxtral — Agent Guide

## Project overview
Python CLI/GUI for audio recording + transcription via APIs (Mistral Voxtral). MVP: manual stop, API-based, zero-footprint defaults (temp files, no persistent dirs unless overridden), results in `transcripts/` when persisted.

### Project structure
```
supervoxtral/
├── svx/                           # Python package
│   ├── __init__.py
│   ├── cli.py                     # Typer CLI entrypoint (orchestration only, uses Config and Pipeline)
│   ├── core/                      # Core logic (audio, config, prompts, storage)
│   │   ├── audio.py               # Recording, ffmpeg detection/conversion
│   │   ├── config.py              # Structured Config dataclasses, loading, resolution, logging setup
│   │   ├── pipeline.py            # Centralized RecordingPipeline for CLI/GUI unification
│   │   ├── prompt.py              # Prompt resolution (supports multiple prompts via Config dict, key-based)
│   │   └── storage.py             # Save transcripts and raw JSON (conditional on keep_transcript_files)
│   ├── providers/                 # API integrations
│   │   ├── __init__.py            # Provider registry (get_provider with Config support)
│   │   ├── base.py                # Provider protocol + shared types
│   │   └── mistral.py             # Mistral Voxtral implementation (init from Config)
│   └── ui/                        # GUI (Qt-based MVP)
│       └── qt_app.py              # RecorderWindow/Worker using Pipeline and Config

├── recordings/                    # Audio files (WAV/MP3/Opus) (conditional)
├── transcripts/                   # API responses (txt/json) (conditional)
├── logs/                          # Application logs (conditional)
├── pyproject.toml                 # Project metadata & deps
├── .env.example                   # Template for secrets (unused; keys in config.toml)
└── README.md                      # User guide
```

## Typical Execution Flow

- **Entry**: `svx/cli.py` Typer `record` command parses args (e.g., --prompt, --save-all, --gui, --transcribe).
- **Config & Prompt**: Load `Config` via `Config.load()` (`core/config.py`); supports dict of prompts in config.toml (e.g., [prompt.default], [prompt.other]); if transcribe_mode, skip prompt resolution; else resolve prompt with `cfg.resolve_prompt(key="default" for CLI, or selected key for GUI)` (`core/prompt.py`).
- **Pipeline**: Run `RecordingPipeline` (`core/pipeline.py`): record WAV/stop (`core/audio.py`), optional conversion (ffmpeg), get provider/init (`providers/__init__.py`, e.g., `mistral.py` from `cfg`); if transcribe_mode (CLI only): no prompt, model override to voxtral-mini-latest (with warning if changed), pass transcribe_mode to provider.transcribe; for GUI: --transcribe ignored (warning), recording starts immediately, uses modular record()/process()/clean() with dynamic mode (Transcribe: no prompt, model override; Prompt key: resolved prompt for selected key); transcribe, conditional save (`core/storage.py` based on `keep_*`/`save_all`), clipboard copy, logging setup.
- **Cleanup**: Temp files auto-deleted (tempfile) if `keep_*=false`; dirs created only if persistence enabled.
- **End**: Return `{"text": str, "raw": dict, "duration": float, "paths": dict}`; CLI prints result (uses "default" prompt), GUI emits progress/updates via callback (buttons: 'Transcribe' for no prompt; capitalized prompt keys (e.g., 'Default', 'Test') for selected prompt; 'Cancel'; Esc/close cancels).

## Build & test
```bash
# Setup
uv pip install -e .

# Lint & format
black svx/
ruff check svx/

# Diagnostics (post-edits)
# Use `diagnostics` tool or run locally to check errors/warnings in pipeline.py, config.py, etc.
basedpyright svx

# Run
# Initialize user config (generates config.toml with zero-footprint defaults)
svx config init

# Record (provider/format configured in config.toml; tests zero-footprint)
svx record --prompt "What's in this file?"

# Test persistence
svx record --save-all --prompt "Test persistence"

# Test GUI
svx record --gui
```

## Maintenance

- use `uv` to install dependancies if needed
- update `pyproject.toml` then run uv `pip install -e .`
- When adding modules: Propagate Config instance; use RecordingPipeline for recording flows; handle temp files via keep_* flags.
- Test temp cleanup: Verify no leftovers in default mode (keep_*=false).


## Code style
- **Imports**: `from __future__ import annotations` first, then stdlib, third-party, local
- **Formatting**: Black (100 line length), ruff for linting (E, F, I, UP rules)
- **Types**: Full type hints required, use `TypedDict` for data structures, `Protocol` for interfaces (e.g., Provider protocol, Config dataclasses with type hints)
- **Naming**: snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants
- **Error handling**: Custom exceptions inherit from standard types, use `ProviderError` for API failures
- **Docstrings**: Google-style with clear purpose/dependencies/`__all__` exports

## Security
- API keys are configured in the user config file (`config.toml`), under provider-specific sections.
- Mistral: define `[providers.mistral].api_key`
- Environment variables are not used for API keys.
- Validate user inputs (e.g., paths in Config, prompt resolution).
