"""Fully-local voice: two small models that never leave the machine.

    hear(audio) -> text     faster-whisper (STT)  — "a model which can hear us"
    say(text)   -> audio     Kokoro-82M (TTS)     — "a model which can speak to us"

Both run on your Mac, no credits, nothing leaves the network. They slot behind
the exact same interface as the OpenAI Speech class, so /api/speech/hear and
/api/speech/say — and the browser — don't change. Set SPEECH_PROVIDER=local.

Kokoro is the default voice: a small (82M) model that sounds far more natural
than Piper. Piper stays available as a lighter fallback (TTS_ENGINE=piper).

Models load lazily on first use, so the server still starts instantly and a
missing package surfaces as a clear 502 on the first voice request.

Install (CPU is fine on an M-series Mac):
    pip install faster-whisper kokoro soundfile "misaki[en]"
    # Kokoro's phonemiser is happiest with espeak-ng present:
    brew install espeak-ng
"""

from __future__ import annotations

import asyncio
import io
import wave

from ..config import Settings


class LocalSpeech:
    mime = "audio/wav"                       # both engines emit PCM WAV

    def __init__(self, s: Settings):
        self.s = s
        self._whisper = None
        self._kokoro = None
        self._piper = None

    # ---- lazy model loaders (heavy imports kept out of server start) ----

    def _stt(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self._whisper = WhisperModel(
                self.s.whisper_model, device=self.s.whisper_device, compute_type=self.s.whisper_compute,
            )
        return self._whisper

    def _kokoro_pipe(self):
        if self._kokoro is None:
            from kokoro import KPipeline
            self._kokoro = KPipeline(lang_code=self.s.kokoro_lang or "a")
        return self._kokoro

    def _piper_voice(self):
        if self._piper is None:
            from piper import PiperVoice
            cfg = self.s.piper_config or (self.s.piper_voice + ".json")
            self._piper = PiperVoice.load(self.s.piper_voice, config_path=cfg)
        return self._piper

    # ---- hear ----

    async def hear(self, data: bytes, filename: str, mime: str) -> str:
        return await asyncio.to_thread(self._hear_sync, data)

    def _hear_sync(self, data: bytes) -> str:
        segments, _ = self._stt().transcribe(
            io.BytesIO(data), vad_filter=True, beam_size=1,
            language=self.s.stt_language or None,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    # ---- say ----

    async def say(self, text: str, voice: str | None = None) -> bytes:
        return await asyncio.to_thread(self._say_sync, text[:2000], voice)

    def _say_sync(self, text: str, voice: str | None) -> bytes:
        if self.s.tts_engine == "piper" and self.s.piper_voice:
            return self._say_piper(text)
        return self._say_kokoro(text, voice or self.s.kokoro_voice)

    def _say_kokoro(self, text: str, voice: str) -> bytes:
        import numpy as np
        pipe = self._kokoro_pipe()
        chunks = []
        for result in pipe(text, voice=voice):
            audio = result[-1]              # (graphemes, phonemes, audio)
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio, dtype="float32"))
        if not chunks:
            return b""
        audio = np.concatenate(chunks)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)          # Kokoro outputs 24 kHz
            w.writeframes(pcm.tobytes())
        return buf.getvalue()

    def _say_piper(self, text: str) -> bytes:
        v = self._piper_voice()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            v.synthesize(text, wav)
        return buf.getvalue()


def make_local_speech(s: Settings):
    if s.speech_provider != "local":
        return None
    # Kokoro needs no file path; Piper needs a voice file.
    if s.tts_engine == "piper" and not s.piper_voice:
        return None
    return LocalSpeech(s)
