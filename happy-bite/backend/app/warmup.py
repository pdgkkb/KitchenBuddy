"""Loading the models before the first question instead of during it.

Every local model here is lazy: Whisper, Kokoro and SD-Turbo build themselves on
first use, and Ollama evicts a model from memory after five minutes idle. That
laziness is right — the server should start instantly — but it means the FIRST
thing you say costs the load time of the whole stack, which on a laptop is the
difference between "quick" and "broken".

So: at start-up, in the background, we touch each one once. Nothing here blocks
the server, nothing here is required, and every failure is a printed line rather
than an exception — a machine that can't warm up can still work, just slower.

`keep_alive` is the other half. Told to hold the model for an hour, Ollama stops
unloading Qwen between questions, which is usually the largest single delay in
a cooking session.
"""

from __future__ import annotations

import asyncio

import httpx

SILENT = "Say the single word: ready."


async def warm_llm(s) -> None:
    """One tiny generation, with a long keep_alive so it stays resident."""
    base = (getattr(s, "openai_base_url", "") or "").rstrip("/")
    if not base or getattr(s, "llm_provider", "") != "openai":
        return                       # cloud models have nothing to preload
    body = {
        "model": s.llm_model,
        "messages": [{"role": "user", "content": SILENT}],
        "max_tokens": 4,
        "stream": False,
        # Ollama reads this; other OpenAI-compatible servers ignore unknown keys.
        "keep_alive": getattr(s, "llm_keep_alive", "1h"),
    }
    key = getattr(s, "openai_api_key", "") or "none"
    try:
        async with httpx.AsyncClient(timeout=180) as http:
            r = await http.post(f"{base}/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {key}"})
        print("warmup: model", s.llm_model, "loaded" if r.status_code < 400 else
              f"replied {r.status_code}")
    except Exception as e:  # noqa: BLE001
        print("warmup: model not reachable yet —", type(e).__name__, e)


async def warm_speech(speech) -> None:
    if not speech:
        return
    try:
        if hasattr(speech, "warmup"):
            await speech.warmup()
        elif hasattr(speech, "say"):
            await speech.say("Ready.")
        print("warmup: voice ready")
    except Exception as e:  # noqa: BLE001
        print("warmup: voice not ready —", type(e).__name__, e)


async def warm_images(images) -> None:
    if not images or not hasattr(images, "warmup"):
        return
    try:
        await images.warmup()
        print("warmup: picture model ready")
    except Exception as e:  # noqa: BLE001
        print("warmup: picture model not ready —", type(e).__name__, e)


def start(app, s) -> None:
    """Fire all three off. Order matters a little: the chat model is what you
    notice first, the picture model is 2.5 GB and can take its time."""
    if not getattr(s, "warm_models", True):
        return

    async def run():
        await warm_llm(s)
        await asyncio.gather(
            warm_speech(getattr(app.state, "speech", None)),
            warm_images(getattr(app.state, "images", None)),
        )

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass
