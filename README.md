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
| `WAKE_WORD` | `bob` | the name the app listens for: anywhere while hands-free is on, and before every command in cooking mode |
| `TTS_MODEL` / `TTS_VOICE` / `STT_MODEL` | `gpt-4o-mini-tts` / `coral` / `gpt-4o-mini-transcribe` | cloud voice, only with `SPEECH_PROVIDER=openai` |
| **Pictures** | | |
| `IMAGE_PROVIDER` | `openai` | `openai` \| `local` \| `none` |
| `IMAGE_MODEL` | `gpt-image-1` | local: e.g. `stabilityai/sd-turbo` |
| `IMAGE_STEPS` / `IMAGE_SIZE` / `IMAGE_DEVICE` | `2` / `512` / `auto` | local generation |
| `IMAGE_COUNT` | `1` | photos kept per recipe |
| `IMAGE_PREFETCH` | `true` | queue photos when a recipe is opened |
| **Other** | | |
| `OCR_LANGUAGES` | `fra+eng` | tesseract language packs for receipts |
| `PRODUCTS_INDEX` | *(blank)* | product database for receipts; blank = `backend/media/products.sqlite3` |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS, comma-separated |
| `MEDIA_DIR` | `media` | photos, queues and indexes (relative to `backend/`) |

---

## Features

### The five screens

**Today** (tonight's dish), **Kitchen** (stock and expiry), **Recipes** (the ones you
have saved — the book starts empty), **Receipt**, **Shopping** — plus the chef chat and
cooking mode on top.

### Choosing from your recipes

"Something else" on Today — or *"Bob, let's cook"* with no dish named — deals
**three cards** from your recipe book, like the upgrade screen in a survivors
game: photo, name, time and difficulty on each. The first hand is the best three
for what's in the kitchen; **Reroll** deals three others. On a keyboard, `1` `2`
`3` pick a card and `R` rerolls. With fewer than three recipes in the book, the
empty places offer to write a new one.

What opens it: **Something else** on Today, **Pick something to cook** in the
history, or saying *"let's cook"*, *"start cooking"* or just *"cook"* with no dish
named and no recipe open. The cards come from your saved recipes, so the book needs
at least one. A request with *"something"*, *"dinner"*, *"meal"* or *"recipe"* in
it (*"make me something for dinner"*) writes a new recipe instead.
([`sheets/Pick.jsx`](happy-bite/frontend/src/sheets/Pick.jsx))

### Difficulty in stars

Every recipe is rated from **½ to 5 stars** in half steps. The app works the
rating out from the method; the model is not asked. Four things count:

| | Weight | From | To |
|---|---|---|---|
| Ingredients (a seasoning counts half) | 30 % | 1 | 15 or more |
| Ingredients going in at the busiest step | 20 % | 1 | 6 or more |
| Things to wash (pans, pot, wok, bowl, baking dish, board and knife, grater, colander, blender, mixer, rolling pin) | 25 % | 1 | 7 or more |
| Total time (each extra minute counts less as the dish gets longer) | 25 % | 5 min | 3 hours |

An egg fried in one pan is ½, a plain omelette 1, a stir-fry with rice 2½, a
lasagne 4. Technique on its own is not counted: croissants score on their hours
and rolling pin, not the lamination. Recipes already saved are re-rated when
they are shown. The same formula is in
[`recipes.stars_from_method`](happy-bite/backend/app/recipes.py) and
[`engine.starsFromMethod`](happy-bite/frontend/src/core/engine.js) — change both
together. A recipe with no method (a two-line idea) keeps the model's estimate;
`complexity` (1–3) is still kept underneath for the Effort filter.

### What you've cooked

The book icon on Today (or the "Cooked this week" tile) opens your cooking
history: every dish finished in cooking mode, newest first, grouped by week and
month, with how many people it was for, its stars, what you said about it in
the review ("Loved it") and a **Cook again** button. At the top: meals this
week and this month, days in a row, and your most-cooked dish. Stored in the
browser with everything else; each entry keeps the dish's name, so it still
reads right after the recipe is thrown away.
([`sheets/History.jsx`](happy-bite/frontend/src/sheets/History.jsx))

### "I'm drained. Give me 15 minutes."

Say or type it to the chef — *"Bob, I'm drained, give me 15 minutes"*, *"I've
got 20 minutes"*, *"I'm exhausted"* — and the assistant writes **one** dinner
from what is already in the kitchen, with nothing to buy, on the table in that
time, and opens it. No questions.

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

Above the request box: **From my kitchen** (the default) or **Any ingredients**.
*Any ingredients* writes the best dish for the request rather than the best one
the shelves allow: the kitchen is still listed to the model but only used where
it fits, the nothing-to-buy rule for quick dinners is off, the template step
below is skipped, and whatever you haven't got goes on the shopping list.
(`anyIngredients` on `POST /api/recipes/generate`.)

1. **A time limit.** Every request gets one, read from what was asked
   (`recipes.time_budget`): **20 minutes** by default, **15** for "I'm drained",
   "tired" or "quick", the number they give ("give me 15 minutes", "half an
   hour"), and **no limit** when they ask for something slow ("a slow Sunday
   roast", the "Take our time" chip). The limit goes into the prompt as its most
   important rule; for 30 minutes or less the recipe must also use only what is
   in the kitchen (unless *Any ingredients* is on). `POST /api/recipes/generate` accepts `maxMinutes` to set it
   directly.
2. **Template first** — but not for a quick dinner.
   [`app/template.py`](happy-bite/backend/app/template.py) looks for a corpus
   recipe whose ingredients you mostly already have (`TEMPLATE_FLOOR`), whose
   written times fit the limit, and converts it to the app's format, scaled to
   the people eating. For a limit of 30 minutes or less this step is skipped:
   corpus methods are rarely that short, and a failed conversion cost more time
   than writing one.
3. **A named dish follows a real recipe for it.** "Make mochi", "spaghetti
   carbonara", "pad thai": [`rag.find_dish`](happy-bite/backend/app/rag.py)
   spots the dish — a word or phrase the corpus uses in recipe *titles* at least
   as often as in ingredient lists (mochi yes, courgettes no) — takes the plain
   versions of it ("Pancakes", not "Potato Pancakes - Grandma's…") and picks
   the most **typical** one: the version whose ingredients most of the others
   share. The model gets that recipe's ingredients **and method**, is told to
   keep what makes the dish (glutinous rice flour stays glutinous rice flour),
   and has no 20-minute limit or nothing-to-buy rule: missing ingredients go on
   the shopping list. The result is checked against the real recipe — a key
   ingredient dropped or swapped, a cooking step (microwave, steam, bake…)
   lost — and sent back once if so. The chef chat does the same for "how do I
   make mochi?".
4. **Otherwise write**, with a similar corpus recipe retrieved as reference
   ([`app/rag.py`](happy-bite/backend/app/rag.py), SQLite FTS5, no extra service).
5. **Check, and send it back once.** The result passes `clean_recipe`, then
   `recipe_problems` looks for what a small model gets wrong: steps adding up to
   more than the limit, a step that says "255 minutes", a step naming a food the
   recipe doesn't contain ("mix in the hummus"), a marinade or sauce used before
   the step that makes it, too many steps, or something to buy on a quick dinner. If it finds any, the model is asked again **once**,
   with that exact list. If the second attempt is still wrong, the better one is
   shown with a *"Check this one before you start"* box listing the problems.

**Portions and time are enforced, not requested.** Per person, by shelf, an
amount over what is still a plate of food (250 g of meat, fish or vegetables,
150 g of dry pasta or rice) comes down to an ordinary portion (150 g of meat,
175 g of vegetables, 90 g of pasta) and the recipe says so. Recipes saved
before this are brought down the same way the next time the app opens. A step
whose words say "roast for 12 minutes" gets 12 minutes whatever its `minutes`
field says, the total is the steps added up, an oven nobody turns on adds ten
minutes, and roasting on a hob heat or with no step to heat the oven sends the
recipe back.

`clean_recipe` also tidies what small models fill in for the sake of it: a cue
of "n/a" or "no cue", a heat on a step that never touches the hob, method sentences in an
ingredient's prep, a total time that doesn't match the steps, and ids written as
names (`"Spinach (g)"` becomes `spinach`).

`POST /api/recipes/generate/stream` streams progress so the UI can show what the
model is doing.

### Cooking mode

One step at a time, large, with heat and what-to-watch-for beside it. With local
voice on it listens, but only acts on what is said to it by name (`WAKE_WORD`,
default **Bob**), so conversation and the radio don't move the recipe on:

- **"Bob, next" / "Bob, back" / "Bob, repeat" / "Bob, go to step 2"**, **"Bob,
  start a timer"**, or "Bob, …" and any question — answered aloud in a sentence
  or two. "Bob" on its own chimes and listens for the next sentence.
- **Each step is read out complete:** what to grab when it needs a new bowl or
  pan, the step, and what goes in with amounts scaled to the table — *"Step 1.
  Grab a bowl. Marinate the chicken cubes. You'll need 400 grams of chicken
  breast."* ([`core/brief.js`](happy-bite/frontend/src/core/brief.js))
- **"Bob, stop chef"** stops talking, **"Bob, stop timer"** cancels a timer,
  **"Bob, stop cooking"** ends the session.
- **"Stop, Bob" while it's thinking or talking** cancels the answer, chimes and
  listens again — for when it started before you'd said the right thing. Say the
  new question in the same breath and it goes straight on: *"Bob, stop — how
  long for the onions?"* Without the name only a bare "stop", "wait" or "never
  mind" counts, so "stop stirring" to someone else cancels nothing.
  ([`lib/stopword.js`](happy-bite/frontend/src/lib/stopword.js))
- **The gap in a step:** on a timed cooking step the screen shows a small window
  and one job that fits in it, and says it out loud when the timer starts —
  *"You have a 90-second window. Rinse the knife and the cutting board."* Frying
  gives 90 seconds (the pan needs a stir); simmering or baking gives the whole
  step less a minute. The job is read off the recipe itself — prep for the next
  step, rinsing what has been used, putting things back in the fridge, setting
  the table — so it needs no model and never repeats itself.
  ([`core/window.js`](happy-bite/frontend/src/core/window.js))
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

**Receipt** tab → **Photograph a receipt**. The photo goes to the server, and the
lines come back sorted into *Matched*, *Needs checking* and *Not food*. Tap a line
to fix it; a fix is remembered and applied to every later receipt. **Use a sample
receipt** still works without the server.

Nothing is trained for this. It uses two ready-made models and a database search:

| Step | What it is | What it does |
|---|---|---|
| 1. **Tesseract** | free, open-source OCR — a small neural network already trained to read printed letters; runs on your machine | photo → `CRF LT DEMI ECR 1L 1,09` |
| 2. **Product lookup** | a search, not a model: real products from [Open Food Facts](https://world.openfoodfacts.org), plus the lines you have corrected before | `CRF LT DEMI ECR` → *Lait demi-écrémé, Carrefour → `milk`* |
| 3. **The chat model** | the one in LM Studio or Ollama | reads each line with what the lookup found, decides the ingredient and quantity |

**How a line is looked up**
([`app/products.py`](happy-bite/backend/app/products.py)). Prices and sizes are
dropped and common till abbreviations spelled out (`CRF` carrefour, `LT` lait,
`PDT` pomme de terre). The database is searched by **fragments of words** rather
than whole words, accents ignored, so `ECR` finds *écrémé* and `POUL` finds
*poulet*. The candidates are then scored word by word: the same word scores
highest, the start of a word (`POUL`) next, the consonants of a word (`BLC`
blanc, `FRMG` fromage) after that. Lines you corrected by hand before are
compared the same way, so a fix for `CRF LT DEMI ECR 1L` also covers
`CRF LAIT DEMI ECR 50CL`.

**What the model is shown**, per line:

```
3. CRF LT DEMI ECR 1L 1,09
   corrected before: "CRF LT DEMI ECR 1L" -> milk
   database: Lait demi-écrémé (Carrefour, 1 L, Semi-skimmed milks) -> milk
```

plus the ingredient ids the lookup found and as many others as fit the context
window.

**How much its answer is trusted**
([`app/receipt.py`](happy-bite/backend/app/receipt.py)):

| Situation | Result |
|---|---|
| model and database agree | confidence goes up |
| model gave no id, database fairly sure | the database's item, marked **Check** |
| model disagrees with a strong database match | marked **Check** |
| line almost exactly one you corrected before | your correction, no question |

With the model off, or if it fails, the lookup alone still produces the receipt;
its confidence is capped, so a guess always lands in *Needs checking*. Prices are
never read as quantities, and anything not in the catalogue comes back as a null
id for you to map.

**Building the product database** — once, from the `happy-bite` folder:

```
python3 import_openfoodfacts.py --download
```

It downloads the Open Food Facts export (~1.3 GB, resumes if interrupted), keeps
the products sold in France, and writes `backend/media/products.sqlite3`
(about 480,000 products, 120 MB; the build itself takes about a minute and a half). Delete
`backend/media/openfoodfacts-products.csv.gz` afterwards; nothing needs the
internet after that. Each product gets its catalogue ingredient from its Open Food
Facts **categories**, which are in English like the catalogue:
*semi-skimmed-milks* → `milk`, *goat-cheeses* → `goat`. Rebuild after the
catalogue gains ingredients. `--country en:belgium` (repeatable) for other
countries, `--limit 200000` for a quick trial. Without the database, receipts are
read by the model alone.

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
| Wake word | browser `SpeechRecognition` (Chrome, Edge, Safari) — on by default | — |

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

**Saying "Bob":** hands-free is **on by default**. Anywhere in the app, say
*"Bob"* and what you want in one breath (*"Bob, I'm drained, give me 15
minutes"*), or *"Bob"*, wait for the chime, then talk. The browser asks for the
microphone the first time. Turn it off in the settings sheet (gear icon on
Today), or by holding the mic button down.

**The wake-word caveat:** spotting "Bob" uses the browser's speech
recogniser, which in Chrome sends audio to Google while hands-free is armed, so
it needs an internet connection. The
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
| `shared/receipt-sample.json` | yes | the sample receipt |
| `shared/kaiser_recipes.jsonl` | **no** (~2.6 GB) | source corpus for RAG and templates — a JSONL export of [`Kaiser1308/CookingRecipes`](https://huggingface.co/datasets/Kaiser1308/CookingRecipes) |

The corpus is English-language home cooking — largely American community
cookbooks and recipe sites. It is rich in everyday dishes and thin or skewed on
others: its most typical carbonara uses butter and bacon, its most typical mochi
is Hawaiian butter mochi. A named dish follows what the corpus says that dish
is.

The corpus never reaches the browser. The RAG index (`media/recipe-rag.sqlite3`)
is built in the background at start-up and rebuilt only when the source changes.

Maintenance scripts (run from `happy-bite/`):

```bash
python3 build_catalogue.py --dry        # add corpus ingredients to catalog.json; --dry writes nothing
```

`build_catalogue.py` never changes an existing entry (your stock points at those
ids); new entries are marked `"added": "corpus"` with a usage count so the prompt
can send only the most useful ones. (`import_corpus.py` filled the old recipe
book, `shared/recipes.json`, which has been removed; it now stops and says so.)

---

## Training your own model

`happy-bite/training/` fine-tunes Qwen with LoRA. Optional; runs on your machine.
On a Mac it trains on MPS without 4-bit quantization (bitsandbytes is
NVIDIA-only) and defaults to `Qwen/Qwen2.5-1.5B-Instruct`, which fits a 16 GB Mac;
a 7B may run out of memory. On an NVIDIA GPU it uses 4-bit QLoRA and defaults to
7B — see the Windows README.

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
Fine-tuning mainly improves recipe quality and id grounding. It does not make
the model pick a particular dish: for a dinner that must come out the same every
time (a demo, say), save that recipe in the book instead.

---

## Diagnostic tools

| Command | Where | What it tells you |
|---|---|---|
| `open http://localhost:8000/api/health/models` | browser | each model: configured, packages present, loaded, reachable |
| `./run.sh` | `happy-bite/backend` | which local model packages the venv is missing |
| `python check_voice.py` | `happy-bite/backend` (server stopped) | full trace of a speak → encode → hear round trip; saves `voice-check.wav` |
| `python3 check_model.py` | repo root | what your model server is really doing: model loaded, tokens/s, whether it reasons, time for a real recipe (reads `backend/.env`; `--url`, `--model`, `--budget`, `--wait`) |
| `npm test` | `happy-bite/frontend` | engine, wake word, voice commands, stop-to-cancel, spoken steps and cooking-window tests |

---

## Project layout

```
KitchenBuddy/
├── README.md                       macOS guide (this file)
├── README-Windows.md               Windows guide
├── check_model.py                  diagnostics
├── data/                           standalone ChromaDB data engine prototype (see data/README_DATA.md)
└── happy-bite/
    ├── backend/
    │   ├── app/
    │   │   ├── main.py             app, start-up, serves frontend/dist
    │   │   ├── config.py           every setting
    │   │   ├── context.py          the model's loaded context window
    │   │   ├── api.py              status, chat, recipes, import, images, speech
    │   │   ├── api_extra.py        recipe images, cooking prefetch, receipts, adapt, health
    │   │   ├── actions.py          tool schemas + validation
    │   │   ├── recipes.py          clean_recipe, time limits, recipe_problems — the untrusted-output gate
    │   │   ├── template.py         cook an existing corpus recipe
    │   │   ├── rag.py              corpus retrieval (SQLite FTS5)
    │   │   ├── prompts.py          system prompts
    │   │   ├── catalog.py          catalogue + custom items
    │   │   ├── adapt.py            equipment adaptation
    │   │   ├── importer.py         recipe import from URLs
    │   │   ├── receipt.py          OCR + model receipt parsing
    │   │   ├── products.py         receipt lines looked up in Open Food Facts
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
    │   │   ├── core/               offline engine, equipment, intent, cooking windows (no model)
    │   │   ├── state/              kitchen store, UI state, action executor
    │   │   ├── screens/            Today, Kitchen, Recipes, Receipt, Shopping, ChatScreen, CookMode
    │   │   ├── sheets/             bottom sheets (recipe, create, people, equipment…)
    │   │   ├── components/         Chat, AdaptPanel, PhotoStrip, VoiceAgent, Charts…
    │   │   ├── lib/                api, store (IndexedDB), voice, wake, speechqueue, command, stockcmd
    │   │   └── styles/
    │   ├── public/                 icon, manifest, service worker
    │   ├── dist/                   production build (npm run build)
    │   └── node_modules/           npm install, not in git
    ├── shared/                     catalogue, receipt sample, corpus (corpus not in git)
    ├── training/                   optional LoRA / QLoRA fine-tuning
    ├── build_catalogue.py
    ├── import_openfoodfacts.py  builds the product database for receipts
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
| Recipe opens with *"Check this one before you start"* | the model got it wrong twice; the box says what — try again, or use a larger model |
| Saying "Bob" does nothing | look under the mic button: it says why (microphone refused, no internet for Chrome's recogniser, another tab holding the mic). Firefox has no recogniser |
| Photos never appear | expected with `IMAGE_PROVIDER=none` — run `tools/make_photos.py` |
| Receipt reading off | `brew install tesseract tesseract-lang` |
| Voice models reload every few seconds | `--reload` restarts on every backend save — use `./run.sh --no-reload` |

---

## Known limitations

- **Receipts** are only as good as the photo Tesseract gets: flat, lit, filling the
  frame. An abbreviation that is neither the start of a word nor its consonants
  (`BAS`) matches weakly until you correct it once. Product categories map to the
  nearest catalogue ingredient, so processed foods are approximate (skyr comes
  back as cheese).
- **Wake word** is on by default and uses the browser's cloud speech recogniser
  while armed.
- **Small local models** follow the recipe rules most of the time, not always:
  the checks catch wrong times and invented ingredients, not odd cooking choices
  (five garlic cloves for two). A larger model writes better recipes.
- **Storage is IndexedDB** — fine for one household, no sync between devices.
- **A profile is a name plus what someone won't eat.** No health data, deliberately.
- **Cooking mode walks the original steps**, not an equipment adaptation.
- **Fine-tuning** has scripts and self-tests but has not been run end to end in
  this repo.
