"""Pictures and voice.

Pictures can come from OpenAI (image_provider=openai) or run FULLY LOCAL and
free on the Mac (image_provider=local) with Stable Diffusion Turbo through
diffusers — no credits, no limits, no key. The local model downloads once
(~2.5 GB) on first use and then runs on the machine's GPU (Apple MPS / CUDA)
or CPU.

With SPEECH_PROVIDER=none the browser uses its own voice and recogniser.

WHAT CHANGED (and why you saw a bare ModuleNotFoundError)
---------------------------------------------------------
`make_images` / `make_speech` used to hand back a provider object without ever
checking that the provider's *packages* were installed. The server then told
the browser "pictures: on", the browser showed the button, and the failure only
surfaced as a 502 with the exception's class name. Now both factories probe
their imports at start-up and return (provider | None, reason). A missing
package switches the feature off honestly and /api/status says which package.

Providers also gained:
  * `create(prompt, name=None)`   — a deterministic filename when you want one
  * `create_many(prompt, n, ...)` — several takes of the same dish
  * `warmup()`                    — load the weights before the first request
"""

import asyncio
import base64
import importlib.util
import random
import secrets
from pathlib import Path

import httpx

from ..config import Settings

# A picture of the same dish shot a few different ways beats three near
# identical frames. One entry is consumed per extra take.
ANGLES = [
    "shot from directly overhead, flat lay",
    "three-quarter view from the side, plate close to camera",
    "close crop on the food, showing texture",
    "on the table beside the pan it was cooked in",
]
LIGHT = [
    "soft natural daylight from a window",
    "warm late-afternoon light, gentle shadows",
    "bright even kitchen light",
]


def _missing(*modules: str) -> str | None:
    """The first of these modules that isn't importable, or None."""
    for m in modules:
        try:
            if importlib.util.find_spec(m) is None:
                return m
        except (ImportError, ValueError):
            return m
    return None


def _safe(name: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(name))
    return keep.strip("-")[:48] or secrets.token_hex(4)


class Images:
    """OpenAI image generation."""

    kind = "openai"

    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key)
        self.model = s.image_model
        self.dir = s.media_path
        self.dir.mkdir(parents=True, exist_ok=True)

    async def warmup(self) -> None:      # nothing to load; the network is the model
        return None

    async def create(self, prompt: str, name: str | None = None) -> str:
        res = await self.client.images.generate(model=self.model, prompt=prompt, size="1536x1024")
        item = res.data[0]
        if getattr(item, "b64_json", None):
            data = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            async with httpx.AsyncClient(timeout=60) as http:
                data = (await http.get(item.url)).content
        else:
            raise RuntimeError("The image service returned nothing")
        return _write(self.dir, data, name)

    async def create_many(self, prompt: str, n: int, name: str | None = None) -> list[str]:
        return await _takes(self, prompt, n, name)


class LocalImages:
    """Free, local Stable Diffusion (SD-Turbo by default) via diffusers.

    The pipeline is heavy to load, so it's built once on first use and reused.
    Generation runs in a worker thread (it's CPU/GPU-bound and blocking) and is
    serialised with a lock — one picture at a time keeps memory sane on a laptop.
    """

    kind = "local"

    def __init__(self, s: Settings):
        self.model = s.image_model or "stabilityai/sd-turbo"
        self.steps = max(1, int(getattr(s, "image_steps", 2) or 2))
        self.size = int(getattr(s, "image_size", 512) or 512)
        self.device_pref = getattr(s, "image_device", "auto") or "auto"
        self.dir = s.media_path
        self.dir.mkdir(parents=True, exist_ok=True)
        self._pipe = None
        self._lock = asyncio.Lock()

    def _device(self) -> str:
        if self.device_pref != "auto":
            return self.device_pref
        import torch
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load(self):
        import torch
        from diffusers import AutoPipelineForText2Image
        dev = self._device()
        # fp16 is a real speed/memory win on CUDA; on MPS and CPU fp32 is the
        # reliable choice (fp16 can render black frames on some Metal builds).
        dtype = torch.float16 if dev == "cuda" else torch.float32
        pipe = AutoPipelineForText2Image.from_pretrained(self.model, torch_dtype=dtype)
        pipe = pipe.to(dev)
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
        return pipe

    def _generate(self, prompt: str, name: str | None, seed: int | None) -> str:
        if self._pipe is None:
            self._pipe = self._load()
        gen = None
        if seed is not None:
            import torch
            gen = torch.Generator(device="cpu").manual_seed(int(seed))
        # SD-Turbo is built for very few steps with no classifier-free guidance.
        image = self._pipe(
            prompt=prompt,
            num_inference_steps=self.steps,
            guidance_scale=0.0,
            height=self.size,
            width=int(self.size * 1.5),
            generator=gen,
        ).images[0]
        target = _path(self.dir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return "/media/" + target.relative_to(self.dir).as_posix()

    async def warmup(self) -> None:
        """Build the pipeline now so the first real picture isn't a 30 s wait."""
        async with self._lock:
            if self._pipe is None:
                await asyncio.to_thread(lambda: setattr(self, "_pipe", self._load()))

    async def create(self, prompt: str, name: str | None = None, seed: int | None = None) -> str:
        async with self._lock:                     # one at a time — laptop memory
            return await asyncio.to_thread(self._generate, prompt, name, seed)

    async def create_many(self, prompt: str, n: int, name: str | None = None) -> list[str]:
        return await _takes(self, prompt, n, name)


def _path(directory: Path, name: str | None) -> Path:
    if not name:
        return directory / f"{secrets.token_hex(8)}.png"
    rel = "/".join(_safe(p) for p in str(name).split("/") if p.strip())
    return directory / (rel + ".png")


def _write(directory: Path, data: bytes, name: str | None) -> str:
    target = _path(directory, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return "/media/" + target.relative_to(directory).as_posix()


async def _takes(provider, prompt: str, n: int, name: str | None) -> list[str]:
    """N versions of one prompt, each framed and lit differently.

    Failures are swallowed per-take: two good pictures beat an exception.
    """
    n = max(1, min(6, int(n or 1)))
    angles = random.sample(ANGLES, k=min(n, len(ANGLES)))
    out: list[str] = []
    for i in range(n):
        variant = f"{prompt} {angles[i % len(angles)]}, {LIGHT[i % len(LIGHT)]}."
        # A fresh suffix per take. Numbering from 1 on every call would
        # overwrite the pictures a recipe already has when topping it up.
        target = f"{name}-{secrets.token_hex(3)}" if name else None
        try:
            if isinstance(provider, LocalImages):
                out.append(await provider.create(variant, target, seed=1000 + i * 7919))
            else:
                out.append(await provider.create(variant, target))
        except Exception as e:  # noqa: BLE001 — one bad take shouldn't lose the others
            print(f"picture {i + 1}/{n} failed:", type(e).__name__, e)
    return out


class Speech:
    mime = "audio/mpeg"

    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key)
        self.s = s

    async def warmup(self) -> None:
        return None

    async def say(self, text: str, voice: str | None = None) -> bytes:
        res = await self.client.audio.speech.create(
            model=self.s.tts_model, voice=voice or self.s.tts_voice, input=text[:2000],
            response_format="mp3",
        )
        return res.content

    async def hear(self, data: bytes, filename: str, mime: str) -> str:
        res = await self.client.audio.transcriptions.create(
            model=self.s.stt_model, file=(filename, data, mime),
        )
        return res.text.strip()


# ------------------------------------------------------------------ factories
# Each returns (provider | None, reason). `reason` is a sentence for a human:
# it goes into /api/status and into the 502 body, so a missing package is
# obvious from the browser instead of being a bare exception name.

def make_images(s: Settings):
    provider, _ = make_images_checked(s)
    return provider


def make_images_checked(s: Settings) -> tuple[object | None, str | None]:
    if s.image_provider == "local":
        gone = _missing("torch", "diffusers", "transformers", "PIL")
        if gone:
            return None, (
                f"Local pictures need the '{gone}' package, which isn't in the backend venv. "
                "Install it with:  pip install torch diffusers transformers accelerate safetensors pillow"
            )
        try:
            return LocalImages(s), None
        except Exception as e:  # noqa: BLE001
            return None, f"Local pictures wouldn't start: {type(e).__name__}: {e}"
    if s.image_provider == "openai":
        if not s.openai_api_key:
            return None, "Pictures are set to OpenAI but OPENAI_API_KEY is empty."
        if _missing("openai"):
            return None, "Pictures need the 'openai' package:  pip install openai"
        try:
            return Images(s), None
        except Exception as e:  # noqa: BLE001
            return None, f"OpenAI pictures wouldn't start: {type(e).__name__}: {e}"
    return None, None            # image_provider=none — switched off on purpose


def make_speech(s: Settings):
    provider, _ = make_speech_checked(s)
    return provider


def make_speech_checked(s: Settings) -> tuple[object | None, str | None]:
    if s.speech_provider == "local":
        gone = _missing("faster_whisper")
        if gone:
            return None, (
                "Local listening needs 'faster-whisper':  pip install faster-whisper"
            )
        engine = (getattr(s, "tts_engine", "kokoro") or "kokoro").lower()
        if engine == "piper":
            gone = _missing("piper")
            if gone:
                return None, "Local speaking needs 'piper-tts':  pip install piper-tts"
            if not getattr(s, "piper_voice", ""):
                return None, "TTS_ENGINE=piper but PIPER_VOICE points at no .onnx voice file."
        else:
            gone = _missing("kokoro", "soundfile")
            if gone:
                return None, (
                    f"Local speaking needs the '{gone}' package. Install with:  "
                    'pip install kokoro soundfile "misaki[en]"   '
                    "(and `brew install espeak-ng` for the phonemiser)"
                )
        try:
            from .speech_local import LocalSpeech
            return LocalSpeech(s), None
        except Exception as e:  # noqa: BLE001
            return None, f"Local voice wouldn't start: {type(e).__name__}: {e}"
    if s.speech_provider == "openai":
        if not s.openai_api_key:
            return None, "Server voice is set to OpenAI but OPENAI_API_KEY is empty."
        if _missing("openai"):
            return None, "Server voice needs the 'openai' package:  pip install openai"
        try:
            return Speech(s), None
        except Exception as e:  # noqa: BLE001
            return None, f"OpenAI voice wouldn't start: {type(e).__name__}: {e}"
    return None, None            # speech_provider=none — the browser speaks instead
