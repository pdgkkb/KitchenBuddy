"""Pictures and voice.

Pictures can come from OpenAI (image_provider=openai) or run FULLY LOCAL and
free on the Mac (image_provider=local) with Stable Diffusion Turbo through
diffusers — no credits, no limits, no key. The local model downloads once
(~2.5 GB) on first use and then runs on the machine's GPU (Apple MPS / CUDA)
or CPU.

With SPEECH_PROVIDER=none the browser uses its own voice and recogniser.
"""

import asyncio
import base64
import secrets

import httpx

from ..config import Settings


class Images:
    """OpenAI image generation."""
    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key)
        self.model = s.image_model
        self.dir = s.media_path
        self.dir.mkdir(parents=True, exist_ok=True)

    async def create(self, prompt: str) -> str:
        res = await self.client.images.generate(model=self.model, prompt=prompt, size="1024x1024")
        item = res.data[0]
        if getattr(item, "b64_json", None):
            data = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            async with httpx.AsyncClient(timeout=60) as http:
                data = (await http.get(item.url)).content
        else:
            raise RuntimeError("The image service returned nothing")
        name = f"{secrets.token_hex(8)}.png"
        (self.dir / name).write_bytes(data)
        return f"/media/{name}"


class LocalImages:
    """Free, local Stable Diffusion (SD-Turbo by default) via diffusers.

    The pipeline is heavy to load, so it's built once on first use and reused.
    Generation runs in a worker thread (it's CPU/GPU-bound and blocking) and is
    serialised with a lock — one picture at a time keeps memory sane on a laptop.
    """
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

    def _generate(self, prompt: str) -> str:
        if self._pipe is None:
            self._pipe = self._load()
        # SD-Turbo is built for very few steps with no classifier-free guidance.
        image = self._pipe(
            prompt=prompt,
            num_inference_steps=self.steps,
            guidance_scale=0.0,
            height=self.size,
            width=self.size,
        ).images[0]
        name = f"{secrets.token_hex(8)}.png"
        image.save(self.dir / name)
        return f"/media/{name}"

    async def create(self, prompt: str) -> str:
        async with self._lock:                     # one at a time — laptop memory
            return await asyncio.to_thread(self._generate, prompt)


class Speech:
    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key)
        self.s = s

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


def make_images(s: Settings):
    if s.image_provider == "local":
        return LocalImages(s)
    if s.image_provider == "openai" and s.openai_api_key:
        return Images(s)
    return None


def make_speech(s: Settings):
    if s.speech_provider == "local":
        from .speech_local import LocalSpeech
        return LocalSpeech(s)
    if s.speech_provider == "openai" and s.openai_api_key:
        return Speech(s)
    return None
