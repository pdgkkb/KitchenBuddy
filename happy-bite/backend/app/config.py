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

    # Qwen 3.5 and its relatives are hybrid-reasoning models: they can write a
    # <think> block before the answer. The Small series (0.8B-9B) has it off by
    # default and that is what we want — a person at the hob wants the answer,
    # not the working. Sent as chat_template_kwargs, which Ollama and llama.cpp
    # read and every other OpenAI-compatible server ignores.
    llm_think: bool = False
    llm_temperature: float = 0.7                     # Qwen's own non-thinking recommendation
    llm_top_p: float = 0.8                           # with thinking on, use 1.0 / 0.95
    llm_num_ctx: int = 0                             # 0 = leave the server's own default alone

    # One attempt, generously long. The default client retries twice, so a
    # model that needs four minutes looked like a twelve-minute hang before it
    # admitted defeat — the user watched 437 seconds of that.
    llm_timeout: float = 600.0

    # ---- the context window, which is where "it won't fit" comes from ----
    #
    # The window the model was LOADED with, in tokens — not the one its model
    # card advertises. Llama 3.2 1B is a 128K model that LM Studio will happily
    # load at 4096, and it is the 4096 that decides whether a request is
    # possible. Prompt and answer share it.
    #
    # Leave it 0 and the server is the one that finds out, which it reports as
    # "The number of tokens to keep from the initial prompt is greater than the
    # context length" — true, unhelpful, and after you have already waited. Set
    # it to what you loaded and the app does the arithmetic first and tells you
    # which number to change.
    llm_context: int = 0
    llm_reserve: float = 0.35                        # share of the window kept for the answer

    # A character ceiling on the ingredient-id list pasted into recipe prompts.
    # 0 sends the whole catalogue, which is right on a large context and ruinous
    # on a small one: a few hundred entries is well over two thousand tokens
    # before the model has written anything. Trimming keeps what is in the
    # kitchen and the seasonings, and drops the rest — which is safe, because
    # ingredients with no id go into the recipe as plain text anyway.
    #
    # Rough guide: 2500 for a 4096-token window, 6000 for 8192, 0 above that.
    llm_ids_chars: int = 0
    # The same ceiling for the chef CHAT only. A chat turn rarely needs more than
    # the kitchen and the seasonings, and on a small local model every character
    # is re-read before the reply. Recipe writing keeps the larger list: with
    # 4,000 characters gemma-4-e2b started returning recipes with no ingredients.
    llm_chat_ids_chars: int = 4000

    # NOTE: `llm_num_ctx` above is Ollama's own name for the same window, sent
    # as options.num_ctx. Ollama reads it and loads the model that way; LM
    # Studio ignores it (use the slider in the app). Setting it does NOT tell
    # this app anything — set `llm_context` to the same number as well, or the
    # arithmetic above is skipped.

    # Send the non-standard fields (thinking mode, keep_alive, num_ctx) at all.
    # Ollama reads them. LM Studio VALIDATES the request body and answers 400 —
    # which is what "BadRequestError" was. The code now drops them by itself on
    # a 400, so this is a manual override rather than something you should need.
    llm_extras: bool = True

    # The floor on how much room a structured reply gets. A full recipe is
    # roughly 700 tokens of JSON; the old 1800 was comfortable until a reasoning
    # model started spending the first thousand on its own deliberations and the
    # recipe ran out of room halfway through step six.
    llm_json_tokens: int = 3000

    # ---- structured output: the three settings that decide whether a recipe
    # ---- comes back as JSON or as something that merely looks like it.
    #
    # Ask the server to CONSTRAIN generation to the schema. Ollama, llama.cpp
    # and LM Studio all accept response_format json_schema and will then only
    # emit tokens that keep the JSON valid — which makes `"minutes": 12 "` and
    # every other stray-punctuation failure impossible rather than unlikely.
    # A server that refuses it answers 400 and the code steps down by itself,
    # so leave this on; turn it off only to prove a server is the problem.
    llm_json_schema: bool = True

    # Structured output is not chat. 0.7 with top_p 0.8 asks a small model to be
    # creative about where the commas go. Recipes come back better AND more
    # reliably at 0.2 — the creativity that matters is in the dish, and that is
    # decided by the prompt and the corpus, not by the sampler.
    llm_json_temperature: float = 0.2

    # One automatic retry at temperature 0 when the JSON doesn't parse AND the
    # model stopped by itself (a truncation or a timeout will just do the same
    # thing again, so those are never retried). Costs another pass on a local
    # model; turns most single-character mistakes into a working recipe.
    llm_json_retry: bool = True

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
    # MLX runs on the GPU against unified memory; faster-whisper and torch
    # Kokoro run on the CPU. auto tries MLX and falls back, saying so.
    speech_backend: str = "auto"                     # auto | mlx | torch
    stt_engine: str = "parakeet"                     # parakeet | whisper  (MLX only)
    parakeet_model: str = ""                         # default: mlx-community/parakeet-tdt-0.6b-v3
    mlx_whisper_model: str = ""                      # default: mlx-community/whisper-small-mlx
    kokoro_mlx_repo: str = ""                        # default: mlx-community/Kokoro-82M-bf16

    whisper_model: str = "base"                      # base | small | distil-large-v3 | a local path
    whisper_device: str = "auto"                     # auto | cpu | cuda
    whisper_compute: str = "auto"                    # auto | int8 | int8_float16 | float16
    stt_language: str = ""                           # "" = auto-detect; or "en", "fr"

    # Speaking is a CHAIN, not a choice — see providers/speech_local.py. This
    # names the FIRST engine to try; if it won't load, or loads and then can't
    # produce a sound (Kokoro without `misaki` does exactly that), the next one
    # takes over mid-call and says so in the log.
    #
    #   kokoro   Kokoro on MLX, then Kokoro on torch, then Piper, then the system voice
    #   piper    Piper first
    #   say      the system voice (macOS `say`, Windows System.Speech), deliberately —
    #            no models, always available. Not as good. Infinitely better than silence.
    tts_engine: str = "kokoro"                       # kokoro | piper | say
    kokoro_voice: str = "af_heart"                   # e.g. af_heart, af_bella, am_michael, ff_siwis (French)
    kokoro_lang: str = "a"                           # a=US Eng, b=UK Eng, f=French, e=Spanish, i=Italian…

    # A system voice name. macOS: `say -v ?` lists them ("Thomas" and "Amélie" are
    # French). Windows: the installed voice's full name, e.g. "Microsoft Zira Desktop".
    # Blank uses the system default.
    say_voice: str = ""

    piper_voice: str = ""                            # path to a Piper .onnx voice (only if tts_engine=piper)
    piper_config: str = ""                           # path to its .onnx.json (default: <voice>.json)

    # Wake word (browser side reads this from /api/status)
    wake_word: str = "hey chef"

    # Answers written before you ask them (see app/prefetch.py).
    #
    # MEASURE THIS ON YOUR OWN MACHINE. Each step costs one model call of about
    # 900 tokens. At the 15 tokens a second this Mac manages that is a minute of
    # GPU per step, in the background, while you are also asking questions at
    # the hob — and two jobs racing on one local model makes both slower. If the
    # prefetch for a step doesn't finish before you reach it, set
    # PREFETCH_ENABLED=false: an honest two-second wait beats a machine that is
    # permanently busy writing answers nobody asked for.
    prefetch_enabled: bool = True
    prefetch_count: int = 3                          # questions parked per cooking step

    # Cooking mode talks to the model differently from the chat screen: a short
    # prompt built for the hob (prompts.cook_system), streamed so the first
    # sentence is spoken while the rest is written, and capped in length.
    cook_answer_tokens: int = 220                    # one or two spoken sentences, with room
    # Kitchen tools (stock, timers) in cooking mode. Off by default: small local
    # models don't call them — gemma-4-e2b narrates "I'll set a timer" and calls
    # nothing — yet every question paid ~1,900 tokens of tool schemas and lost
    # streaming. Timers and step controls are recognised in the browser instead
    # (lib/command.js). Turn on for a model that really uses tools (Claude, a 7B+).
    cook_tools: bool = False

    # Local retrieval over shared/kaiser_recipes.jsonl (SQLite FTS5, no service needed)
    rag_enabled: bool = True
    rag_source: str = ""                             # blank = shared/kaiser_recipes.jsonl
    rag_index: str = ""                              # blank = backend/media/recipe-rag.sqlite3
    rag_top_k: int = 4

    # Using a corpus recipe as a TEMPLATE instead of writing a new one (see
    # app/template.py). Writing a dish from nothing is the most expensive thing
    # this server asks of a local model; converting one that already fits the
    # kitchen is a fraction of the work and invents nothing.
    #
    # `template_floor` is how much of a candidate's ingredient list has to be in
    # the kitchen before it counts as "already yours". Below about 0.6 you start
    # getting recipes that need a shopping trip; at 1.0 almost nothing ever
    # qualifies, because every real recipe names salt or water.
    template_enabled: bool = True
    template_floor: float = 0.72
    template_candidates: int = 8                     # corpus hits scored per request

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