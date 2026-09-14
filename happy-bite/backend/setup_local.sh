#!/usr/bin/env bash
# Install the model packages Happy Bite was already trying to import.
#
# The app's local providers import torch/diffusers (pictures), kokoro (speaking)
# and faster-whisper (hearing) lazily, on first use. None of them were in
# requirements.txt, which is why pictures failed with a bare ModuleNotFoundError
# and the voice never came back. This installs them, in the order that hurts
# least if you stop halfway.
#
#   cd backend && source .venv/bin/activate && ./setup_local.sh
#
# Nothing here is required. Skip a section and that one feature stays off — the
# server now says so at /api/health/models instead of failing at the button.

set -u
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
step() { printf '\n%s==>%s %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '%s !%s %s\n' "$YELLOW" "$OFF" "$1"; }

if [ -z "${VIRTUAL_ENV:-}" ]; then
  warn "No virtualenv active. Run 'source .venv/bin/activate' first, or this"
  warn "installs several GB into your system Python."
  printf 'Carry on anyway? [y/N] '
  read -r reply
  case "$reply" in [yY]*) ;; *) exit 1 ;; esac
fi

PY_MINOR=$(python -c 'import sys; print(sys.version_info[1])')
if [ "$PY_MINOR" -ge 13 ]; then
  warn "Python 3.$PY_MINOR — torch and kokoro don't publish wheels for it yet."
  warn "Use a 3.11 or 3.12 venv for the backend if the next step fails."
fi

step "Hearing — faster-whisper"
pip install -q "faster-whisper>=1.0" && echo "ok" || warn "faster-whisper failed"

step "Speaking — Kokoro (82M, far better than Piper)"
pip install -q kokoro soundfile "misaki[en]" && echo "ok" || warn "kokoro failed"
if ! command -v espeak-ng >/dev/null 2>&1; then
  warn "espeak-ng isn't installed — Kokoro's phonemiser wants it."
  warn "  macOS:  brew install espeak-ng     Debian:  sudo apt install espeak-ng"
fi

step "Pictures — Stable Diffusion Turbo via diffusers (~2.5 GB on first run)"
pip install -q torch diffusers transformers accelerate safetensors pillow \
  && echo "ok" || warn "diffusers stack failed"

step "Receipts — the character recogniser"
pip install -q pytesseract pillow && echo "ok" || warn "pytesseract failed"
if ! command -v tesseract >/dev/null 2>&1; then
  warn "The tesseract program isn't on PATH — pytesseract is only the wrapper."
  warn "  macOS:  brew install tesseract tesseract-lang"
  warn "  Debian: sudo apt install tesseract-ocr tesseract-ocr-fra"
fi

step "What the server will see"
python - <<'PYCHECK'
import importlib.util as u
rows = [
    ("faster_whisper", "hearing (Whisper)"),
    ("kokoro",         "speaking (Kokoro)"),
    ("soundfile",      "speaking (audio out)"),
    ("torch",          "pictures (tensor engine)"),
    ("diffusers",      "pictures (SD-Turbo)"),
    ("pytesseract",    "receipts (OCR wrapper)"),
]
for mod, what in rows:
    try:
        found = u.find_spec(mod) is not None
    except Exception:
        found = False
    print(f"  {'yes' if found else ' no'}   {mod:<16} {what}")
PYCHECK

cat <<'NEXT'

Now put these in backend/.env (a local, no-cloud setup):

    LLM_PROVIDER=openai
    OPENAI_BASE_URL=http://localhost:11434/v1
    LLM_MODEL=qwen2.5:3b-instruct
    OPENAI_API_KEY=ollama

    IMAGE_PROVIDER=local
    IMAGE_MODEL=stabilityai/sd-turbo
    IMAGE_COUNT=3

    SPEECH_PROVIDER=local
    TTS_ENGINE=kokoro
    KOKORO_LANG=a           # f = French, a = US English
    KOKORO_VOICE=af_nicole
    WHISPER_MODEL=base

Then:  uvicorn app.main:app --reload --port 8000
And:   open http://localhost:8000/api/health/models
NEXT
