"""How big is the model's context window — the one it was LOADED with?

The model card says 128K. LM Studio loads it at whatever its slider says, Ollama
at num_ctx, and only that number decides whether a prompt fits. Everything that
sizes a prompt (the ingredient-id list in api.ids_for, the token arithmetic in
providers/llm.py) needs it, and until now it only had it if someone had copied
it into LLM_CONTEXT by hand. Nobody did — so the whole catalogue, 3,000
ingredients and ~34K tokens, went to a model loaded at 8,192, and the server
answered "The number of tokens to keep from the initial prompt is greater than
the context length".

So it is asked for. In order:

    1. LLM_CONTEXT                 set by hand: always wins
    2. the server itself           LM Studio: /api/v0/models/<id> → loaded_context_length
                                   Ollama:    /api/ps → context_length of the loaded model
    3. LLM_NUM_CTX                 what Ollama was told to load with
    4. 4096 for a local server that won't say — LM Studio's and Ollama's own
       defaults; guessing big is how this failure happened
    5. 0 ("no limit") for a cloud API, where windows are large

`refresh()` is async and cheap (cached for 20 s); the endpoints that build a
prompt call it first. `current()` is the synchronous read everything else uses.
"""

from __future__ import annotations

import time
from urllib.parse import quote, urlsplit

import httpx

TTL = 20.0
LOCAL_FALLBACK = 4096
# A local model can be loaded with a huge window (LM Studio loads gemma-4-e2b at
# 64,768) but every token of prompt is read on a laptop GPU before the first word
# comes back. Prompt budgets are sized as if the window were at most this, unless
# LLM_CONTEXT says otherwise.
LOCAL_PROMPT_CEILING = 8192

_state = {"at": 0.0, "value": None, "source": ""}


def _local_root(s) -> str | None:
    """scheme://host:port of an OpenAI-compatible server on this machine or LAN."""
    if getattr(s, "llm_provider", "") != "openai" or not getattr(s, "openai_base_url", ""):
        return None
    parts = urlsplit(s.openai_base_url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def current(s) -> int:
    """The best known window, in tokens. 0 means no limit is needed."""
    explicit = int(getattr(s, "llm_context", 0) or 0)
    if explicit:
        return explicit
    if _state["value"] is not None:
        return _state["value"]
    if not _local_root(s):
        return 0
    return int(getattr(s, "llm_num_ctx", 0) or 0) or LOCAL_FALLBACK


def source() -> str:
    return _state["source"] or "not checked yet"


async def refresh(s) -> int:
    """Ask the server what it actually loaded. Never raises; never slow."""
    if int(getattr(s, "llm_context", 0) or 0):
        _set(int(s.llm_context), "LLM_CONTEXT")
        return _state["value"]
    root = _local_root(s)
    if not root:
        _set(0, "cloud API")
        return 0
    if _state["value"] is not None and time.monotonic() - _state["at"] < TTL:
        return _state["value"]

    found, where = None, ""
    model = getattr(s, "llm_model", "")
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            try:                                                        # LM Studio
                r = await client.get(f"{root}/api/v0/models/{quote(model, safe='')}")
                if r.status_code == 200:
                    found = int(r.json().get("loaded_context_length") or 0) or None
                    where = "LM Studio"
            except (httpx.HTTPError, ValueError):
                pass
            if not found:
                try:                                                    # Ollama
                    r = await client.get(f"{root}/api/ps")
                    if r.status_code == 200:
                        for m in r.json().get("models", []):
                            if model in (m.get("name"), m.get("model")):
                                found = int(m.get("context_length") or 0) or None
                                where = "Ollama"
                except (httpx.HTTPError, ValueError):
                    pass
    except Exception:  # noqa: BLE001 — a probe must never break a request
        pass

    if found and found > LOCAL_PROMPT_CEILING:
        _set(LOCAL_PROMPT_CEILING, f"{where} loaded {found}; prompts sized for {LOCAL_PROMPT_CEILING} "
                                   "so a laptop doesn't read them for long")
    elif found:
        _set(found, f"reported by {where}")
    elif int(getattr(s, "llm_num_ctx", 0) or 0):
        _set(int(s.llm_num_ctx), "LLM_NUM_CTX")
    else:
        _set(LOCAL_FALLBACK, "assumed — the server didn't say (model not loaded yet?)")
    return _state["value"]


def _set(value: int, where: str) -> None:
    if value != _state["value"] or where != _state["source"]:
        print(f"llm: context window {value or 'unlimited'}"
              + (" tokens" if value else "") + f" ({where})")
    _state.update(at=time.monotonic(), value=value, source=where)
