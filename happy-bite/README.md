# Happy Bite

An offline-first kitchen assistant: it tracks what's in your kitchen and
what's going off, proposes tonight's dinner from what you already have,
and — new in this version — an AI chef talks you through cooking it,
writes fresh recipes, imports recipes from cooking websites, generates
dish photos, and can speak and listen.

Rewritten from the original vanilla-JS PWA into **React (Vite)** on the
front and **Python (FastAPI)** on the back, so the AI features can run on
a real server that holds the API keys — including your own local Qwen
model instead of a cloud provider.

## The one line that governs everything

**CORE works offline and never calls a model. EXTRA is the AI layer and
degrades gracefully when it's off.**

- **CORE** — tonight's dish, stock, expiry, receipts, the shopping list.
  Runs entirely in the browser (IndexedDB). Pull the cable out and it
  still works. No model is ever consulted.
- **EXTRA** — the chef chat, recipe writing, link import, images, voice.
  Needs the backend. With the backend off, the app hides these and says
  so; CORE is untouched.

Model output is treated as untrusted input: every recipe the model
produces passes `clean_recipe`, which drops invented ingredient ids,
negative quantities and malformed steps rather than repairing them.

## Run it

Backend (holds all keys — nothing sensitive reaches the browser):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in a provider, or leave blank for CORE-only
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173, proxies /api to :8000
```

For the kitchen tablet, build once and let FastAPI serve it at a single
address: `cd frontend && npm run build`, then just run the backend — it
serves `frontend/dist` and the API together.

## Choosing the AI (or running with none)

Set these in `backend/.env`. Every capability is independent; a blank key
switches that one feature off and `/api/status` reports it.

- **Cloud:** `LLM_PROVIDER=anthropic` (Claude) or `openai`. Only the
  question and your stock list (names + quantities, never who lives in
  the house) leave the machine.
- **Local, no cloud:** run Qwen with Ollama or vLLM and set
  `LLM_PROVIDER=openai` with `OPENAI_BASE_URL` — see the Qwen preset in
  `.env.example`. Chat and recipes then run on your own hardware.
- **Images and server-side voice** currently need OpenAI (Claude can't
  make images). Voice falls back to the browser's own speech if off.

## Train your own recipe model

`training/` fine-tunes **Qwen3-8B** to speak Happy Bite's recipe format
natively. It's optional, needs a GPU, and runs on your machine — see
`training/README.md`. The training data is generated *through this app's
own validator*, so the model only ever learns in-schema recipes.

## Layout

```
shared/     one copy of the catalogue + seed recipes, read by both sides
backend/    FastAPI: chat (SSE), recipe generation, link import (SSRF-guarded),
            images, voice; app/recipes.py is the untrusted-output gate
frontend/   React: core engine (offline), state, charts, 5 screens,
            cooking mode, chef chat
training/   optional Qwen3-8B QLoRA fine-tuning kit
```

## What's verified

- Backend: 20 tests (`cd backend && python -m pytest -q`) — validator
  drops bad model output, SSE chat assembles correctly, link importer
  parses schema.org recipes and refuses private URLs, ingredient matcher.
- Frontend: 6 engine tests (`cd frontend && npm test`) + production build.
- The running app was smoke-tested on phone and tablet viewports.

## Known weaknesses (honest list)

- **Receipt scanning is still simulated** — it replays a sample receipt
  and applies your past corrections. Real OCR isn't wired yet.
- **12 seed recipes.** The catalogue is small; the AI and link import are
  meant to grow it.
- **Storage is IndexedDB**, not SQLite — fine for one household, not for
  sync across devices.
- **A profile is only a name + what someone won't eat.** No health data,
  deliberately — that keeps the app clear of medical-device territory.
- The AI/training pieces were written and unit-tested here but the
  fine-tuning itself has not been run end-to-end (no GPU in the build
  environment).


## When you want photos : 
cd backend && source .venv/bin/activate
       python tools/make_photos.py --list
       python tools/make_photos.py