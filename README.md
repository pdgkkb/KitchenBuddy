# Happy Bite (KitchenBuddy) — macOS

> **On Windows?** Follow [README-Windows.md](README-Windows.md) instead.

An offline-first kitchen assistant. It tracks what is in your kitchen and what is
about to go off, suggests tonight's dinner from what you already have, and — with
the AI layer switched on — a chef talks you through cooking it, writes and adapts
recipes, imports recipes from websites and listens and speaks hands-free.

React (Vite) in the browser, Python (FastAPI) on the server. Every model can run
locally on an Apple Silicon Mac: no cloud account is required.

---

## Contents

1. [How it is built](#how-it-is-built)
2. [Quick start](#quick-start)
3. [The chat model: LM Studio or Ollama](#the-chat-model-lm-studio-or-ollama)
4. [Configuration (`backend/.env`)](#configuration-backendenv)
5. [Features](#features)
6. [Voice](#voice)
7. [Recipe photos](#recipe-photos)
8. [Recipe corpus, templates and the catalogue](#recipe-corpus-templates-and-the-catalogue)
9. [Training your own model](#training-your-own-model)
10. [Diagnostic tools](#diagnostic-tools)
11. [Project layout](#project-layout)
12. [Troubleshooting](#troubleshooting)
13. [Known limitations](#known-limitations)

---

## How it is built

**CORE works offline and never calls a model. EXTRA is the AI layer and degrades
gracefully when it is off.**

- **CORE** — tonight's dish, stock, expiry dates, the shopping list, receipts.
  Runs entirely in the browser and stores everything in IndexedDB. With the
  backend stopped, CORE still works.
- **EXTRA** — chef chat, cooking mode, recipe writing, link import, receipt
  reading, photos, voice. Needs the backend. Each capability is independent: if
  one is not configured, the app hides it and `/api/status` says why.

The server is **stateless**. Your household data lives in the browser, so when you
say *"I used 100 g of milk"* the flow is:

```
you speak ─▶ /api/chat ─▶ model emits a tool call
                              │
               actions.normalize  (validated against the catalogue)
                              │
        SSE {type:"action"} ──▶ useApplyAction ──▶ kitchen.jsx ──▶ IndexedDB + UI
```

The assistant can therefore only do what you could already do by tapping. Model
output is always treated as untrusted input: recipes pass `clean_recipe`,
actions pass `actions.normalize`, equipment adaptations pass `clean_adaptation`.
Invented ids, impossible quantities and malformed steps are dropped, not repaired.

---

## Quick start

### Requirements

| | Install | Why |
|---|---|---|
| **Python 3.12** (or 3.11) | `brew install python@3.12` | torch, kokoro and diffusers publish no wheels for 3.13/3.14 |
| **Node 18+** | `brew install node` | the frontend |
| **A model server** | [LM Studio](https://lmstudio.ai) *or* [Ollama](https://ollama.com) | the chat model — see [below](#the-chat-model-lm-studio-or-ollama) |
| ffmpeg, espeak-ng | `brew install ffmpeg espeak-ng` | local voice |
| Tesseract | `brew install tesseract tesseract-lang` | optional, receipt reading |

Apple Silicon (M1 or newer) is assumed: the voice runs on MLX, Apple's GPU
framework. An Intel Mac works too, with voice on the CPU (`SPEECH_BACKEND=torch`).

### 1. Backend

```bash
cd happy-bite/backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-mac.txt
./setup_local.sh            # optional: checks the local model packages and tools
# create backend/.env — see "Configuration" below
./run.sh                    # http://127.0.0.1:8000
```

`./run.sh` always runs the venv's own Python (a `uvicorn` on your PATH may belong
to another interpreter that cannot see your packages), stops anything already
holding port 8000, and lists missing packages.

| Command | Use it when |
|---|---|
| `./run.sh` | editing backend code — restarts on every save, which reloads the voice models (~9 s) |
| `./run.sh --no-reload` | cooking — no surprise restarts |

### 2. Model server

Start **one** of LM Studio or Ollama with a model loaded — see the next section.

### 3. Frontend

```bash
cd happy-bite/frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

Day to day that is **three things running**: the model server, the backend, the
frontend.

### One address for a kitchen tablet

```bash
cd happy-bite/frontend && npm run build
```

When `frontend/dist` exists, the backend serves the app, the API and the photos
together on port 8000 — no Vite needed.

### Check it

```bash
open http://localhost:8000/api/health/models    # every model: configured, loaded, reachable, and why not
curl http://localhost:8000/api/status           # what the browser sees
```

---

## The chat model: LM Studio or Ollama

Both are free apps that run a language model on your machine and expose it with
the same OpenAI-style API. The backend talks to **whichever one
`OPENAI_BASE_URL` points at** and nothing else, so:

- **Run one, not both.** The other isn't used.
- Running both costs little while the unused one has no model loaded. Once it
  loads one, that model holds several GB of the memory the GPU, the chat model and
  the voice all share, and everything slows down.

### LM Studio — recommended on a Mac

A desktop app with a model browser. On Apple Silicon it runs **MLX** models, made
for the Mac's GPU, as well as GGUF.

1. Install from [lmstudio.ai](https://lmstudio.ai) and open it.
2. **Discover** → search for a model → download an **MLX** build
   (e.g. `llama-3.2-1b-instruct-mlx`, or a larger Qwen for better recipes).
3. **Developer** → load the model → **Start Server** (port 1234).
4. In the loaded model's settings:
   - **Context length** — e.g. 8192. Put the same number in `LLM_CONTEXT`.
   - **Idle unload / auto-evict** — off, or a long time, so the model stays loaded
     between questions.
   - **Reasoning / thinking** — off for reasoning models.
5. The model's exact id: `curl http://localhost:1234/v1/models`.

```ini
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:1234/v1
LLM_MODEL=llama-3.2-1b-instruct-mlx
LLM_CONTEXT=8192
LLM_THINK=false
```

### Ollama

A command-line server. Runs GGUF models through llama.cpp on the Mac's GPU.

```bash
brew install ollama            # or the app from ollama.com
ollama serve                   # not needed if the Ollama app is already running
ollama pull qwen2.5:3b-instruct
```

```ini
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b-instruct
LLM_NUM_CTX=8192               # Ollama loads the model with this window...
LLM_CONTEXT=8192               # ...and this tells the app the same number
LLM_KEEP_ALIVE=1h
LLM_THINK=false
```

### Side by side

| | LM Studio | Ollama |
|---|---|---|
| Interface | desktop app | command line (+ menu-bar app) |
| Default port | 1234 | 11434 |
| Model formats on a Mac | **MLX** and GGUF | GGUF |
| Context window | set in the app | `LLM_NUM_CTX` from `.env` |
| Keep model loaded | set in the app | `LLM_KEEP_ALIVE` from `.env` |
| Thinking switch from the app | ignored (400) — turn off in the app | honoured |

**Which is faster?** On Apple Silicon, MLX models in LM Studio are usually faster
than the same model as GGUF, but it depends on the model and chip. Measure yours:
`python3 check_model.py --url http://localhost:1234/v1` and again with
`http://localhost:11434/v1`. **Model size matters far more than the server**: a 1B
model answers several times faster than a 7–9B one and writes weaker recipes.

### Thinking models

Hybrid-reasoning models (Qwen 3.5 etc.) can write a long private monologue before
answering — one recipe was measured at **26 minutes** of reasoning. Keep
`LLM_THINK=false`. The app sends `chat_template_kwargs: {"enable_thinking": false}`
and strips any `<think>…</think>` before parsing. LM Studio rejects that field; the
backend drops it and logs:

```
llm: <model> answered 400 — dropping <fields> and asking again   <- fine
llm: !! that was the thinking switch. …                          <- turn thinking off in LM Studio
```

LM Studio's own log prints `Done reasoning. Reasoned for N seconds` — that N is
your wait.

### Context window

What matters is the window the model was **loaded** with, not what the model
supports (LM Studio often loads at 4096). Set `LLM_CONTEXT` to that number so the
app can check a prompt fits before sending and tell you which setting to change.
On a small window also set `LLM_IDS_CHARS` (≈2500 for 4K, 6000 for 8K).

### Bigger model: Qwen 3.5 9B on Ollama

```ini
LLM_MODEL=hf.co/unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_KEEP_ALIVE=1h
```

Needs Ollama **0.30+** (older versions fail with `500 unable to load model`).
~6.5 GB on disk, ~7.5 GB in memory: comfortable alone on a 16 GB Mac, tight next to
local pictures.

---

## Configuration (`backend/.env`)

Everything is optional. A blank or missing setting switches that one feature off;
the server still starts. All settings and their defaults are in
[`happy-bite/backend/app/config.py`](happy-bite/backend/app/config.py).

### Fully local

```ini
# Chat — see the LM Studio / Ollama section for the server-specific lines
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:1234/v1
LLM_MODEL=llama-3.2-1b-instruct-mlx
LLM_CONTEXT=8192
LLM_THINK=false

# Pictures — off while cooking, made by tools/make_photos.py instead
IMAGE_PROVIDER=none

# Voice
SPEECH_PROVIDER=local
SPEECH_BACKEND=auto                           # MLX on the GPU, falls back to CPU
STT_ENGINE=parakeet                           # parakeet | whisper
STT_LANGUAGE=en
TTS_ENGINE=kokoro
KOKORO_VOICE=af_heart
KOKORO_LANG=a                                 # a US, b UK, f French, e Spanish, i Italian
```

### Cloud instead

```ini
LLM_PROVIDER=anthropic          # or openai (no OPENAI_BASE_URL)
ANTHROPIC_API_KEY=...
LLM_MODEL=claude-sonnet-5
IMAGE_PROVIDER=openai           # gpt-image-1
SPEECH_PROVIDER=openai          # gpt-4o-mini-tts / gpt-4o-mini-transcribe
```

Only the question and your stock list (names and quantities, never who lives in
the house) leave the machine.

### Reference

| Setting | Default | What it does |
|---|---|---|
| **Chat** | | |
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `none` |
| `LLM_MODEL` | `claude-sonnet-5` | model id as the server knows it |
| `OPENAI_BASE_URL` | — | point `openai` at LM Studio or Ollama |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | cloud keys (not needed for a local server) |
| `LLM_THINK` | `false` | allow `<think>` reasoning |
| `LLM_TEMPERATURE` / `LLM_TOP_P` | `0.7` / `0.8` | chat sampling |
| `LLM_TIMEOUT` | `600` | seconds, single attempt |
| `LLM_CONTEXT` | `0` | the context window the model was **loaded** with |
| `LLM_NUM_CTX` | `0` | Ollama only: the window Ollama loads the model with |
| `LLM_RESERVE` | `0.35` | share of the window kept for the answer |
| `LLM_IDS_CHARS` | `0` | cap on the ingredient-id list in recipe prompts |
| `LLM_CHAT_IDS_CHARS` | `4000` | the same cap for the chef chat, kept small so replies start sooner |
| `LLM_EXTRAS` | `true` | send `keep_alive`, `num_ctx`, thinking switch (dropped automatically on a 400) |
| `LLM_KEEP_ALIVE` | `1h` | Ollama only: keep the model loaded |
| **Structured output (recipes)** | | |
| `LLM_JSON_TOKENS` | `3000` | minimum reply budget for a recipe |
| `LLM_JSON_SCHEMA` | `true` | constrain generation to the JSON schema |
| `LLM_JSON_TEMPERATURE` | `0.2` | sampling for JSON replies |
| `LLM_JSON_RETRY` | `true` | one retry at temperature 0 if the JSON doesn't parse |
| **Recipes from the corpus** | | |
| `RAG_ENABLED` | `true` | retrieve similar corpus recipes before writing |
| `RAG_SOURCE` / `RAG_INDEX` | `shared/kaiser_recipes.jsonl` / `media/recipe-rag.sqlite3` | |
| `RAG_TOP_K` | `4` | |
| `TEMPLATE_ENABLED` | `true` | convert an existing corpus recipe instead of writing one |
| `TEMPLATE_FLOOR` | `0.72` | share of a recipe's ingredients you must have |
| `TEMPLATE_CANDIDATES` | `8` | corpus hits scored per request |
| **Cooking mode** | | |
| `PREFETCH_ENABLED` | `true` | pre-answer likely questions per step (a model call per step) |
| `PREFETCH_COUNT` | `3` | questions per step; background jobs pause while a question is answered |
| `COOK_ANSWER_TOKENS` | `220` | length cap on a spoken answer in cooking mode |
| `COOK_TOOLS` | `false` | kitchen tools in cooking mode (on only for models that really call tools); timers are recognised in the browser |
| `WARM_MODELS` | `true` | load models at start-up |
| **Voice** | | |
| `SPEECH_PROVIDER` | `openai` | `openai` \| `local` \| `none` (none = browser speech) |
| `SPEECH_BACKEND` | `auto` | `auto` \| `mlx` \| `torch` |
| `STT_ENGINE` | `parakeet` | `parakeet` \| `whisper` (MLX) |
| `PARAKEET_MODEL` / `MLX_WHISPER_MODEL` / `KOKORO_MLX_REPO` | mlx-community defaults | override the MLX weights |
| `WHISPER_MODEL` / `WHISPER_DEVICE` / `WHISPER_COMPUTE` | `base` / `auto` / `auto` | faster-whisper (CPU fallback) |
| `STT_LANGUAGE` | — | blank = auto-detect |
| `TTS_ENGINE` | `kokoro` | first engine in the speaking chain: `kokoro` \| `piper` \| `say` |
| `KOKORO_VOICE` / `KOKORO_LANG` | `af_heart` / `a` | |
| `SAY_VOICE` | — | system voice name (`say -v ?` lists them) |
| `PIPER_VOICE` / `PIPER_CONFIG` | — | path to a Piper `.onnx` voice |
| `WAKE_WORD` | `hey chef` | |
| **Pictures** | | |
| `IMAGE_PROVIDER` | `openai` | `openai` \| `local` \| `none` |
| `IMAGE_MODEL` | `gpt-image-1` | local: e.g. `stabilityai/sd-turbo` |
| `IMAGE_STEPS` / `IMAGE_SIZE` / `IMAGE_DEVICE` | `2` / `512` / `auto` | local generation |
| `IMAGE_COUNT` | `1` | photos kept per recipe |
| `IMAGE_PREFETCH` | `true` | queue photos when a recipe is opened |
| **Other** | | |
| `OCR_LANGUAGES` | `fra+eng` | tesseract language packs for receipts |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS, comma-separated |
| `MEDIA_DIR` | `media` | photos, queues and indexes (relative to `backend/`) |

---

## Features

### The five screens

**Today** (tonight's dish), **Kitchen** (stock and expiry), **Recipes** (seed
recipes plus your saved ones), **Receipt**, **Shopping** — plus the chef chat and
cooking mode on top.

### Chef chat and actions

The chef can change your kitchen through tools, validated in
[`backend/app/actions.py`](happy-bite/backend/app/actions.py) and executed by
[`frontend/src/state/actions.jsx`](happy-bite/frontend/src/state/actions.jsx):
`adjust_stock`, `add_stock`, `set_expiry`, `add_custom_ingredient`, `save_recipe`,
`add_to_shopping`, `set_timer`, `open_screen`.

Try: *"I bought 300 g of chicken"*, *"I used 100 ml of milk and 2 eggs"*, *"the
yoghurt is good for 2 more days"*, *"put rice on the shopping list"*, *"set a timer
for 12 minutes"*.

- An ingredient not in the catalogue is created on the spot, and every later chat
  turn tells the model about your custom items.
- A category that doesn't exist yet is created too, and appears in Kitchen
  immediately.
- The prompt explicitly separates *bought* (add) from *used* (subtract).

### Recipe writing

1. **Template first.** [`app/template.py`](happy-bite/backend/app/template.py)
   looks for a corpus recipe whose ingredients you mostly already have
   (`TEMPLATE_FLOOR`) and converts it to the app's format, scaled to the people
   eating. Converting is much cheaper for a local model than inventing.
2. **Otherwise write**, with similar corpus recipes retrieved as reference
   ([`app/rag.py`](happy-bite/backend/app/rag.py), SQLite FTS5, no extra service).
3. Either way the result passes `clean_recipe`.

`POST /api/recipes/generate/stream` streams progress so the UI can show what the
model is doing.

### Cooking mode

One step at a time, large, with heat and what-to-watch-for beside it. With local
voice on it listens continuously:

- **"next" / "back" / "repeat"**, **"start a timer"**, or any question — answered
  aloud in a sentence or two.
- **"stop chef"** stops talking, **"stop timer"** cancels a timer, **"stop
  cooking"** ends the session.
- **Answers before you ask:** for the current and next step, the server
  pre-generates likely questions and answers
  ([`app/prefetch.py`](happy-bite/backend/app/prefetch.py)); a close match is
  answered instantly with no model call. The match bar is deliberately high — a
  confidently wrong answer at the hob is worse than a short wait.
- **Speaks as it writes:** [`lib/speechqueue.js`](happy-bite/frontend/src/lib/speechqueue.js)
  sends each finished sentence to the voice while the rest is still streaming,
  and marks it spoken so the full reply isn't read twice.

If prefetch for a step doesn't finish before you reach it (a slow local model),
set `PREFETCH_ENABLED=false`.

### Receipts

[`app/receipt.py`](happy-bite/backend/app/receipt.py): **tesseract** reads the
photo into text, then the chat model maps lines like `PLT FERM X6` to catalogue
items. No vision model needed. Unknown ids land in "needs a look", prices are not
mistaken for quantities, and corrections you have made before are applied first.

> **Not connected in the UI yet.** `POST /api/receipt/read` works, but the Receipt
> tab's `onFile` in [`screens/Receipt.jsx`](happy-bite/frontend/src/screens/Receipt.jsx)
> still replays the sample receipt.

### Importing from a website

[`app/importer.py`](happy-bite/backend/app/importer.py) reads the page's
schema.org Recipe (JSON-LD) first and only falls back to the visible text through
the model. The server refuses private addresses (SSRF guard).

### Cooking with the equipment you have

- [`core/equipment.js`](happy-bite/frontend/src/core/equipment.js) — sixteen
  appliances and a detector that reads each step (`heat` is the strongest signal).
  Works offline.
- [`app/adapt.py`](happy-bite/backend/app/adapt.py) — `POST /api/recipes/adapt`
  rewrites a recipe for your kit; `clean_adaptation` drops steps outside the recipe
  and equipment you don't own.
- [`components/AdaptPanel.jsx`](happy-bite/frontend/src/components/AdaptPanel.jsx)
  — the panel inside a recipe.

Equipment is `null` until someone opens the equipment sheet, and null means "not
said", not "owns nothing" — nothing is flagged until then. `possible` is `yes`,
`partly` or `no`; a `yes` that changes no steps is rejected. Answers are cached
under `recipeId::<sorted equipment ids>`, so buying an air fryer invalidates them.

### API

| Route | |
|---|---|
| `GET /api/status` | which features are on, and why not |
| `GET /api/health/models` | every model's state in detail |
| `POST /api/chat` | chef chat (SSE, with `action` events) |
| `POST /api/understand` | intent parsing |
| `POST /api/recipes/generate`, `…/generate/stream` | write a recipe |
| `POST /api/recipes/import` | import from a URL |
| `POST /api/recipes/adapt` | adapt to your equipment |
| `POST /api/recipes/images`, `GET`/`DELETE /api/recipes/images/{id}` | recipe photos |
| `POST /api/images` | one-off image |
| `POST /api/cook/prefetch`, `POST /api/cook/quick` | cooking-mode answers |
| `POST /api/receipt/read` | read a receipt photo |
| `POST /api/speech/say`, `POST /api/speech/hear` | text-to-speech, speech-to-text |

---

## Voice

| Job | Default | Fallback |
|---|---|---|
| Hearing | Parakeet TDT 0.6B on MLX | MLX Whisper → faster-whisper (CPU) |
| Speaking | Kokoro-82M on MLX | Kokoro (torch, CPU) → Piper → macOS `say` |
| Wake word | browser `SpeechRecognition` (Chrome, Safari) | — |

Speaking is a **chain**: if an engine won't load, or loads but can't produce sound
(Kokoro without `misaki` does exactly that), the next one takes over and the log
says so. `say` needs nothing installed, so voice never fails silently.

Weights download from Hugging Face on first use and are cached; later starts only
check the cache. At start-up the server warms both directions end to end (~9 s):

```
voice: speaking with Kokoro on MLX (mlx-community/Kokoro-82M-bf16)
voice: speaking checked end to end (kokoro-mlx)
voice: hearing with Parakeet on MLX (mlx-community/parakeet-tdt-0.6b-v3)
voice: warm in 9.0s
```

The `Creating new KokoroPipeline` line is printed once per process by mlx-audio and
is normal.

**Measured on an Apple Silicon Mac:** a short phrase takes ~0.15–0.25 s to
synthesise, a full sentence ~0.6 s.

**The wake-word caveat:** spotting "hey chef" uses the browser's speech
recogniser, which in Chrome sends audio to Google while hands-free is armed. The
command after the wake word is transcribed locally. A fully on-device wake word
would use openWakeWord with `onnxruntime-web` (melspectrogram → embedding → wake
model ONNX files in `frontend/public/wake/`), keeping the same
`startWakeWord({ phrase, onWake, onError, onStatus }) → { stop }` shape; it is not
built yet.

If the microphone returns 502, run `python check_voice.py` from `backend/` (server
stopped). It speaks with Kokoro, encodes that as webm/opus the way Chrome does,
and transcribes it with nothing catching the error, so you see the real trace.

---

## Recipe photos

The local picture model (SD-Turbo, ~2.5 GB in memory) competes with the chat model
— beside a 9B model it once pushed a 16 GB Mac into swap and turned a 20-second
recipe into seven minutes. So by default the server doesn't paint:

1. `IMAGE_PROVIDER=none` in `.env`.
2. Opening a recipe adds it to `media/photo-queue.json`
   (the app shows "Queued for the next photo run").
3. You run the tool when you're not cooking:

```bash
cd happy-bite/backend && source .venv/bin/activate
python tools/make_photos.py --list          # what's waiting
python tools/make_photos.py                 # paint them, then exit
python tools/make_photos.py --watch         # keep checking every 20 s (--every N)
python tools/make_photos.py --again rec_id  # redo one
```

Files go to `media/recipes/`, indexed in `media/recipes.json`. Photos already on
disk always show. To paint on demand again, set `IMAGE_PROVIDER=local` and
`IMAGE_MODEL=stabilityai/sd-turbo`.

---

## Recipe corpus, templates and the catalogue

| File | In git | Role |
|---|---|---|
| `shared/catalog.json` | yes | every ingredient the app knows (ids, categories, units) |
| `shared/recipes.json` | yes | curated seed recipes shown in Recipes |
| `shared/receipt-sample.json` | yes | the sample receipt |
| `shared/kaiser_recipes.jsonl` | **no** (~2.6 GB) | source corpus for RAG and templates — a JSONL export of [`Kaiser1308/CookingRecipes`](https://huggingface.co/datasets/Kaiser1308/CookingRecipes) |

The corpus never reaches the browser. The RAG index (`media/recipe-rag.sqlite3`)
is built in the background at start-up and rebuilt only when the source changes.

Maintenance scripts (run from `happy-bite/`):

```bash
python3 build_catalogue.py --dry        # add corpus ingredients to catalog.json; --dry writes nothing
python3 import_corpus.py --count 79     # restructure corpus recipes into app recipes with your model (--dry)
python3 ../audit.py                     # read shared/recipes.json as a cook would and list faults
```

`build_catalogue.py` never changes an existing entry (your stock points at those
ids); new entries are marked `"added": "corpus"` with a usage count so the prompt
can send only the most useful ones. `import_corpus.py` is slow on purpose (about a
minute a recipe on a 4B model), saves after each accepted recipe and can be
resumed.

---

## Training your own model

`happy-bite/training/` fine-tunes Qwen with QLoRA. Optional; runs on your machine.
On a 16 GB Mac it defaults to `Qwen/Qwen2.5-1.5B-Instruct` on MPS; a 7B may run out
of memory (7B is the default on an NVIDIA GPU — see the Windows README).

```bash
cd happy-bite/training
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # BASE_MODEL, DATASET, HF_TOKEN, MAX_RECIPES

python build_dataset.py --selftest       # offline check
python build_dataset.py --peek           # preview 3 examples
python build_dataset.py [--max 8000]     # source schema -> data/train.jsonl, data/val.jsonl
python build_app_dataset.py [--peek]     # OR app schema -> data/app_train.jsonl, data/app_val.jsonl
python train_qlora.py                    # -> out/…-lora/
python test_model.py [--base-only]       # score replies (base-only = untuned baseline)
python export_ollama.py                  # merge adapters, write an Ollama Modelfile
```

- `build_dataset.py` keeps the source recipes as written (title, ingredients,
  directions), only normalising container types.
- `build_app_dataset.py` converts them to the **app's** recipe schema: ingredients
  that match a catalogue id become `needs`/`seasoning`, the rest go to `extras`.
  Use it to teach the in-app recipe format. It prints an ingredient grounding
  rate; raise it by growing `shared/catalog.json` or its `SYNONYMS` map.
- Other sources: `--dataset owner/name` or `--csv path.csv`.

Tool calling works without fine-tuning — instruct models support it natively.
Fine-tuning mainly improves recipe quality and id grounding.

---

## Diagnostic tools

| Command | Where | What it tells you |
|---|---|---|
| `open http://localhost:8000/api/health/models` | browser | each model: configured, packages present, loaded, reachable |
| `./run.sh` | `happy-bite/backend` | which local model packages the venv is missing |
| `python check_voice.py` | `happy-bite/backend` (server stopped) | full trace of a speak → encode → hear round trip; saves `voice-check.wav` |
| `python3 check_model.py` | repo root | what your model server is really doing: model loaded, tokens/s, whether it reasons, time for a real recipe (reads `backend/.env`; `--url`, `--model`, `--budget`, `--wait`) |
| `python3 audit.py` | repo root | faults in the seed recipes |
| `npm test` | `happy-bite/frontend` | core engine tests |

---

## Project layout

```
KitchenBuddy/
├── README.md                       macOS guide (this file)
├── README-Windows.md               Windows guide
├── audit.py, check_model.py        diagnostics
└── happy-bite/
    ├── backend/
    │   ├── app/
    │   │   ├── main.py             app, start-up, serves frontend/dist
    │   │   ├── config.py           every setting
    │   │   ├── api.py              status, chat, recipes, import, images, speech
    │   │   ├── api_extra.py        recipe images, cooking prefetch, receipts, adapt, health
    │   │   ├── actions.py          tool schemas + validation
    │   │   ├── recipes.py          clean_recipe — the untrusted-output gate
    │   │   ├── template.py         cook an existing corpus recipe
    │   │   ├── rag.py              corpus retrieval (SQLite FTS5)
    │   │   ├── prompts.py          system prompts
    │   │   ├── catalog.py          catalogue + custom items
    │   │   ├── adapt.py            equipment adaptation
    │   │   ├── importer.py         recipe import from URLs
    │   │   ├── receipt.py          OCR + model receipt parsing
    │   │   ├── prefetch.py         pre-answered cooking questions
    │   │   ├── imagestore.py       recipe photo store
    │   │   ├── photoqueue.py       queue for tools/make_photos.py
    │   │   ├── warmup.py           model warm-up
    │   │   └── providers/          llm.py, media.py (images, voice), speech_local.py
    │   ├── tools/make_photos.py
    │   ├── check_voice.py
    │   ├── run.sh, setup_local.sh  macOS
    │   ├── run.ps1                 Windows
    │   ├── requirements-mac.txt
    │   ├── requirements-windows.txt
    │   └── media/                  generated, not in git
    ├── frontend/
    │   ├── src/
    │   │   ├── core/               offline engine, equipment, intent (no model)
    │   │   ├── state/              kitchen store, UI state, action executor
    │   │   ├── screens/            Today, Kitchen, Recipes, Receipt, Shopping, Chat, CookMode
    │   │   ├── sheets/             bottom sheets (recipe, create, people, equipment…)
    │   │   ├── components/         Chat, AdaptPanel, PhotoStrip, VoiceAgent, Charts…
    │   │   ├── lib/                api, store (IndexedDB), voice, wake, speechqueue, command
    │   │   └── styles/
    │   ├── public/                 icon, manifest, service worker
    │   └── dist/                   production build
    ├── shared/                     catalogue, seed recipes, corpus
    ├── training/                   optional QLoRA fine-tuning
    ├── build_catalogue.py
    └── import_corpus.py
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Feature shows as off / "package missing" but the import works in your shell | server running under a different Python — use `./run.sh` |
| `No module named 'pydantic_settings'` | `pip install -r requirements-mac.txt` in the venv |
| pip fails on torch / kokoro | venv is Python 3.13+; rebuild on 3.12 |
| Chat is off, `/api/health/models` says unreachable | the model server isn't running, or `OPENAI_BASE_URL` has the wrong port (1234 LM Studio, 11434 Ollama) |
| Recipe takes minutes | the model is reasoning — `LLM_THINK=false` and turn reasoning off in LM Studio; measure with `check_model.py` |
| `The number of tokens to keep from the initial prompt is greater than the context length` | loaded context too small — raise it in the server, set `LLM_CONTEXT`, lower `LLM_IDS_CHARS` / `LLM_JSON_TOKENS` |
| 400 BadRequest from LM Studio | non-standard fields; the app retries without them, or set `LLM_EXTRAS=false` |
| Everything slow, Mac swapping | two models loaded at once — only one of LM Studio / Ollama, `IMAGE_PROVIDER=none` while cooking |
| Microphone returns 502 | `python check_voice.py`; usually missing `ffmpeg` or `misaki` |
| No voice but no error | check the log for which engine in the speaking chain answered |
| Cooking mode slows down | prefetch racing your questions on one local model — `PREFETCH_ENABLED=false` |
| Photos never appear | expected with `IMAGE_PROVIDER=none` — run `tools/make_photos.py` |
| Receipt reading off | `brew install tesseract tesseract-lang` |
| Voice models reload every few seconds | `--reload` restarts on every backend save — use `./run.sh --no-reload` |

---

## Known limitations

- **Receipt scanning in the app is simulated** — the server can read receipts, the
  Receipt tab doesn't call it yet.
- **Wake word** uses the browser's cloud speech recogniser while armed.
- **Storage is IndexedDB** — fine for one household, no sync between devices.
- **A profile is a name plus what someone won't eat.** No health data, deliberately.
- **Cooking mode walks the original steps**, not an equipment adaptation.
- **Fine-tuning** has scripts and self-tests but has not been run end to end in
  this repo.
