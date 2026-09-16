#!/usr/bin/env bash
# Start the Happy Bite backend, correctly, every time.
#
#     cd backend && ./run.sh               # restarts itself when a backend file changes
#     cd backend && ./run.sh --no-reload   # for cooking: no restarts, so no voice re-warming
#
# On Windows use run.ps1.
# It exists because the failure it prevents is invisible: `uvicorn` on your PATH
# can be a DIFFERENT Python from the venv you activated. The server then runs
# under that other interpreter, can't see anything you pip-installed, and
# reports every local feature as "package missing" while the same import works
# fine in your shell. `python -m uvicorn` cannot make that mistake.
#
# It also kills whatever is already holding the port. A second `uvicorn` that
# fails to bind leaves the FIRST one serving — so you edit code, --reload picks
# it up, and you conclude the new code is broken when you are simply talking to
# a process you thought you had replaced.

set -u
cd "$(dirname "$0")"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
PORT="${PORT:-8000}"
RELOAD="--reload"
[ "${1:-}" = "--no-reload" ] && RELOAD=""

# ---- 1. the venv must exist and be the one we use -------------------------

if [ ! -x ".venv/bin/python" ]; then
  echo "${RED}No .venv here.${OFF} Create one with a supported Python:"
  echo "    python3.12 -m venv .venv && source .venv/bin/activate"
  echo "    pip install -r requirements-mac.txt"
  exit 1
fi

PY="$PWD/.venv/bin/python"          # used directly — never whatever is on PATH
VERSION=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
MINOR=$("$PY" -c 'import sys; print(sys.version_info[1])')

if [ "$MINOR" -ge 13 ]; then
  echo "${RED}The venv is Python $VERSION.${OFF} torch, diffusers and kokoro publish no"
  echo "wheels for it, so pictures and voice cannot work. Rebuild it on 3.12:"
  echo "    deactivate 2>/dev/null; rm -rf .venv"
  echo "    python3.12 -m venv .venv && source .venv/bin/activate"
  echo "    pip install -r requirements-mac.txt && ./setup_local.sh"
  exit 1
fi

# ---- 2. nothing else may be holding the port ------------------------------

if command -v lsof >/dev/null 2>&1; then
  HOLDING=$(lsof -ti:"$PORT" 2>/dev/null || true)
  if [ -n "$HOLDING" ]; then
    echo "${YELLOW}Port $PORT was in use by PID(s): $HOLDING — stopping them.${OFF}"
    # shellcheck disable=SC2086
    kill $HOLDING 2>/dev/null || true
    sleep 1
    STILL=$(lsof -ti:"$PORT" 2>/dev/null || true)
    # shellcheck disable=SC2086
    [ -n "$STILL" ] && { kill -9 $STILL 2>/dev/null || true; sleep 1; }
  fi
fi

# ---- 3. say what will actually run ----------------------------------------

echo "${GREEN}Python $VERSION${OFF}  $PY"
"$PY" - <<'PYCHECK'
import importlib.util as u
rows = [("faster_whisper", "hearing"), ("kokoro", "speaking"), ("soundfile", "audio out"),
        ("torch", "pictures"), ("diffusers", "pictures"), ("pytesseract", "receipts"),
        ("uvicorn", "the server itself")]
missing = []
for mod, what in rows:
    try:
        ok = u.find_spec(mod) is not None
    except Exception:
        ok = False
    if not ok:
        missing.append((mod, what))
if missing:
    print("  missing from this venv:")
    for mod, what in missing:
        print(f"    - {mod:<16} ({what})")
    print("  run ./setup_local.sh to install them")
else:
    print("  every local model package present")
PYCHECK

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "${YELLOW}Installing uvicorn into the venv…${OFF}"
  "$PY" -m pip install -q uvicorn || { echo "${RED}couldn't install uvicorn${OFF}"; exit 1; }
fi

echo
# shellcheck disable=SC2086
exec "$PY" -m uvicorn app.main:app $RELOAD --port "$PORT"