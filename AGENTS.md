# SuperVoxtral — Agent Guide

## Project overview
Python CLI/TUI for audio recording + transcription via APIs (Mistral Voxtral, Whisper). MVP: manual stop, API-based, results in `transcripts/`.

### Project structure
```
supervoxtral/
├── svx/                           # Python package
│   ├── __init__.py
│   ├── cli.py                     # Typer CLI entrypoint (orchestration only)
│   ├── core/                      # Core logic (audio, config, prompts, storage)
│   │   ├── audio.py               # Recording, ffmpeg detection/conversion
│   │   ├── config.py              # Paths, env loading, logging setup
│   │   ├── prompt.py              # Prompt resolution and default files
│   │   └── storage.py             # Save transcripts and raw JSON
│   ├── providers/                 # API integrations
│   │   ├── __init__.py            # Provider registry (get_provider, register...)
│   │   ├── base.py                # Provider protocol + shared types
│   │   └── mistral.py             # Mistral Voxtral implementation
│   └── ui/                        # TUI (future)
├── prompt/                        # Default user prompt file (user.md)
├── recordings/                    # Audio files (WAV/MP3/Opus)
├── transcripts/                   # API responses (txt/json)
├── logs/                          # Application logs
├── pyproject.toml                 # Project metadata & deps
├── .env.example                   # Template for secrets
└── README.md                      # User guide
```

## Build & test
```bash
# Setup
uv pip install -e .

# Lint & format
black svx/
ruff check svx/
mypy svx/

# Run
svx --provider mistral --format mp3 --prompt "What's in this file?"
```

## Maintenance

- use `uv` to install dependancies if needed
- update `pyproject.toml` then run uv `pip install -e .`


## Code style
- **Imports**: `from __future__ import annotations` first, then stdlib, third-party, local
- **Formatting**: Black (100 line length), ruff for linting (E, F, I, UP rules)
- **Types**: Full type hints required, use `TypedDict` for data structures, `Protocol` for interfaces
- **Naming**: snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants
- **Error handling**: Custom exceptions inherit from standard types, use `ProviderError` for API failures
- **Docstrings**: Google-style with clear purpose/dependencies/`__all__` exports

## Security
- API keys in `.env` (gitignored)
- Required: `MISTRAL_API_KEY`
- Optional: `OPENAI_API_KEY`
- Validate user inputs
