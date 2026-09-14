# Happy Bite — six changes, and the two answers you asked for first

Drop-in patch set for `Infernoflamex/KitchenBuddy`. I could read the repo through
the project's synced copy but not write to it, so this is a bundle plus an
installer that makes exact, anchored edits and refuses to guess.

> **Copying the 12 files across by hand is not enough.** They are new *modules*;
> until the 41 edits below are made, nothing in the app imports them and nothing
> changes. `apply.py` is not a wrapper around copying files — it IS the other
> half of the work: the import lines, the new `api.js` calls, the `ReceiptButton`
> in Shopping, the `onAction` in CookMode, the stylesheet imports. Run it, or run
> `apply.py --show` and make the 41 edits by hand. `verify.py` says which half
> you're missing.

```bash
unzip happy-bite-upgrade.zip && cd happy-bite-upgrade
python3 apply.py --check ~/path/to/KitchenBuddy     # says what it would do
python3 apply.py         ~/path/to/KitchenBuddy     # does it
python3 apply.py --undo  ~/path/to/KitchenBuddy     # puts it back
python3 verify.py        ~/path/to/KitchenBuddy     # proves it's all wired in
python3 apply.py --show  ~/path/to/KitchenBuddy     # every edit as find/replace, by hand
```

`main.jsx` needs no change, by design — `happy-extra.css` is imported from the
component modules, the way `ChatScreen.jsx` already imports `chat-actions.css`.
Three of those five import lines are *patches to existing files*, so they only
exist once `apply.py` has run.

Then:

```bash
cd backend && source .venv/bin/activate && ./setup_local.sh
uvicorn app.main:app --reload --port 8000
open http://localhost:8000/api/health/models        # the new page that explains itself
```

41 edits across 14 existing files, 12 new files. Every anchor must appear
**exactly once** in its file or that edit is reported and skipped — nothing is
half-applied, and everything overwritten is copied to `.happybite-backup/`
first. Running it twice is safe.

---

## "How would I know everything is there?"

Fair question, and the honest answer is that you shouldn't take my word for it —
so `verify.py` checks it for you. It reads the repo, runs nothing, changes
nothing, and exits 0 only when all of this holds:

| It checks | Because |
|---|---|
| All 12 new files exist | the obvious one |
| Every relative import in every touched file points at a real file | a typo'd path is a blank screen, not an error you notice |
| All 18 cross-module calls resolve — `api.readReceipt`, `voice.markSpoken`, `sq.arm`, `K.addCategory` … | a component calling a function nobody exported fails only when you press the button |
| Every CSS class our JSX uses is defined in a stylesheet **that file itself imports** | exactly what you just asked about |
| The old code is actually gone: no `CAT[` in Kitchen.jsx, no `useChat(context, null)`, no `setTimeout(load, 700)` | a patch that "applied" but left the old path in place is worse than one that failed |
| Every `/api/...` the browser calls is declared by a route | otherwise it's a 404 you find by using the app |
| Every backend module compiles and every `from .x import y` resolves | |

On the stylesheet specifically: it is imported by a JS-side
`import "../styles/happy-extra.css"` — the same way `ChatScreen.jsx` already
imports `chat-actions.css`. Vite bundles CSS imported from a module, so nothing
goes in `main.jsx`. It is imported in **five** places: the two new components
import it themselves, so they work wherever you drop them, and CookMode,
Shopping and RecipeSheet import it for the markup they own.

**Two things this check caught in my own work, now fixed:**

1. `PhotoStrip.jsx` and `ReceiptButton.jsx` used classes from the stylesheet but
   relied on their *parent* to have imported it. True as shipped, but it would
   have broken silently the first time you rendered either one somewhere else.
   They now import it themselves.
2. `create_many()` numbered its takes from 1 on every call, so topping a recipe
   up from one picture to three **overwrote** the picture it already had. Now
   each take gets its own suffix; proved with a test that asserts three files on
   disk after a one-then-two generation.

## Your two questions

### "Why do I only `ollama pull qwen`? What about the other models?"

Because only one of the four is an Ollama model. Ollama serves **language**
models; the other three aren't, and each fetches its own weights the first time
it runs:

| Model | Job | Comes from |
|---|---|---|
| Qwen 2.5-7B | thinks, answers, calls tools | Ollama — **yes**, `ollama pull` |
| faster-whisper | hears you | pip package, downloads its weights on first use |
| Kokoro-82M | speaks to you | pip package, same |
| SD-Turbo | makes the pictures | HuggingFace via `diffusers`, ~2.5 GB on first use |

So there is nothing else to pull. What was missing was the **pip packages** —
which is also the answer to your other two problems.

### "Image generation doesn't work" / "I can't hear the model talking back"

One cause, two symptoms. `IMAGE_PROVIDER=local` makes `LocalImages._load()` do
`import torch` and `from diffusers import …`; `SPEECH_PROVIDER=local` makes
`speech_local.py` import `kokoro` and `soundfile`. **None of those four are in
`backend/requirements.txt`**, so they were never installed, and the import blew
up the first time you pressed the button. That is your `ModuleNotFoundError`.

It was invisible because `make_images()` handed back a provider object without
checking its packages existed. The server therefore told the browser
*"pictures: on"*, the browser drew the button, and the failure only surfaced as
a 502 whose body was `f"...({type(e).__name__})"` — the exception's class name
and nothing else. Hence a toast that says `ModuleNotFoundError` without saying
*which module*.

Three fixes, all in this bundle:

* `setup_local.sh` and the additions to `requirements.txt` install them.
* `make_images_checked` / `make_speech_checked` probe the imports at start-up.
  A missing package now switches the feature off **honestly** — the button
  disappears and `/api/status` carries the reason.
* The 502 bodies name the missing package instead of its exception class.

Voice has a second, independent trap worth knowing: when the server voice 502s,
`voice.speak()` silently falls back to the browser's `speechSynthesis`, so you
get *something* — but only where `prefs.speakReplies` is on, which is off by
default and only set by cooking mode's `startLive()`, which itself only runs
when `ui.server.voice` is true. With Kokoro missing, that chain never started.
Install the packages and it works; `/api/health/models` will tell you either way.

---

## What else is in here

### 1. Two or three pictures per recipe, kept

`backend/app/imagestore.py` — a recipe asks for its pictures by id, gets
whatever exists immediately, and a background worker makes the rest. One
picture at a time (SD-Turbo on a laptop is happiest that way, and a burst of
recipe cards shouldn't stall the chat). Files land in `media/recipes/`, and
`media/recipes.json` maps recipe → files so a `--reload` doesn't throw the whole
book away.

The takes are varied on purpose — overhead, three-quarter, close crop, each with
its own light and seed. One generated photo is a coin flip; three give you
something to choose between. `PhotoStrip.jsx` shows them under the recipe and
any of them can become the dish's photo with a tap.

`IMAGE_COUNT=3` in `.env` changes how many.

### 2. The chef stops thinking so long

Two mechanisms, both switched on by default:

**Answers written before you ask.** While a step is on screen, the server asks
Qwen — once, in the background — for the handful of questions that step invites
and their answers, for this step and the next. When you speak, the match is
checked first: a hit comes back in milliseconds with no model call at all. This
is `backend/app/prefetch.py`.

The matching is deliberately blunt and the bar is deliberately high, because a
confidently wrong answer at the hob is much worse than a two-second wait. Two
parked questions equally close to what you said returns nothing and lets the
model read the sentence properly. I tested it on 18 utterances — "how long?",
"how much longer", "stir?", "they're sticking" all hit; "what wine goes with
this", "set a timer", "is the oven on" all correctly fall through.

**It starts talking after the first sentence.** `frontend/src/lib/speechqueue.js`
sits on the reply stream: the moment a sentence is complete it goes to Kokoro
while the rest is still being written. Sentence-boundary detection knows that
"180 °C." and "approx." aren't ends of sentences. It marks what it said so the
finished reply isn't read out a second time.

**And the models are warm.** `backend/app/warmup.py` loads Whisper, Kokoro and
SD-Turbo at start-up and pings Qwen with `keep_alive: 1h` — Ollama otherwise
evicts the model after five minutes idle, which is usually the single largest
delay in a cooking session.

### 3. Receipt scanning, for real

The README called this "still simulated", and it was: `onFile` replayed
`SAMPLE_RECEIPT` after a 700 ms `setTimeout`. Now:

1. **Tesseract** turns the photograph into text. It is the character recogniser
   you described, and a receipt — dark monospace on pale paper — is the one
   thing it is very good at.
2. **Your existing Qwen** turns that text into catalogue lines. This is the part
   that needs judgement: `PLT FERM X6` is eggs, `CRM FRAICHE 30CL` is cream, and
   the `2,30` at the end of the line is money, not a quantity.

Splitting it this way means **no vision model** — the local 7B is enough.

Everything the model returns is validated the same way recipes are: an id that
isn't in the catalogue drops to `null` and lands in "needs a look", quantities
that don't parse become a sensible default, duplicates and price fragments are
stripped. Your remembered corrections still apply first, so a line you fix once
stays fixed.

The button is now on the **Shopping** screen as you asked — `capture="environment"`
opens the rear camera on a phone, and there's an "or choose an image" fallback
for the library or a laptop. It also replaces the simulated path on the Receipt
tab. The sample receipt is still one tap away.

Needs `brew install tesseract tesseract-lang` as well as the pip package —
`setup_local.sh` checks and tells you.

### 4. "I bought 300 grams of chicken"

Three things were in the way:

* **Cooking mode never carried actions out.** `CookMode.jsx` called
  `useChat(context, null)` — no `onAction`. Your upgrade doc flagged this as a
  one-line change that never got made. So mid-recipe, the sentence was heard,
  understood, turned into a valid `add_stock` call, streamed to the browser, and
  dropped on the floor. Fixed.
* **The model wasn't told the difference between buying and using.** "I bought
  300 g of chicken" and "I used 300 g of chicken" sound alike and are opposite
  instructions; a 7B that guesses wrong silently corrupts your kitchen. Added
  `STOCK_TALK` to the chef prompt, which says which tool is which, says to act
  before replying, and says not to invent amounts.
* **New categories were impossible.** `add_stock` could invent an *ingredient*
  but `actions.py` forced any unknown category to `"other"`. Now a category it
  doesn't recognise is created — `_category()` on the server proposes it,
  `addCategory` in `kitchen.jsx` persists it, and `Kitchen.jsx` looks categories
  up live instead of freezing them into a map at import time (which is why a
  new shelf would otherwise not appear until you reloaded).

So: *"I bought 300 grams of chicken"* → chicken exists, quantity added. *"I
bought 400 g of samphire"* → ingredient invented, filed under a shelf that is
created if there isn't one.

---

## `.env` for a fully local setup

```ini
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b-instruct
OPENAI_API_KEY=ollama

IMAGE_PROVIDER=local
IMAGE_MODEL=stabilityai/sd-turbo
IMAGE_COUNT=3                 # pictures per recipe
IMAGE_SIZE=512                # 768 is sharper and about twice as slow

SPEECH_PROVIDER=local
TTS_ENGINE=kokoro
KOKORO_LANG=f                 # f = French, a = US English
KOKORO_VOICE=ff_siwis
WHISPER_MODEL=base

PREFETCH_ENABLED=true
PREFETCH_COUNT=4              # questions parked per cooking step
WARM_MODELS=true
LLM_KEEP_ALIVE=1h
OCR_LANGUAGES=fra+eng
```

---

## What I verified, and what I didn't

**Verified here:** all 41 anchors resolve to exactly one place; the installer is
idempotent and `--undo` restores cleanly; every Python file parses; every
patched frontend file parses as JSX through esbuild; `verify.py` passes on a
reconstructed checkout (imports, cross-module calls, stylesheet reach, endpoint
coverage, removed-code checks); the prefetch matcher scores 18/18 on hit-and-miss
cases; the receipt validator correctly drops an invented id, a non-numeric
quantity, a duplicate line and an empty line; topping up a recipe's pictures
leaves three files on disk rather than overwriting one.

**Not verified — no GPU, no Mac, and no link to your machine in this session:**
nothing was actually run against your repo. SD-Turbo has not produced a picture,
Kokoro has not said a word, tesseract has not read a receipt, and `npm run build`
has not been run. Treat it as a reviewed patch set, not a tested release.

**The one thing I'd watch:** the streaming speech queue and `useChat`'s own
"speak the finished reply" both want to read the same text aloud. I added an
echo guard in `voice.js` (`markSpoken` / `isEcho`) that suppresses the repeat for
eight seconds on a ~90% prefix match. I could see the top of `useChat` but not
where it calls `speak()`, so if you ever hear a reply twice, that guard is where
to look — and `sq.arm()` in `CookMode.askChef` is the one line to remove to turn
streaming speech off entirely.
