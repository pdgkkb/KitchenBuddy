"""Settings, read once from the environment (or backend/.env).

Every capability is optional. A missing key switches that one feature
off and /api/status says so — it never stops the server starting."""

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
    openai_base_url: str = ""

    image_provider: str = "openai"                   # openai | none
    image_model: str = "gpt-image-1"

    speech_provider: str = "openai"                  # openai | none
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "coral"
    stt_model: str = "gpt-4o-mini-transcribe"

    allowed_origins: str = "http://localhost:5173"
    media_dir: str = "media"

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        return p if p.is_absolute() else Path(__file__).resolve().parents[1] / p

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
