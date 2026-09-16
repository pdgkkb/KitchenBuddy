#!/usr/bin/env bash
# Install everything the Happy Bite backend can use on this Mac, and say what
# is still missing.
#
#     cd backend && ./setup_local.sh
#     cd backend && ./run.sh               # then start the server
#
# Safe to run again. Anything already installed is skipped, so a second run
# takes seconds and ends with the same report.
#
# requirements-mac.txt is the ONLY list of packages — this script has none of
# its own, so the pins in that file (diffusers, transformers…) are the ones that
# get installed. It is installed one section at a time: a section that fails
# (usually torch, on the wrong Python) leaves the others in place instead of
# taking the whole install down with it.
#
# Like run.sh, it uses .venv/bin/python by path, never whatever `python` is on
# PATH — so it doesn't matter whether a venv is activated.
#
# On Windows use requirements-windows.txt and run.ps1.

set -u
cd "$(dirname "$0")"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
step() { printf '\n%s==>%s %s\n' "$GREEN" "$OFF" "$1"; }
ok()   { printf '  %sok%s  %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '  %s!%s   %s\n' "$YELLOW" "$OFF" "$1"; }

REQ="requirements-mac.txt"
[ -f "$REQ" ] || { echo "${RED}$REQ is missing.${OFF}"; exit 1; }

# ---- 1. the venv: made here if missing, a supported Python or nothing -----

if [ ! -x ".venv/bin/python" ]; then
  BASE=$(command -v python3.12 || command -v python3.11 || true)
  if [ -z "$BASE" ]; then
    echo "${RED}No .venv, and no python3.12 to make one with.${OFF}"
    echo "    brew install python@3.12 && ./setup_local.sh"
    exit 1
  fi
  step "Creating .venv with $BASE"
  "$BASE" -m venv .venv || exit 1
fi

PY="$PWD/.venv/bin/python"
VERSION=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
MINOR=$("$PY" -c 'import sys; print(sys.version_info[1])')

if [ "$MINOR" -ge 13 ]; then
  echo "${RED}The venv is Python $VERSION.${OFF} torch, diffusers and kokoro publish no"
  echo "wheels for it, so pictures and voice cannot work. Rebuild it on 3.12:"
  echo "    rm -rf .venv && ./setup_local.sh"
  exit 1
fi

step "Python $VERSION  ${DIM}$PY${OFF}"
[ "$(uname -m)" = "arm64" ] || warn "Not Apple Silicon — the GPU voice packages will be skipped."

# ---- 2. Python packages, one section of requirements-mac.txt at a time ----

SECTIONS=$(mktemp -d)
trap 'rm -rf "$SECTIONS"' EXIT

# Split on the "# ---- title" headers. Sections with no requirement lines in
# them (the closing warning) are dropped.
"$PY" - "$REQ" "$SECTIONS" <<'SPLIT'
import re, sys
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
title, lines, n = None, [], 0

def flush():
    global n
    if title and any(l.split("#")[0].strip() for l in lines):
        n += 1
        (out / f"{n:02d}.txt").write_text(f"# {title}\n" + "\n".join(lines) + "\n")

for line in src.read_text().splitlines():
    m = re.match(r"#\s*-{3,}\s*(.+)$", line)
    if m:
        flush()
        title, lines = m.group(1).strip(), []
    else:
        lines.append(line)
flush()
SPLIT

FAILED=""
CORE_FAILED=0
for f in "$SECTIONS"/*.txt; do
  title=$(head -n 1 "$f" | sed 's/^# //')
  step "$title"
  "$PY" -m pip install --disable-pip-version-check -r "$f" 2>&1 \
    | grep -v '^Requirement already satisfied'
  if [ "${PIPESTATUS[0]}" -eq 0 ]; then
    ok "$title"
  else
    warn "$title — failed (see above); that feature stays off"
    FAILED="$FAILED$title, "
    [ "$title" = "the app itself" ] && CORE_FAILED=1
  fi
done

# ---- 3. programs pip cannot install --------------------------------------

step "Programs from Homebrew"

OCR_LANGS=$("$PY" -c 'from app.config import settings; print(settings().ocr_languages)' 2>/dev/null || echo "fra+eng")
BREW_MISSING=""
add_formula() { case " $BREW_MISSING " in *" $1 "*) ;; *) BREW_MISSING="$BREW_MISSING $1" ;; esac; }

need() {  # program, formula(s), what it is for
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1"
  else
    warn "$1 is missing — $3"
    for formula in $2; do add_formula "$formula"; done
  fi
}

need espeak-ng espeak-ng              "Kokoro's phonemiser on the CPU path"
need ffmpeg    ffmpeg                 "the GPU hearing models decode the browser's recording with it"
need tesseract "tesseract tesseract-lang" "reading receipts (pytesseract is only the wrapper)"

if command -v tesseract >/dev/null 2>&1; then
  HAVE_LANGS=$(tesseract --list-langs 2>/dev/null)
  for lang in $(echo "$OCR_LANGS" | tr '+' ' '); do
    if echo "$HAVE_LANGS" | grep -qx "$lang"; then
      ok "tesseract language: $lang"
    else
      warn "tesseract has no '$lang' language pack (OCR_LANGUAGES=$OCR_LANGS)"
      add_formula tesseract-lang
    fi
  done
fi

if [ -n "$BREW_MISSING" ]; then
  if command -v brew >/dev/null 2>&1 && [ -t 0 ]; then
    printf '\n  Install them now with  brew install%s  ? [y/N] ' "$BREW_MISSING"
    read -r reply
    case "$reply" in
      # shellcheck disable=SC2086
      [yY]*) brew install $BREW_MISSING || warn "brew install failed" ;;
      *)     warn "Skipped. Later:  brew install$BREW_MISSING" ;;
    esac
  else
    warn "Install with:  brew install$BREW_MISSING"
  fi
fi

# ---- 4. what the server will find ----------------------------------------

step "What the server will see"
"$PY" - <<'REPORT'
import importlib.util as u

def have(mod):
    try:
        return u.find_spec(mod) is not None
    except Exception:
        return False

rows = [
    ("fastapi",           "the server"),
    ("pydantic_settings", "reading .env"),
    ("multipart",         "voice and receipt uploads"),
    ("openai",            "chat through LM Studio / Ollama / OpenAI"),
    ("anthropic",         "chat through Claude"),
    ("parakeet_mlx",      "hearing, on the GPU"),
    ("mlx_whisper",       "hearing, on the GPU (fallback)"),
    ("faster_whisper",    "hearing, on the CPU"),
    ("mlx_audio",         "speaking, Kokoro on the GPU"),
    ("kokoro",            "speaking, Kokoro on the CPU"),
    ("misaki",            "Kokoro's phonemiser"),
    ("soundfile",         "audio out"),
    ("piper",             "speaking, Piper (fallback)"),
    ("torch",             "pictures and CPU Kokoro"),
    ("diffusers",         "pictures (SD-Turbo)"),
    ("pytesseract",       "receipts"),
]
for mod, what in rows:
    print(f"  {'yes' if have(mod) else ' no'}   {mod:<18} {what}")

print()
try:
    from app.config import settings
    s = settings()
except Exception as e:
    print(f"  couldn't read backend/.env: {e}")
    raise SystemExit

where = f" at {s.openai_base_url}" if s.openai_base_url else ""
print(f"  chat      {s.llm_provider}: {s.llm_model}{where}")
if s.llm_provider == "openai" and s.openai_base_url:
    try:
        import httpx
        r = httpx.get(s.openai_base_url.rstrip("/") + "/models", timeout=3)
        print(f"            model server answers ({r.status_code})")
    except Exception:
        print("            model server is NOT answering — start LM Studio and load the model")

print(f"  voice     {s.speech_provider}: backend={s.speech_backend}, "
      f"hearing={s.stt_engine}, speaking={s.tts_engine} ({s.kokoro_voice})")

if s.image_provider == "none":
    print("  pictures  none in the server — paint them with: .venv/bin/python tools/make_photos.py")
else:
    print(f"  pictures  {s.image_provider}: {s.image_model}")
REPORT

cat <<NEXT

${GREEN}Next${OFF}
  ./run.sh                                   start the server
  open http://localhost:8000/api/health/models
  .venv/bin/python tools/make_photos.py      paint the recipe pictures that are waiting
  .venv/bin/python check_voice.py            if the microphone fails (server stopped first)
NEXT

if [ -n "$FAILED" ]; then
  echo
  warn "Did not install: ${FAILED%, }"
fi
[ "$CORE_FAILED" -eq 0 ] || { echo "${RED}The server itself will not start until 'the app itself' installs.${OFF}"; exit 1; }
exit 0
