# Happy Bite — KitchenBuddy upgrade

This delivers the foundation for the four threads you asked for: **KitchenBuddy
acting on the backend**, **catalog extensibility**, **the 2.2M-recipe / training
alignment**, and a **fully-local voice stack**. Every file below is drop-in —
nothing here was run on your machine (there's no GPU and no link to your
computer in this session), so treat it as a reviewed patch set, wire it into the
repo, and run it locally.

## The one idea that makes it all work

Your household's data — stock, expiry, saved recipes — lives **in the browser**
(IndexedDB, `store.js`). The server is stateless. So KitchenBuddy can't "write to
a database" when you say *"I used 100 g of milk."* Instead:

1. The model calls a **tool** (`adjust_stock`).
2. The server **validates** it against the catalog (`actions.py`) — untrusted
   model output, same discipline as `clean_recipe`.
3. The server streams it to the browser as an `action` **event**.
4. The browser **executes** it against its own store and the screen updates.

The model's tool_result is a synthetic "ok" so it can finish its sentence; the
browser is the real executor. This is why the assistant can only do things you
could already do by tapping — `useApplyAction` calls the very same store methods.

```
you speak ─▶ /api/chat ─▶ Qwen/Claude emits tool_use
                              │
             actions.normalize (validate vs catalog)
                              │
        SSE {type:"action"} ──▶ useApplyAction ──▶ kitchen.jsx ──▶ IndexedDB + UI
```

## What's included in this delivery

### 1. KitchenBuddy action layer (the heart)

| File | Change |
|---|---|
| `backend/app/actions.py` | **NEW.** Tool schemas + `normalize()` that turns each tool call into a safe action. Tools: `adjust_stock`, `add_stock`, `set_expiry`, `add_custom_ingredient`, `save_recipe`, `add_to_shopping`, `set_timer`. |
| `backend/app/providers/llm.py` | Adds `chat_actions()` — the streaming tool loop for **both** Anthropic and OpenAI/Ollama. Keeps `stream()` and `json()`. |
| `backend/app/prompts.py` | CHEF prompt gains tool guidance + custom-item awareness; `chef_system()` now receives the valid ingredient ids. |
| `backend/app/api.py` | `/api/chat` runs the tool loop and emits `action` events; `/api/status` reports `wakeWord`; voice served with the provider's real MIME type. |
| `frontend/src/state/actions.jsx` | **NEW.** `useApplyAction()` maps each action to a store mutation + a one-line receipt. |
| `frontend/src/state/kitchen.jsx` | New mutations: `adjustStock`, `addStockItem`, `setExpiry`, `addCustom`, `addToShopping`. |
| `frontend/src/components/Chat.jsx` | `useChat(context, greeting, onAction)` handles `action` events; the thread renders receipts + a "Cook it" card for saved recipes. |
| `frontend/src/screens/ChatScreen.jsx` | Passes `onAction`; adds the hands-free toggle. |
| `frontend/src/core/engine.js` | `daysLeft()` honors an explicit `expires` date (from `set_expiry`). |
| `frontend/src/styles/chat-actions.css` | **NEW.** Styles for the receipts (theme-aware). |

Try: *"I used 100 ml of milk and 2 eggs"*, *"the yoghurt is good for 2 more
days"*, *"put rice and chickpeas on the shopping list"*, *"set a timer for 12
minutes"*, *"save this as a recipe"*.

> **Also apply `onAction` in cooking mode.** `CookMode.jsx` uses `useChat` too;
> pass `useApplyAction()` as the third argument there so timers/stock work while
> cooking. (I didn't have that file; it's a one-line change mirroring ChatScreen.)

### 2. Catalog extensibility

Already wired by the above: `catalog.py` merges custom items, the browser
persists them in `customs`, and every chat turn sends them back so the model
**stays aware of what it created** (listed in the prompt). `add_custom_ingredient`
and `add_stock`-with-a-name let KitchenBuddy invent an item on the spot — your
"if it's not in the catalog, we should be able to put anything in there, and the
model should know the custom items." Nothing new to install.

### 3. Recipes tab + the 2.2M recipes

Confirmed correct as-is: the Recipes tab shows the **curated pre-done recipes +
your saved ones** (`RECIPES.concat(myRecipes)`), and the 2.2M dataset **never
ships to the browser** — it's Qwen's knowledge, exactly your model. The pre-done
recipes stay so users can browse. No change needed; the "Yours" filter already
exists.

### 4. Training alignment

| File | Change |
|---|---|
| `training/build_app_dataset.py` | **NEW.** Converts your Kaiser recipes into **app-schema** training examples: ingredients that map to a catalog id become `needs`/`seasoning`; everything else is preserved verbatim in `extras`; instructions become `steps`. Teaches Qwen to emit recipes the app drops straight into `clean_recipe`. |

`build_dataset.py` (unchanged) teaches the *source* schema (free text). The new
builder teaches the *app* schema. Run whichever matches how you want Qwen to
answer — for the in-app "create a recipe and show it cleanly" flow you want the
app schema:

```bash
cd training
python build_app_dataset.py            # reads data/kaiser_recipes.jsonl
python build_app_dataset.py --peek     # eyeball 3 converted examples first
python build_app_dataset.py --max 100000
# then point train_qlora.py at data/app_train.jsonl / data/app_val.jsonl
```

It prints an **ingredient grounding rate** (% mapped to a catalog id). Raise it
by growing `shared/catalog.json` or the `SYNONYMS` map at the top of the builder.
I verified the converter on samples (see below) — pasta/tomato/garlic/rice/soy
map, mayonnaise/artichoke fall to `extras`.

> **Actions at inference** don't need fine-tuning to *start*: Qwen2.5-7B-Instruct
> supports tool-calling natively via Ollama, so `chat_actions` works against the
> base instruct model. Fine-tuning on app-schema recipes mainly improves recipe
> quality and id-grounding. A later pass can add tool-call examples if you want
> the actions crisper.

### 5. Fully-local voice

| File | Change |
|---|---|
| `backend/app/providers/speech_local.py` | **NEW.** `LocalSpeech`: faster-whisper (STT, "hears us") + Piper (TTS, "speaks to us"). Same interface as the OpenAI `Speech`, so `/api/speech/*` and the browser are unchanged. |
| `backend/app/providers/media.py` | `make_speech` gains the `local` branch; `mime` attribute so WAV is served correctly. |
| `backend/app/config.py` | `speech_provider=local` settings: `whisper_model/device/compute`, `stt_language`, `piper_voice/config`, `wake_word`. |
| `frontend/src/lib/wake.js` | **NEW.** Wake-word spotter (browser SpeechRecognition) → hands off to `voice.listen` (which uses local Whisper when the local stack is on). |
| `frontend/src/screens/ChatScreen.jsx` | Hands-free toggle in the header. |

Setup (see `docs/VOICE.md`):

```bash
pip install faster-whisper piper-tts
# download one Piper voice (.onnx + .onnx.json) from
#   https://huggingface.co/rhasspy/piper-voices   (e.g. fr_FR-siwis-medium)
```

`.env`:
```
SPEECH_PROVIDER=local
WHISPER_MODEL=base            # or small / distil-large-v3
PIPER_VOICE=/path/to/fr_FR-siwis-medium.onnx
WAKE_WORD=hey chef
# and, for a local Qwen:
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b-instruct
```

The "multiple models" you described are now: **Whisper** (hears), **Qwen**
(thinks/acts), **Piper** (speaks), plus the **browser wake-word** spotter.

## The three "models" of the voice loop, and the one honest caveat

The **conversation** is fully local: Whisper + Qwen + Piper all run on your
machine, no tokens, nothing leaving the network. The **wake word** as shipped
uses the browser's speech recogniser to spot the phrase — which in Chrome
streams audio to Google *while armed* (the UI says so; it's a deliberate toggle).
The fully on-device wake word (openWakeWord via onnxruntime-web, no cloud even
while armed) is the recommended next upgrade; it needs model files I couldn't
fetch or verify here. `docs/VOICE.md` specs it. Everything else runs today.

## Not yet done / next increments

- **openWakeWord on-device** wake word (removes the last cloud hop).
- **`CookMode.jsx`**: pass `useApplyAction()` to its `useChat` (one line).
- **Optional**: tool-call fine-tuning examples for sharper actions on smaller
  local models; a bulk pre-index of the 2.2M for retrieval if you ever want
  "search the corpus" rather than "generate from learned knowledge".
