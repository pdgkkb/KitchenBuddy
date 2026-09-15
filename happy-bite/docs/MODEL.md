# Changing the chef's brain

The model is one setting, `LLM_MODEL` in `backend/.env`, and everything else
follows it: `/api/status` reports it, `warmup.py` preloads it, and
`/api/health/models` now says whether the server can actually reach it.

## Moving to Qwen 3.5 9B

```ini
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=hf.co/unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL
LLM_KEEP_ALIVE=1h
LLM_THINK=false
```

`LLM_THINK` is new. Qwen 3.5 is a hybrid-reasoning family: the big models think
by default, the Small series (0.8B, 2B, 4B, **9B**) does not. Leaving it `false`
matches the 9B's own default and keeps answers short, which is what a person at
the hob wants. Either way the server strips any `<think>…</think>` block before
the text reaches the browser or the JSON parser — a reasoning block full of
braces would otherwise break recipe parsing outright.

`LLM_TEMPERATURE` / `LLM_TOP_P` default to 0.7 / 0.8, which is Qwen's own
recommendation for non-thinking mode. With `LLM_THINK=true`, use 1.0 / 0.95.

### Before you switch, check Ollama can load it

**Qwen 3.5 GGUFs do not load in older Ollama.** The repository ships the text
and vision weights as separate GGUF files, and Ollama's bundled llama.cpp did
not know the `qwen35` architecture, so a pull succeeds and the first request
returns `500 unable to load model`. Fixed from Ollama **v0.30.x**, which serves
through llama.cpp's own `llama-server`.

So, in order:

```bash
ollama --version                                          # want 0.30 or newer
ollama run hf.co/unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL       # say hello to it
```

If that errors, you have three ways forward, none of which need the app to
change — only `LLM_MODEL` and `OPENAI_BASE_URL`:

1. **Upgrade Ollama.** The simplest fix.
2. **Text-only Modelfile.** Download just the text GGUF and
   `ollama create qwen35-9b -f Modelfile` with `FROM ./<file>.gguf`. Happy Bite
   never sends images to the language model, so nothing is lost.
3. **LM Studio or llama-server.** Both speak the same OpenAI protocol; point
   `OPENAI_BASE_URL` at them (`http://localhost:1234/v1` for LM Studio) and
   leave everything else alone.

`curl localhost:8000/api/health/models` tells you which of these you're in: the
`chat` block now carries `reachable` and, when it isn't, the server's own words
for why.

## What it costs

| | Qwen 2.5 7B Q4 | Qwen 3.5 9B UD-Q4_K_XL |
|---|---|---|
| Weights on disk | ~4.4 GB | ~6.5 GB |
| Resident while cooking | ~5 GB | ~7.5 GB |
| First token, cold | slow | slower |

On a 16 GB Mac that is comfortable on its own and tight beside SD-Turbo
(~2.5 GB) and Whisper. If pictures start failing after the switch, that is why:
either `IMAGE_PROVIDER=none` while you cook, or drop `IMAGE_COUNT` to 1.

`LLM_KEEP_ALIVE=1h` stops Ollama evicting 6.5 GB after five minutes idle and
paying the load again mid-recipe. It matters more at 9B than it did at 7B.


## When the model thinks instead of cooking

Qwen 3.5 is a hybrid-reasoning model and **this build has thinking ON by
default**. Left on, it writes a long private monologue before the answer. In
this kitchen that is not a small tax:

```
[qwen3.5-9b-mlx] Done reasoning. Reasoned for 1553.34 seconds.
```

Twenty-six minutes, for a recipe. Nothing else in the stack matters next to
that number: not memory, not prompt size, not the framework.

### Two places it can be switched off

**The request.** The app sends
`chat_template_kwargs: {"enable_thinking": false}`, which is the documented way
and works against Ollama and llama.cpp. If the server refuses it with a 400,
the backend log says so in one line and shouts that the switch was lost.

**The server.** If it refuses the field, only the server's own settings can do
it — LM Studio has a reasoning toggle per loaded model, and a `model.yaml` with
`enableThinking: false` gives you a switch in its UI. Turn it off there.

### How to tell which is happening

The backend log on the first request after a restart:

```
llm: … answered 400 — dropping keep_alive and asking again      <- fine
llm: !! that was the thinking switch                            <- not fine
```

And LM Studio's own log prints `Done reasoning. Reasoned for N seconds` every
time it reasons. That N is your whole wait.
