"""Pictures and voice. Both optional; both OpenAI today.

With SPEECH_PROVIDER=none the browser uses its own voice for reading
aloud, and its own recogniser for listening — the frontend handles that
fallback, so nothing here needs to pretend."""

import base64
import secrets

import httpx

from ..config import Settings


class Images:
    def __init__(self, s: Settings):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=s.openai_api_key)
        self.model = s.image_model
        self.dir = s.media_path
        self.dir.mkdir(parents=True, exist_ok=True)

    async def create(self, prompt: str) -> str:
        """Generates, saves under MEDIA_DIR, returns the public path."""
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
    return Images(s) if s.image_provider == "openai" and s.openai_api_key else None


def make_speech(s: Settings):
    return Speech(s) if s.speech_provider == "openai" and s.openai_api_key else None
