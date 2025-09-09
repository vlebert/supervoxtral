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
svx record --provider mistral --format mp3 --prompt "What's in this file?"


## Security
- API keys in `.env` (gitignored)
- Required: `MISTRAL_API_KEY`
- Optional: `OPENAI_API_KEY`
- Validate user inputs

## Progress checklist
```markdown
- [x] Initialiser projet (Typer CLI, config .env)
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
```
