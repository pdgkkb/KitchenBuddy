#!/usr/bin/env python3
"""Why the microphone comes back 502.

Run it from the KitchenBuddy folder, with the backend's virtualenv active and
the backend NOT running (it doesn't need the server — it loads the same code
the server loads):

    source backend/.venv/bin/activate        # or wherever your venv is
    python3 check_voice.py

It does exactly what /api/speech/hear does, in the same process, with nothing
catching the exception. The 502 you get in the browser is this same failure
with the useful part removed.

Three stages, each one printed:

    1. speaking      Kokoro makes a WAV, so there is real audio to transcribe
    2. encoding      ffmpeg turns that WAV into webm/opus — what Chrome sends
    3. hearing       the webm goes through hear(), the way the server does it

If stage 3 fails, the whole trace is printed. That trace is the answer.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import traceback
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

GREEN, RED, YEL, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YEL = DIM = OFF = ""

ok = lambda m: print(f"  {GREEN}ok{OFF}   {m}")          # noqa: E731
no = lambda m: print(f"  {RED}FAIL{OFF} {m}")            # noqa: E731
note = lambda m: print(f"  {DIM}{m}{OFF}")               # noqa: E731


def main() -> int:
    try:
        from app.config import settings
        from app.providers.speech_local import make_local_speech
        cfg = settings()                     # settings is a cached factory, not the object
    except Exception:                                    # noqa: BLE001
        print(f"{RED}Can't even import the backend.{OFF}")
        print("Run this from the KitchenBuddy folder, with the backend's venv active.\n")
        traceback.print_exc()
        return 2

    print("\nSettings")
    for name in ("speech_provider", "speech_backend", "stt_engine", "whisper_model",
                 "whisper_device", "whisper_compute", "stt_language",
                 "tts_engine", "kokoro_voice", "kokoro_lang"):
        note(f"{name:18} {getattr(cfg, name, '(not set)')}")

    if cfg.speech_provider != "local":
        print(f"\n{YEL}SPEECH_PROVIDER={cfg.speech_provider}{OFF} — this checks the local "
              "path only. Set SPEECH_PROVIDER=local in backend/.env to use it.")
        return 2

    sp = make_local_speech(cfg)
    if sp is None:
        no("make_local_speech returned None — the server has no voice provider at all, "
           "which is why every call 502s. Check TTS_ENGINE and PIPER_VOICE in backend/.env.")
        return 1

    # --------------------------------------------------------------- 1. speak
    print("\n1. Speaking — making audio to transcribe")
    try:
        wav = asyncio.run(sp.say("Testing, one two three. The rice needs ten minutes.", None))
        ok(f"{len(wav)} bytes of WAV")
    except Exception:                                    # noqa: BLE001
        no("speaking failed — that is a separate bug from hearing")
        traceback.print_exc()
        wav = _silence()
        note("carrying on with silence instead, so hearing is still checked")

    (ROOT / "voice-check.wav").write_bytes(wav)
    note("saved voice-check.wav — play it to hear what the app sounds like")

    # -------------------------------------------------------------- 2. encode
    print("\n2. Encoding — turning it into what the browser actually sends")
    webm, mime, name = wav, "audio/wav", "check.wav"
    exe = shutil.which("ffmpeg")
    if not exe:
        note("no ffmpeg on PATH — testing with WAV instead of webm.")
        note("Chrome records webm/opus, so this misses container problems.")
        note("  winget install Gyan.FFmpeg" if sys.platform == "win32" else "  brew install ffmpeg")
    else:
        try:
            out = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                 "-c:a", "libopus", "-f", "webm", "pipe:1"],
                input=wav, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            if out.returncode == 0 and out.stdout:
                webm, mime, name = out.stdout, "audio/webm;codecs=opus", "check.webm"
                ok(f"{len(webm)} bytes of webm/opus")
            else:
                no("ffmpeg couldn't encode: " + out.stderr.decode("utf-8", "replace")[-200:])
        except Exception:                                # noqa: BLE001
            no("ffmpeg failed")
            traceback.print_exc()

    # --------------------------------------------------------------- 3. hear
    print("\n3. Hearing — the exact call /api/speech/hear makes")
    try:
        text = asyncio.run(sp.hear(webm, name, mime))
    except Exception:                                    # noqa: BLE001
        no("THIS is your 502. The whole trace:\n")
        traceback.print_exc()
        print(f"\n{YEL}What to do with it{OFF}")
        print("  ModuleNotFoundError: onnxruntime   ->  pip install onnxruntime")
        print("  anything about av / opus / codec   ->  pip install --force-reinstall av")
        print("  ffmpeg not found                   ->  "
              + ("winget install Gyan.FFmpeg" if sys.platform == "win32" else "brew install ffmpeg"))
        print("  anything else                      ->  send me the trace above\n")
        return 1

    if text.strip():
        ok(f'heard: "{text.strip()}"')
        print(f"\n{GREEN}Hearing works.{OFF} If the browser still 502s, the audio it sends "
              "is the difference — check the backend console, which now prints the trace.\n")
        return 0
    no("no error, but nothing was transcribed either")
    note("An empty transcription is what the silence filter does to audio it "
         "thinks is silent. If stage 1 failed and this ran on silence, that is "
         "expected — fix speaking first.")
    return 1


def _silence() -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 16_000)
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
