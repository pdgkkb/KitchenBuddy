"""Settings, read once from the environment (or backend/.env).

Every capability is optional. A missing key switches that one feature
off and /api/status says so — it never stops the server starting.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]          # the repository root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )

    llm_provider: str = "anthropic"                  # anthropic | openai | none
    llm_model: str = "claude-sonnet-5"
    anthropic_api_key: str = ""

    openai_api_key: str = ""
    openai_base_url: str = ""                        # e.g. http://localhost:11434/v1 for Ollama

    image_provider: str = "openai"                   # openai | local | none
    image_model: str = "gpt-image-1"                 # local: a HF id, e.g. stabilityai/sd-turbo

    # Local image generation (image_provider = local) — free, on-device via diffusers
    image_steps: int = 2                             # SD-Turbo wants 1–4
    image_size: int = 512                            # square px; 512 is fast, 768 sharper/slower
    image_device: str = "auto"                       # auto | mps | cuda | cpu
    image_count: int = 1                             # one main photo kept per recipe
    image_prefetch: bool = True                      # start them when a recipe is opened

    speech_provider: str = "openai"                  # openai | local | none

    # OpenAI voice (speech_provider = openai)
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "coral"
    stt_model: str = "gpt-4o-mini-transcribe"

    # Local voice (speech_provider = local) — see providers/speech_local.py
    whisper_model: str = "base"                      # base | small | distil-large-v3 | a local path
    whisper_device: str = "auto"                     # auto | cpu | cuda
    whisper_compute: str = "auto"                    # auto | int8 | int8_float16 | float16
    stt_language: str = ""                           # "" = auto-detect; or "en", "fr"

    tts_engine: str = "kokoro"                       # kokoro | piper
    kokoro_voice: str = "af_heart"                   # e.g. af_heart, af_bella, am_michael, ff_siwis (French)
    kokoro_lang: str = "a"                           # a=US Eng, b=UK Eng, f=French, e=Spanish, i=Italian…

    piper_voice: str = ""                            # path to a Piper .onnx voice (only if tts_engine=piper)
    piper_config: str = ""                           # path to its .onnx.json (default: <voice>.json)

    # Wake word (browser side reads this from /api/status)
    wake_word: str = "hey chef"

    # Answers written before you ask them (see app/prefetch.py)
    prefetch_enabled: bool = True
    prefetch_count: int = 4                          # questions parked per cooking step

    # Local retrieval over shared/kaiser_recipes.jsonl (SQLite FTS5, no service needed)
    rag_enabled: bool = True
    rag_source: str = ""                             # blank = shared/kaiser_recipes.jsonl
    rag_index: str = ""                              # blank = backend/media/recipe-rag.sqlite3
    rag_top_k: int = 4

    # Load the models at start-up, and stop Ollama unloading Qwen between
    # questions — usually the single largest delay in a cooking session.
    warm_models: bool = True
    llm_keep_alive: str = "1h"

    # Reading till receipts (see app/receipt.py)
    ocr_languages: str = "fra+eng"                   # tesseract language packs

    allowed_origins: str = "http://localhost:5173"
    media_dir: str = "media"

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        return p if p.is_absolute() else Path(__file__).resolve().parents[1] / p

    @property
    def rag_source_path(self) -> Path:
        return Path(self.rag_source) if self.rag_source else ROOT / "shared" / "kaiser_recipes.jsonl"

    @property
    def rag_index_path(self) -> Path:
        return Path(self.rag_index) if self.rag_index else self.media_path / "recipe-rag.sqlite3"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()