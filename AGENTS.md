# SuperVoxtral — Agent Guide

## Project overview
Python CLI/TUI for audio recording + transcription via APIs (Mistral Voxtral, Whisper). MVP: manual stop, API-based, results in `transcripts/`.

### Project structure
```
supervoxtral/
├── svx/                    # Python package
│   ├── __init__.py
│   ├── cli.py              # Typer CLI entrypoint
│   ├── core/               # Audio, config, storage (placeholders)
│   ├── providers/          # API integrations
│   └── ui/                 # TUI (future)
├── recordings/             # Audio files (WAV/MP3/Opus)
├── transcripts/            # API responses (txt/json)
├── logs/                   # Application logs
├── pyproject.toml          # Project metadata & deps
├── .env.example            # Template for secrets
└── README.md               # User guide
```

## Build & test
```bash
# Setup
source .venv/bin/activate
uv pip install -e .

# Run
svx --provider mistral --format mp3 --prompt "What's in this file?"


## Security
- API keys in `.env` (gitignored)
- Required: `MISTRAL_API_KEY`
- Optional: `OPENAI_API_KEY`
- Validate user inputs
