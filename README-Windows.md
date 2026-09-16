# Happy Bite (KitchenBuddy) — Windows

> **On a Mac?** Follow [README.md](README.md) instead.

An offline-first kitchen assistant. It tracks what is in your kitchen and what is
about to go off, suggests tonight's dinner from what you already have, and — with
the AI layer switched on — a chef talks you through cooking it, writes and adapts
recipes, imports recipes from websites and listens and speaks hands-free.

React (Vite) in the browser, Python (FastAPI) on the server. Every model can run
locally on a Windows 10/11 PC: no cloud account is required. An NVIDIA GPU makes
it much faster but isn't required.

All commands below are for **PowerShell**.

---

## Contents

1. [How it is built](#how-it-is-built)
2. [Quick start](#quick-start)
3. [The chat model: LM Studio or Ollama](#the-chat-model-lm-studio-or-ollama)
4. [Configuration (`backend\.env`)](#configuration-backendenv)
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
| **Python 3.12** (or 3.11) | `winget install Python.Python.3.12` | torch, kokoro and diffusers publish no wheels for 3.13/3.14 |
| **Node 18+** | `winget install OpenJS.NodeJS.LTS` | the frontend |
| **Git** | `winget install Git.Git` | cloning the repo |
| **A model server** | [LM Studio](https://lmstudio.ai) *or* [Ollama](https://ollama.com) | the chat model — see [below](#the-chat-model-lm-studio-or-ollama) |
| ffmpeg | `winget install Gyan.FFmpeg` | optional, fallback decoder for recordings |
| Tesseract | `winget install UB-Mannheim.TesseractOCR` | optional, receipt reading |
| NVIDIA driver | from nvidia.com | optional, GPU speed-up |

Open a **new** PowerShell window after installing, so PATH changes apply.

If PowerShell refuses to run scripts (`run.ps1`, `Activate.ps1`), allow local
scripts once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 1. Backend

```powershell
cd happy-bite\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# NVIDIA GPU only — install the CUDA build of torch FIRST.
# Get the exact command for your CUDA version from pytorch.org/get-started/locally, e.g.:
#   pip install torch --index-url https://download.pytorch.org/whl/cu<version>

pip install -r requirements-windows.txt
# create backend\.env — see "Configuration" below
.\run.ps1                   # http://127.0.0.1:8000
```

`run.ps1` always runs the venv's own Python (a `uvicorn` on PATH may belong to
another interpreter that cannot see your packages), stops anything already holding
port 8000, lists missing packages and says whether torch can see your GPU.

| Command | Use it when |
|---|---|
| `.\run.ps1` | editing backend code — restarts on every save, which reloads the voice models |
| `.\run.ps1 -NoReload` | cooking — no surprise restarts |
| `.\run.ps1 -Port 8001` | port 8000 is taken by something you want to keep |

`setup_local.sh` is macOS/Linux only; on Windows, `requirements-windows.txt` already
installs everything it would.

### 2. Model server

Start **one** of LM Studio or Ollama with a model loaded — see the next section.

### 3. Frontend

```powershell
cd happy-bite\frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to 127.0.0.1:8000
```

Day to day that is **three things running**: the model server, the backend, the
frontend.

### One address for a kitchen tablet

```powershell
cd happy-bite\frontend; npm run build
```

When `frontend\dist` exists, the backend serves the app, the API and the photos
together on port 8000 — no Vite needed. To reach it from a tablet, allow Python
through Windows Defender Firewall when asked.

### Check it

```powershell
start http://localhost:8000/api/health/models    # every model: configured, loaded, reachable, and why not
curl.exe http://localhost:8000/api/status        # what the browser sees
```

(Use `curl.exe`, not `curl` — in Windows PowerShell `curl` is an alias for
`Invoke-WebRequest`.)

---

## The chat model: LM Studio or Ollama

Both are free apps that run a language model on your machine and expose it with
the same OpenAI-style API. The backend talks to **whichever one
`OPENAI_BASE_URL` points at** and nothing else, so:

- **Run one, not both.** The other isn't used.
- Running both costs little while the unused one has no model loaded. Once it
  loads one, that model holds several GB of RAM — or worse, of your GPU's VRAM,
  which the voice and picture models also want — and everything slows down.

On Windows both run GGUF models through llama.cpp (CUDA on NVIDIA, Vulkan or CPU
otherwise), so **speed is about the same**. Pick by how you like to work.

### Ollama — simplest with this app on Windows

A background server with a command line. The app can control it directly from
`.env`: context window, keep-alive and the thinking switch all work without
touching Ollama itself.

1. Install from [ollama.com](https://ollama.com) (or `winget install Ollama.Ollama`).
   It starts in the system tray and keeps running — **you don't need `ollama
   serve`** unless you quit the tray app.
2. Pull a model:

```powershell
ollama pull qwen2.5:3b-instruct
ollama list                    # the exact ids you can use
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

### LM Studio

A desktop app with a model browser and every setting in its UI. Good if you want
to try many models.

1. Install from [lmstudio.ai](https://lmstudio.ai) and open it.
2. **Discover** → search for a model → download a **GGUF** build
   (MLX builds are Mac-only).
3. **Developer** → load the model → **Start Server** (port 1234).
4. In the loaded model's settings:
   - **GPU offload** — as many layers as fit in your VRAM.
   - **Context length** — e.g. 8192. Put the same number in `LLM_CONTEXT`.
   - **Idle unload / auto-evict** — off, or a long time, so the model stays loaded.
   - **Reasoning / thinking** — off for reasoning models.
5. The model's exact id: `curl.exe http://localhost:1234/v1/models`.

```ini
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:1234/v1
LLM_MODEL=qwen2.5-3b-instruct
LLM_CONTEXT=8192
LLM_THINK=false
```

### Side by side

| | Ollama | LM Studio |
|---|---|---|
| Interface | tray app + command line | desktop app |
| Default port | 11434 | 1234 |
| Model formats on Windows | GGUF | GGUF |
| Context window | `LLM_NUM_CTX` from `.env` | set in the app |
| Keep model loaded | `LLM_KEEP_ALIVE` from `.env` | set in the app |
| Thinking switch from the app | honoured | ignored (400) — turn off in the app |
| GPU layers | automatic | set in the app |

**Measure rather than guess:** `python check_model.py --url http://localhost:11434/v1`
(and `:1234/v1`) from the repo root prints tokens per second. **Model size matters
far more than the server**: a 1–3B model answers several times faster than a 7–9B
one and writes weaker recipes. The fastest setup is a model that fits **entirely
in VRAM**; once layers spill onto the CPU, speed drops sharply.

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
~6.5 GB; fits fully on a GPU with 8 GB of VRAM only without much else loaded —
12 GB is comfortable.

---

## Configuration (`backend\.env`)

Create `happy-bite\backend\.env` (plain text, e.g. in Notepad or VS Code).
Everything is optional. A blank or missing setting switches that one feature off;
the server still starts. All settings and their defaults are in
[`happy-bite/backend/app/config.py`](happy-bite/backend/app/config.py).

### Fully local

```ini
# Chat — see the Ollama / LM Studio section for the server-specific lines
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b-instruct
LLM_NUM_CTX=8192
LLM_CONTEXT=8192
LLM_THINK=false

# Pictures — off while cooking, made by tools\make_photos.py instead
IMAGE_PROVIDER=none

# Voice (no MLX on Windows)
SPEECH_PROVIDER=local
SPEECH_BACKEND=torch
WHISPER_MODEL=base.en              # small.en is more accurate, slower
WHISPER_DEVICE=cpu                 # cuda only with cuBLAS 12 + cuDNN 9 on PATH
WHISPER_COMPUTE=int8               # float16 with cuda
STT_LANGUAGE=en
TTS_ENGINE=kokoro
KOKORO_VOICE=af_heart
KOKORO_LANG=a                      # a US, b UK, f French, e Spanish, i Italian
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
| `OPENAI_BASE_URL` | — | point `openai` at Ollama or LM Studio |
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
| `RAG_SOURCE` / `RAG_INDEX` | `shared\kaiser_recipes.jsonl` / `media\recipe-rag.sqlite3` | |
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
| `SPEECH_BACKEND` | `auto` | use `torch` on Windows (`auto` also works, after a failed MLX attempt) |
| `WHISPER_MODEL` | `base` | `base.en` \| `small.en` \| `base` \| `small` \| `distil-large-v3` \| a local path |
| `WHISPER_DEVICE` | `auto` | `cpu` \| `cuda` \| `auto` |
| `WHISPER_COMPUTE` | `auto` | `int8` (CPU) \| `float16` (CUDA) \| `int8_float16` |
| `STT_LANGUAGE` | — | blank = auto-detect |
| `TTS_ENGINE` | `kokoro` | first engine in the speaking chain: `kokoro` \| `piper` \| `say` (Windows voice) |
| `KOKORO_VOICE` / `KOKORO_LANG` | `af_heart` / `a` | |
| `SAY_VOICE` | — | Windows voice name, e.g. `Microsoft Zira Desktop` |
| `PIPER_VOICE` / `PIPER_CONFIG` | — | path to a Piper `.onnx` voice |
| `WAKE_WORD` | `bob` | the name the app listens for: anywhere while hands-free is on, and before every command in cooking mode |
| `TTS_MODEL` / `TTS_VOICE` / `STT_MODEL` | `gpt-4o-mini-tts` / `coral` / `gpt-4o-mini-transcribe` | cloud voice, only with `SPEECH_PROVIDER=openai` |
| **Pictures** | | |
| `IMAGE_PROVIDER` | `openai` | `openai` \| `local` \| `none` |
| `IMAGE_MODEL` | `gpt-image-1` | local: e.g. `stabilityai/sd-turbo` |
| `IMAGE_STEPS` / `IMAGE_SIZE` / `IMAGE_DEVICE` | `2` / `512` / `auto` | local generation (`cuda` or `cpu`) |
| `IMAGE_COUNT` | `1` | photos kept per recipe |
| `IMAGE_PREFETCH` | `true` | queue photos when a recipe is opened |
| **Other** | | |
| `OCR_LANGUAGES` | `fra+eng` | tesseract language packs for receipts |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CORS, comma-separated |
| `MEDIA_DIR` | `media` | photos, queues and indexes (relative to `backend\`) |

The MLX-only settings (`STT_ENGINE`, `PARAKEET_MODEL`, `MLX_WHISPER_MODEL`,
`KOKORO_MLX_REPO`) are ignored on Windows.

---

## Features

### The five screens

**Today** (tonight's dish), **Kitchen** (stock and expiry), **Recipes** (seed
recipes plus your saved ones), **Receipt**, **Shopping** — plus the chef chat and
cooking mode on top.

### "I'm drained. Give me 15 minutes."

The first button on Today. One tap, no questions: the assistant writes **one**
dinner from what is already in the kitchen, with nothing to buy, that is on the
table in 15 minutes, and opens it. Saying or typing the same thing to the chef
does the same — *"Bob, I'm drained, give me 15 minutes"*, *"I've got 20
minutes"*, *"I'm exhausted"*. With the assistant off, the button filters the
recipe book to easy dishes under 15 minutes instead.

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

1. **A time limit.** Every request gets one, read from what was asked
   (`recipes.time_budget`): **20 minutes** by default, **15** for "I'm drained",
   "tired" or "quick", the number they give ("give me 15 minutes", "half an
   hour"), and **no limit** when they ask for something slow ("a slow Sunday
   roast", the "Take our time" chip). The limit goes into the prompt as its most
   important rule; for 30 minutes or less the recipe must also use only what is
   in the kitchen. `POST /api/recipes/generate` accepts `maxMinutes` to set it
   directly.
2. **Template first** — but not for a quick dinner.
   [`app/template.py`](happy-bite/backend/app/template.py) looks for a corpus
   recipe whose ingredients you mostly already have (`TEMPLATE_FLOOR`), whose
   written times fit the limit, and converts it to the app's format, scaled to
   the people eating. For a limit of 30 minutes or less this step is skipped:
   corpus methods are rarely that short, and a failed conversion cost more time
   than writing one.
3. **Otherwise write**, with a similar corpus recipe retrieved as reference
   ([`app/rag.py`](happy-bite/backend/app/rag.py), SQLite FTS5, no extra service).
4. **Check, and send it back once.** The result passes `clean_recipe`, then
   `recipe_problems` looks for what a small model gets wrong: steps adding up to
   more than the limit, a step that says "255 minutes", a step naming a food the
   recipe doesn't contain ("mix in the hummus"), too many steps, or something to
   buy on a quick dinner. If it finds any, the model is asked again **once**,
   with that exact list. If the second attempt is still wrong, the better one is
   shown with a *"Check this one before you start"* box listing the problems.

`clean_recipe` also tidies what small models fill in for the sake of it: a cue
of "n/a", a heat on a step that never touches the hob, method sentences in an
ingredient's prep, a total time that doesn't match the steps, and ids written as
names (`"Spinach (g)"` becomes `spinach`).

`POST /api/recipes/generate/stream` streams progress so the UI can show what the
model is doing.

### Cooking mode

One step at a time, large, with heat and what-to-watch-for beside it. With local
voice on it listens, but only acts on what is said to it by name (`WAKE_WORD`,
default **Bob**), so conversation and the radio don't move the recipe on:

- **"Bob, next" / "Bob, back" / "Bob, repeat"**, **"Bob, start a timer"**, or
  "Bob, …" and any question — answered aloud in a sentence or two. "Bob" on its
  own chimes and listens for the next sentence.
- **"Bob, stop chef"** stops talking, **"Bob, stop timer"** cancels a timer,
  **"Bob, stop cooking"** ends the session.
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

[`app/receipt.py`](happy-bite/backend/app/receipt.py): **tesseract** reads the
photo into text, then the chat model maps lines like `PLT FERM X6` to catalogue
items. No vision model needed. Unknown ids land in "needs a look", prices are not
mistaken for quantities, and corrections you have made before are applied first.
The backend finds Tesseract in `C:\Program Files\Tesseract-OCR` even when it isn't
on PATH. Select the French language data in the installer for French receipts.

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

| Job | Default on Windows | Fallback |
|---|---|---|
| Hearing | faster-whisper (CPU `int8`, or CUDA) | — |
| Speaking | Kokoro-82M on torch (CUDA if torch has it, else CPU) | Piper → the Windows system voice |
| Wake word | browser `SpeechRecognition` (Chrome, Edge) — on by default | — |

Speaking is a **chain**: if an engine won't load, or loads but can't produce sound
(Kokoro without `misaki` does exactly that), the next one takes over and the log
says so. The last link is the built-in Windows voice (System.Speech through
PowerShell), which needs nothing installed, so voice never fails silently. List
the installed voices for `SAY_VOICE` with:

```powershell
Add-Type -AssemblyName System.Speech
(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices().VoiceInfo.Name
```

Weights download from Hugging Face on first use and are cached in
`%USERPROFILE%\.cache\huggingface`. At start-up the server warms both directions
end to end and prints which engines answered:

```
voice: speaking with Kokoro on torch
voice: speaking checked end to end (kokoro-torch)
voice: hearing with faster-whisper on cpu (base.en)
voice: hearing checked end to end
voice: warm in …s
```

**Speed:** without MLX, Kokoro on the CPU is noticeably slower than on a Mac's GPU.
With an NVIDIA card and the CUDA build of torch, it's fast. Measure your own with
`python check_voice.py`.

**Kokoro and espeak-ng:** `misaki[en]` normally brings its own copy of espeak-ng.
If the log mentions espeak, install espeak-ng from its GitHub releases page
(the `.msi`) and restart the backend.

**GPU hearing:** `WHISPER_DEVICE=cuda` needs NVIDIA's cuBLAS 12 and cuDNN 9 DLLs on
PATH (CUDA Toolkit 12 + cuDNN 9). If the log says `HEARING IS BROKEN` with a
missing `cudnn`/`cublas` DLL, go back to `WHISPER_DEVICE=cpu` and `WHISPER_COMPUTE=int8`.

**Saying "Bob":** hands-free is **on by default**. Anywhere in the app, say
*"Bob"* and what you want in one breath (*"Bob, I'm drained, give me 15
minutes"*), or *"Bob"*, wait for the chime, then talk. The browser asks for the
microphone the first time. Turn it off in the settings sheet (gear icon on
Today), or by holding the mic button down.

**The wake-word caveat:** spotting "Bob" uses the browser's speech
recogniser, which sends audio to Google (Chrome) or Microsoft (Edge) while
hands-free is armed, so it needs an internet connection. The command after the wake word is transcribed locally.
Firefox has no speech recogniser, so no wake word there. A fully on-device wake
word (openWakeWord with `onnxruntime-web`) is not built yet.

If the microphone returns 502, run `python check_voice.py` from `backend\` (server
stopped). It speaks with Kokoro, encodes that as webm/opus the way the browser does
(needs ffmpeg), and transcribes it with nothing catching the error, so you see the
real trace.

---

## Recipe photos

The local picture model (SD-Turbo, ~2.5 GB) competes with the chat model for GPU
memory, so by default the server doesn't paint:

1. `IMAGE_PROVIDER=none` in `.env`.
2. Opening a recipe adds it to `media\photo-queue.json`
   (the app shows "Queued for the next photo run").
3. You run the tool when you're not cooking:

```powershell
cd happy-bite\backend; .\.venv\Scripts\Activate.ps1
python tools\make_photos.py --list          # what's waiting
python tools\make_photos.py                 # paint them, then exit
python tools\make_photos.py --watch         # keep checking every 20 s (--every N)
python tools\make_photos.py --again rec_id  # redo one
```

On an NVIDIA GPU (with CUDA torch) a picture takes seconds; on the CPU expect
minutes each. Files go to `media\recipes\`, indexed in `media\recipes.json`. Photos
already on disk always show. To paint on demand, set `IMAGE_PROVIDER=local` and
`IMAGE_MODEL=stabilityai/sd-turbo`.

---

## Recipe corpus, templates and the catalogue

| File | In git | Role |
|---|---|---|
| `shared\catalog.json` | yes | every ingredient the app knows (ids, categories, units) |
| `shared\recipes.json` | yes | curated seed recipes shown in Recipes |
| `shared\receipt-sample.json` | yes | the sample receipt |
| `shared\kaiser_recipes.jsonl` | **no** (~2.6 GB) | source corpus for RAG and templates — a JSONL export of [`Kaiser1308/CookingRecipes`](https://huggingface.co/datasets/Kaiser1308/CookingRecipes) |

The corpus never reaches the browser. The RAG index (`media\recipe-rag.sqlite3`)
is built in the background at start-up and rebuilt only when the source changes.

Maintenance scripts (run from `happy-bite\` with the backend venv active):

```powershell
python build_catalogue.py --dry        # add corpus ingredients to catalog.json; --dry writes nothing
python import_corpus.py --count 79     # restructure corpus recipes into app recipes with your model (--dry)
python ..\audit.py                     # read shared\recipes.json as a cook would and list faults
```

`build_catalogue.py` never changes an existing entry (your stock points at those
ids); new entries are marked `"added": "corpus"` with a usage count so the prompt
can send only the most useful ones. `import_corpus.py` is slow on purpose (about a
minute a recipe on a 4B model), saves after each accepted recipe and can be
resumed.

---

## Training your own model

`happy-bite\training\` fine-tunes Qwen with QLoRA. Optional, and needs an NVIDIA
GPU: `Qwen/Qwen2.5-7B-Instruct` wants ~24 GB of VRAM; set
`BASE_MODEL=Qwen/Qwen2.5-1.5B-Instruct` in `training\.env` for smaller cards.

The 4-bit loading uses `bitsandbytes`, which is most reliable on Linux. **Run
training inside WSL2** (Ubuntu) if it fails natively — the commands are then the
Linux ones:

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
| `start http://localhost:8000/api/health/models` | browser | each model: configured, packages present, loaded, reachable |
| `.\run.ps1` | `happy-bite\backend` | missing packages, whether torch sees CUDA, whether ffmpeg is on PATH |
| `python check_voice.py` | `happy-bite\backend` (server stopped) | full trace of a speak → encode → hear round trip; saves `voice-check.wav` |
| `python check_model.py` | repo root | what your model server is really doing: model loaded, tokens/s, whether it reasons, time for a real recipe (reads `backend\.env`; `--url`, `--model`, `--budget`, `--wait`) |
| `python audit.py` | repo root | faults in the seed recipes |
| `npm test` | `happy-bite\frontend` | engine, wake word, stock commands and cooking-window tests |
| `nvidia-smi` | anywhere | how much VRAM each model server is holding |

---

## Project layout

```
KitchenBuddy\
├── README.md                       macOS guide
├── README-Windows.md               Windows guide (this file)
├── audit.py, check_model.py        diagnostics
├── data\                           standalone ChromaDB data engine prototype (see data\README_DATA.md)
└── happy-bite\
    ├── backend\
    │   ├── app\
    │   │   ├── main.py             app, start-up, serves frontend\dist
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
    │   │   ├── prefetch.py         pre-answered cooking questions
    │   │   ├── imagestore.py       recipe photo store
    │   │   ├── photoqueue.py       queue for tools\make_photos.py
    │   │   ├── warmup.py           model warm-up
    │   │   └── providers\          llm.py, media.py (images, voice), speech_local.py
    │   ├── tools\make_photos.py
    │   ├── check_voice.py
    │   ├── run.ps1                 Windows
    │   ├── run.sh, setup_local.sh  macOS
    │   ├── requirements-windows.txt
    │   ├── requirements-mac.txt
    │   └── media\                  generated, not in git
    ├── frontend\
    │   ├── src\
    │   │   ├── core\               offline engine, equipment, intent, cooking windows (no model)
    │   │   ├── state\              kitchen store, UI state, action executor
    │   │   ├── screens\            Today, Kitchen, Recipes, Receipt, Shopping, ChatScreen, CookMode
    │   │   ├── sheets\             bottom sheets (recipe, create, people, equipment…)
    │   │   ├── components\         Chat, AdaptPanel, PhotoStrip, VoiceAgent, Charts…
    │   │   ├── lib\                api, store (IndexedDB), voice, wake, speechqueue, command, stockcmd
    │   │   └── styles\
    │   ├── public\                 icon, manifest, service worker
    │   ├── dist\                   production build (npm run build)
    │   └── node_modules\           npm install, not in git
    ├── shared\                     catalogue, seed recipes, corpus (corpus not in git)
    ├── training\                   optional LoRA / QLoRA fine-tuning
    ├── build_catalogue.py
    └── import_corpus.py
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `running scripts is disabled on this system` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `py` or `python` not found | reopen PowerShell after installing; or untick the Microsoft Store "App execution aliases" for python |
| Feature shows as off / "package missing" but the import works in your shell | server running under a different Python — use `.\run.ps1` |
| pip fails on torch / kokoro | venv is Python 3.13+; rebuild on 3.12 |
| `run.ps1` says torch is CPU-only on an NVIDIA machine | reinstall torch from pytorch.org's CUDA index (see Quick start) |
| Chat is off, `/api/health/models` says unreachable | the model server isn't running, or `OPENAI_BASE_URL` has the wrong port (11434 Ollama, 1234 LM Studio) |
| Frontend shows every API call failing | backend not running on port 8000 |
| Recipe takes minutes | the model is reasoning, or is too big for your VRAM — `LLM_THINK=false`, a smaller model, `nvidia-smi` |
| `The number of tokens to keep from the initial prompt is greater than the context length` | loaded context too small — raise it in the server, set `LLM_CONTEXT`, lower `LLM_IDS_CHARS` / `LLM_JSON_TOKENS` |
| 400 BadRequest from LM Studio | non-standard fields; the app retries without them, or set `LLM_EXTRAS=false` |
| Everything slow | both Ollama and LM Studio holding a model — quit one; `IMAGE_PROVIDER=none` while cooking |
| `HEARING IS BROKEN` mentioning `cudnn` / `cublas` | `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE=int8` |
| Microphone returns 502 | `python check_voice.py` |
| Voice sounds robotic | Kokoro failed and the Windows system voice took over — the log says why |
| No microphone prompt in the browser | use `http://localhost`, not the PC's IP, for dev — browsers only allow the mic on localhost or HTTPS |
| Tablet can't reach the PC | allow Python in Windows Defender Firewall |
| Recipe opens with *"Check this one before you start"* | the model got it wrong twice; the box says what — try again, or use a larger model |
| Saying "Bob" does nothing | look under the mic button: it says why (microphone refused, no internet for the browser's recogniser, another tab holding the mic). Firefox has no recogniser |
| Receipt reading off | `winget install UB-Mannheim.TesseractOCR` |
| Voice models reload after every save | `--reload` — use `.\run.ps1 -NoReload` |

---

## Known limitations

- **Receipt scanning in the app is simulated** — the server can read receipts, the
  Receipt tab doesn't call it yet.
- **Wake word** is on by default and uses the browser's cloud speech recogniser
  while armed.
- **Small local models** follow the recipe rules most of the time, not always:
  the checks catch wrong times and invented ingredients, not odd cooking choices
  (five garlic cloves for two). A larger model writes better recipes.
- **Not yet run on Windows by the maintainers.** The Windows voice fallback,
  `run.ps1` and this guide were written against the code; report anything that
  doesn't match.
- **Storage is IndexedDB** — fine for one household, no sync between devices.
- **A profile is a name plus what someone won't eat.** No health data, deliberately.
- **Cooking mode walks the original steps**, not an equipment adaptation.
- **Fine-tuning** has scripts and self-tests but has not been run end to end in
  this repo.
