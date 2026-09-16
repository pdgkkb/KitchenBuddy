"""Fully-local voice, on the GPU this Mac actually has.

    hear(audio) -> text      speech to text
    say(text)   -> audio     Kokoro-82M

WHAT CHANGED AND WHY
--------------------
The previous version ran `faster-whisper` (CTranslate2, CPU) and Kokoro
(PyTorch, CPU). Both work. Both leave an Apple Silicon GPU sitting idle while
the CPU does matrix maths, on a machine whose whole architecture is unified
memory — nothing has to be copied to reach the GPU, so there is no reason not
to use it.

So the default is now MLX: Parakeet or Whisper for hearing, Kokoro for
speaking, both on the GPU.

THE OLD PATH IS STILL HERE AND STILL WORKS. If MLX is not installed, or a
model will not load, this falls back to faster-whisper and torch Kokoro and
PRINTS WHY. An upgrade that can leave you with no voice at all is not an
upgrade; you should be able to install the new packages when it suits you and
have the app keep working until then.

    SPEECH_BACKEND=auto     try MLX, fall back quietly to the old path (default)
    SPEECH_BACKEND=mlx      MLX or nothing — fail loudly, for testing
    SPEECH_BACKEND=torch    the old path, deliberately

Install the MLX side:

    pip install mlx mlx-audio parakeet-mlx mlx-whisper misaki
    brew install ffmpeg          # decodes the browser's webm before transcription

On Windows there is no MLX: hearing is faster-whisper and speaking is Kokoro on
torch (CPU, or CUDA when torch was installed with it). The last link of the
speaking chain is the Windows system voice (System.Speech through PowerShell)
where a Mac would use `say`.

WHAT CHANGED THIS TIME, AND WHY IT IS THE SAME LESSON AS BEFORE
--------------------------------------------------------------
`_load_tts` used to be the only place a speaking engine could be rejected, and
it only ever watched the model LOAD. Kokoro-on-MLX loads perfectly without
`misaki` — the package it needs to turn letters into sounds — and then throws
on the FIRST GENERATE:

    ImportError: Kokoro requires the optional 'misaki' package for text
    processing. Install it with: pip install misaki

So the startup log said "voice: speaking with Kokoro on MLX", the fallback to
torch Kokoro never ran, and every single `say()` 502'd. That is word for word
the bug this file already documented on the hearing side — "loading a model
proves nothing about decoding audio" — and the speaking side had not learnt it.

Now speaking is a CHAIN, not a choice. Each engine is built and tried at the
moment it has to produce sound; one that raises is named once, dropped for the
rest of the process, and the next one takes over mid-call. The chain ends on
macOS with `say`, the system voice, which needs nothing installed at all. It
is not as good as Kokoro. It is infinitely better than silence, and it means a
missing Python package can never again cost you the whole feature.

    TTS_ENGINE=kokoro   Kokoro (MLX, then torch), then Piper, then `say`  (default)
    TTS_ENGINE=piper    Piper first
    TTS_ENGINE=say      the macOS system voice, deliberately — no models, instant
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from ..config import Settings

KOKORO_RATE = 24_000                       # Kokoro's output rate
SAY_RATE = 22_050                          # what we ask the system voice for
WINDOWS = sys.platform == "win32"

# Speaks $env:HB_TEXT into $env:HB_OUT. The text travels in an environment
# variable, never inside the script, so nothing it contains can run as code.
_SAPI_SCRIPT = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "if ($env:HB_VOICE) { $s.SelectVoice($env:HB_VOICE) };"
    "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
    f"{SAY_RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
    " [System.Speech.AudioFormat.AudioChannel]::Mono);"
    "$s.SetOutputToWaveFile($env:HB_OUT, $f);"
    "$s.Speak($env:HB_TEXT);"
    "$s.Dispose()"
)


def _install(mac: str, windows: str) -> str:
    return windows if WINDOWS else mac


def _flag(s, name: str, default: str) -> str:
    return str(getattr(s, name, default) or default).lower()


class LocalSpeech:
    mime = "audio/wav"                     # every engine here emits PCM WAV

    def __init__(self, s: Settings):
        self.s = s
        self.backend = _flag(s, "speech_backend", "auto")
        self._stt_impl = None              # ("parakeet"|"mlx-whisper"|"faster-whisper", obj)
        self._tts_impl = None              # (name, obj) — the one currently working
        self._tts_chain = None             # names still worth trying, in order
        self._vad = True                   # turned off for good if it ever raises
        self._said_decode = False          # the ffmpeg note is printed once

    # ------------------------------------------------------------ hearing

    def _load_stt(self):
        if self._stt_impl:
            return self._stt_impl
        want_mlx = self.backend in ("auto", "mlx")

        if want_mlx:
            engine = _flag(self.s, "stt_engine", "parakeet")
            if engine == "parakeet":
                try:
                    from parakeet_mlx import from_pretrained
                    name = getattr(self.s, "parakeet_model", "") or "mlx-community/parakeet-tdt-0.6b-v3"
                    self._stt_impl = ("parakeet", from_pretrained(name))
                    print(f"voice: hearing with Parakeet on MLX ({name})")
                    return self._stt_impl
                except Exception as e:                       # noqa: BLE001
                    self._fallback_note("Parakeet", e)
            try:
                import mlx_whisper
                name = getattr(self.s, "mlx_whisper_model", "") or "mlx-community/whisper-small-mlx"
                self._stt_impl = ("mlx-whisper", (mlx_whisper, name))
                print(f"voice: hearing with Whisper on MLX ({name})")
                return self._stt_impl
            except Exception as e:                           # noqa: BLE001
                self._fallback_note("Whisper on MLX", e)

        if self.backend == "mlx":
            raise RuntimeError("SPEECH_BACKEND=mlx but no MLX speech model would load. "
                               "pip install mlx-audio parakeet-mlx mlx-whisper")

        from faster_whisper import WhisperModel
        self._stt_impl = ("faster-whisper", WhisperModel(
            self.s.whisper_model, device=self.s.whisper_device,
            compute_type=self.s.whisper_compute))
        print(f"voice: hearing with faster-whisper on {self.s.whisper_device} ({self.s.whisper_model})")
        return self._stt_impl

    async def hear(self, data: bytes, filename: str, mime: str) -> str:
        return await asyncio.to_thread(self._hear_sync, data, filename, mime)

    def _hear_sync(self, data: bytes, filename: str = "", mime: str = "") -> str:
        kind, model = self._load_stt()
        began = time.perf_counter()

        if kind == "faster-whisper":
            text = self._faster_whisper_text(model, data, filename, mime)
        else:
            # The browser sends webm or mp4 from MediaRecorder. faster-whisper
            # decodes that itself; the MLX loaders want a file they can hand to
            # ffmpeg. A temp file is the whole difference, and it costs less
            # than a millisecond.
            hint = f"{filename} {mime}".lower()
            suffix = (".wav" if "wav" in hint else
                      ".mp4" if "mp4" in hint or "m4a" in hint else ".webm")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
                fh.write(data)
                path = fh.name
            try:
                if kind == "parakeet":
                    result = model.transcribe(path)
                    text = (getattr(result, "text", "") or "").strip()
                else:
                    mlx_whisper, repo = model
                    out = mlx_whisper.transcribe(
                        path, path_or_hf_repo=repo,
                        language=self.s.stt_language or None)
                    text = (out.get("text") or "").strip()
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        took = time.perf_counter() - began
        if took > 2.0:
            print(f"voice: that transcription took {took:.1f}s — consider a smaller model")
        return text

    # ---------------------------------------- the two ways transcription fails
    #
    # Both of these blow up INSIDE transcribe(), not when the model loads. That
    # is why the startup log could say "hearing with faster-whisper on the CPU
    # (base.en)" and every single recording still come back 502: loading a
    # model proves nothing about decoding audio.
    #
    #   1. vad_filter=True runs Silero through onnxruntime. onnxruntime is an
    #      optional dependency here — it arrives with piper-tts, which you are
    #      not using. No onnxruntime, no VAD, and the error names onnx.
    #   2. reading the browser's webm/opus goes through PyAV. A PyAV wheel
    #      built without the opus decoder reads the container and fails on the
    #      stream.
    #
    # Neither is worth losing your voice over, so each one is caught, named
    # once, and routed around: VAD is simply switched off (it trims silence,
    # it does not transcribe), and decoding falls back to ffmpeg, which you
    # already installed with brew and which hands back raw samples that skip
    # PyAV entirely.

    def _faster_whisper_text(self, model, data: bytes, filename: str, mime: str) -> str:
        lang = self.s.stt_language or None

        def run(source):
            segments, _ = model.transcribe(source, vad_filter=self._vad,
                                           beam_size=1, language=lang)
            return " ".join(seg.text.strip() for seg in segments).strip()

        try:
            return run(io.BytesIO(data))
        except Exception as e:                               # noqa: BLE001
            blame = f"{type(e).__name__}: {e}".lower()
            if self._vad and ("onnx" in blame or "vad" in blame or "silero" in blame):
                self._vad = False
                print("voice: the silence filter needs onnxruntime, which isn't "
                      "installed — carrying on without it "
                      "(pip install onnxruntime to get it back)")
                return self._faster_whisper_text(model, data, filename, mime)
            pcm = self._pcm_via_ffmpeg(data, filename, mime)   # raises on its own terms
            return run(pcm)

    def _pcm_via_ffmpeg(self, data: bytes, filename: str, mime: str):  # noqa: ARG002
        """webm/mp4 bytes -> 16 kHz mono float32, without PyAV in the way."""
        import numpy as np

        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError(
                "Couldn't decode the recording, and ffmpeg isn't on PATH to do "
                "it instead. Run:  " + _install("brew install ffmpeg", "winget install Gyan.FFmpeg"))
        # No -f on the input: ffmpeg probes the bytes, which is more reliable
        # than trusting a filename the browser made up.
        out = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if out.returncode != 0 or not out.stdout:
            raise RuntimeError("ffmpeg couldn't decode the recording: "
                               + (out.stderr.decode("utf-8", "replace")[-300:] or "no output"))
        if not self._said_decode:
            self._said_decode = True
            print("voice: decoding recordings with ffmpeg (PyAV wouldn't read them)")
        return np.frombuffer(out.stdout, dtype="<f4").copy()

    # ------------------------------------------------------------ speaking
    #
    # A chain, not a choice. See the module docstring: an engine that LOADS is
    # not an engine that SPEAKS, and the difference is one uninstalled package.

    def _chain(self) -> list[str]:
        if self._tts_chain is not None:
            return self._tts_chain
        engine = _flag(self.s, "tts_engine", "kokoro")
        order: list[str] = []
        if engine == "say":
            order.append("say")
        if engine == "piper" and getattr(self.s, "piper_voice", ""):
            order.append("piper")
        if self.backend in ("auto", "mlx"):
            order.append("kokoro-mlx")
        order.append("kokoro-torch")
        if getattr(self.s, "piper_voice", "") and "piper" not in order:
            order.append("piper")
        if "say" not in order and (shutil.which("powershell") if WINDOWS
                                   else sys.platform == "darwin" and shutil.which("say")):
            order.append("say")             # the system voice: needs nothing installed
        self._tts_chain = order
        return order

    def _build(self, name: str):
        """Construct one engine. Raising here drops it from the chain."""
        if name == "piper":
            from piper import PiperVoice
            cfg = self.s.piper_config or (self.s.piper_voice + ".json")
            return PiperVoice.load(self.s.piper_voice, config_path=cfg)
        if name == "kokoro-mlx":
            from mlx_audio.tts.utils import load_model
            repo = getattr(self.s, "kokoro_mlx_repo", "") or "mlx-community/Kokoro-82M-bf16"
            model = load_model(repo)
            print(f"voice: speaking with Kokoro on MLX ({repo})")
            return model
        if name == "kokoro-torch":
            from kokoro import KPipeline
            print("voice: speaking with Kokoro on torch")
            return KPipeline(lang_code=self.s.kokoro_lang or "a")
        if name == "say":
            if WINDOWS:
                if not shutil.which("powershell"):
                    raise RuntimeError("powershell is not on PATH, so the Windows voice can't be used.")
                print("voice: speaking with the Windows system voice — no model needed")
                return None
            if not shutil.which("say"):
                raise RuntimeError("`say` is not on PATH (this is a macOS command).")
            print("voice: speaking with the macOS system voice (`say`) — no model needed")
            return None
        raise RuntimeError(f"unknown speaking engine {name!r}")

    def _render(self, name: str, model, text: str, voice: str | None) -> bytes:
        """Text -> WAV bytes with ONE engine. Raises so the caller can move on."""
        if name == "piper":
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                model.synthesize(text, wav)
            return buf.getvalue()

        if name == "say":
            return self._say_command(text, voice)

        import numpy as np
        chunks = []
        v = voice or self.s.kokoro_voice
        if name == "kokoro-mlx":
            import mlx.core as mx
            for result in model.generate(text=text, voice=v, speed=1.0,
                                         lang_code=self.s.kokoro_lang or "a"):
                audio = result.audio
                # MLX IS LAZY. Without this the work is deferred to whenever
                # the array is first read — which is inside numpy, off this
                # thread's clock, and after the caller thinks it is done.
                mx.eval(audio)
                chunks.append(np.asarray(audio, dtype="float32").reshape(-1))
        else:
            for result in model(text, voice=v):
                audio = result[-1]                      # (graphemes, phonemes, audio)
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                chunks.append(np.asarray(audio, dtype="float32").reshape(-1))

        if not chunks:
            raise RuntimeError("the model produced no audio at all")
        return _wav(np.concatenate(chunks), KOKORO_RATE)

    def _say_command(self, text: str, voice: str | None) -> bytes:
        """The system voice (macOS `say`, Windows System.Speech). No model, no package, no download.

        Deliberately last in the chain and deliberately present: everything
        above it depends on a Python package that can be missing, mismatched
        with the interpreter, or — on a very new Python — not yet built. This
        one is part of the operating system.
        """
        name = voice or getattr(self.s, "say_voice", "") or ""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            path = fh.name
        try:
            if WINDOWS:
                env = {**os.environ, "HB_TEXT": text, "HB_OUT": path, "HB_VOICE": name}
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SAPI_SCRIPT],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            else:
                cmd = ["say", "-o", path, f"--data-format=LEI16@{SAY_RATE}"]
                if name:
                    cmd += ["-v", name]
                out = subprocess.run([*cmd, text], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, timeout=60)
            if out.returncode != 0:
                raise RuntimeError(out.stderr.decode("utf-8", "replace")[-200:]
                                   or "the system voice failed")
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def say(self, text: str, voice: str | None = None) -> bytes:
        return await asyncio.to_thread(self._say_sync, text[:2000], voice)

    def _say_sync(self, text: str, voice: str | None) -> bytes:
        tried: list[str] = []
        while True:
            if self._tts_impl is None:
                chain = self._chain()
                if not chain:
                    raise RuntimeError(
                        "No speaking engine worked. Tried: " + (", ".join(tried) or "nothing")
                        + ". For Kokoro:  " + _install("pip install misaki mlx-audio",
                                                       'pip install kokoro soundfile "misaki[en]"')
                        + "  — or set TTS_ENGINE=say to use the system voice.")
                name = chain[0]
                try:
                    self._tts_impl = (name, self._build(name))
                except Exception as e:                       # noqa: BLE001
                    tried.append(name)
                    self._drop(name, e, "wouldn't load")
                    continue
            name, model = self._tts_impl
            try:
                return self._render(name, model, text, voice)
            except Exception as e:                           # noqa: BLE001
                # THE BUG THIS WHOLE SECTION EXISTS FOR. Kokoro-on-MLX loads
                # without misaki and dies here, on the first word it is asked
                # to say. Naming it and moving down the chain is the difference
                # between "the voice is a bit worse today" and "the voice is
                # gone and the browser shows a 502".
                tried.append(name)
                self._tts_impl = None
                self._drop(name, e, "loaded but couldn't speak")

    def _drop(self, name: str, e: Exception, what: str) -> None:
        chain = self._chain()
        if name in chain:
            chain.remove(name)
        hint = ""
        if "misaki" in f"{e}".lower():
            hint = ("  ->  pip install misaki       (Kokoro needs it to turn letters "
                    "into sounds; it is not installed by mlx-audio)")
        print(f"voice: {name} {what} ({type(e).__name__}: {str(e)[:200]})"
              + (f"\n{hint}" if hint else "")
              + f"\nvoice: falling back to {chain[0] if chain else 'nothing left'}")

    # ------------------------------------------------------------- warming

    async def warmup(self) -> None:
        """Load both models and run one throwaway inference each.

        The old version deliberately did nothing here, because eager loading of
        torch Kokoro could leave a half-built pipeline on meta tensors. MLX has
        no such problem, and the first call compiles Metal kernels — several
        seconds you would otherwise pay in the middle of the first thing you
        say. Failures are printed, never raised: a cold model still works.
        """
        await asyncio.to_thread(self._warm_sync)

    def _warm_sync(self) -> None:
        began = time.perf_counter()
        try:
            # Not _build(). Warming has to go through the SAME call the app
            # makes, because that is the call that finds out whether an engine
            # which loaded can actually produce a sound — and, now, the call
            # that walks down the chain until one can. By the time this line
            # returns, the engine named in the log is a working one.
            self._say_sync("Ready.", None)
            print(f"voice: speaking checked end to end ({self._tts_impl[0]})")
        except Exception as e:                               # noqa: BLE001
            print(f"voice: SPEAKING IS BROKEN — {type(e).__name__}: {e}")
        try:
            # Loading the model proves the model loads; it proves nothing about
            # decoding audio or about the silence filter, which are the two
            # things that actually broke. So warmup transcribes half a second
            # of real silence, through the real code path. If hearing is going
            # to fail, it fails HERE, in the startup log, with its name on it —
            # instead of on the first thing you say, as a 502 in a console.
            self._hear_sync(_silence_wav(), "warmup.wav", "audio/wav")
            print("voice: hearing checked end to end")
        except Exception as e:                               # noqa: BLE001
            print(f"voice: HEARING IS BROKEN — {type(e).__name__}: {e}")
            print("voice: every recording will come back as an error until that "
                  "is fixed. Run  python check_voice.py  for the full trace.")
        print(f"voice: warm in {time.perf_counter() - began:.1f}s")

    # -------------------------------------------------------------- saying so

    @staticmethod
    def _fallback_note(what: str, e: Exception) -> None:
        # Named, every time. A silent fallback is how you end up wondering why
        # the GPU is idle on a machine you installed MLX for.
        print(f"voice: {what} unavailable ({type(e).__name__}: {str(e)[:160]}) — using the CPU path")


def _wav(samples, rate: int) -> bytes:
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _silence_wav() -> bytes:
    """Half a second of 16 kHz silence as a WAV, for the warmup transcription."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(b"\x00\x00" * 8_000)
    return buf.getvalue()


def make_local_speech(s: Settings):
    if s.speech_provider != "local":
        return None
    # No longer refuses to exist when TTS_ENGINE=piper has no voice file: the
    # chain simply skips Piper and speaks with Kokoro or `say`. Switching off
    # the whole local voice — hearing included — over one missing .onnx was an
    # over-reaction that took the microphone down with it.
    return LocalSpeech(s)