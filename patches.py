"""Every edit this upgrade makes to a file that already exists.

Each entry is (anchor, replacement). The anchor must appear EXACTLY ONCE in the
file or the patch refuses to run — no fuzzy matching, no "closest line", no
guessing. A repo that has drifted gets a clear report naming the file and the
text it expected, which you can then apply by hand in thirty seconds.

`mark` is a string that, if already present, means this patch has been applied
before; it makes the installer safe to re-run.
"""

P = {}


def patch(path, anchor, replacement, mark):
    P.setdefault(path, []).append({"anchor": anchor, "new": replacement, "mark": mark})


# =====================================================================
# backend/app/config.py — the new settings
# =====================================================================

patch(
    "backend/app/config.py",
    '    image_device: str = "auto"                       # auto | mps | cuda | cpu\n',
    '''    image_device: str = "auto"                       # auto | mps | cuda | cpu
    image_count: int = 3                             # pictures kept per recipe (1-6)
    image_prefetch: bool = True                      # start them when a recipe is opened
''',
    "image_count:",
)

patch(
    "backend/app/config.py",
    '''    # Wake word (browser side reads this from /api/status)
    wake_word: str = "hey chef"
''',
    '''    # Wake word (browser side reads this from /api/status)
    wake_word: str = "hey chef"

    # Answers written before you ask them (see app/prefetch.py)
    prefetch_enabled: bool = True
    prefetch_count: int = 4                          # questions parked per cooking step

    # Load the models at start-up, and stop Ollama unloading Qwen between
    # questions — usually the single largest delay in a cooking session.
    warm_models: bool = True
    llm_keep_alive: str = "1h"

    # Reading till receipts (see app/receipt.py)
    ocr_languages: str = "fra+eng"                   # tesseract language packs
''',
    "prefetch_count:",
)

# =====================================================================
# backend/app/api.py — say WHY something failed
# =====================================================================

patch(
    "backend/app/api.py",
    '''def need(request: Request, name: str, what: str):
    s = svc(request, name)
    if not s:
        raise HTTPException(503, f"{what} is switched off on the server.")
    return s
''',
    '''def need(request: Request, name: str, what: str):
    s = svc(request, name)
    if not s:
        why = getattr(request.app.state, f"{name}_why", None)
        raise HTTPException(503, why or f"{what} is switched off on the server.")
    return s


def _why(e: Exception) -> str:
    """A sentence someone can act on.

    "ModuleNotFoundError" tells you something is missing but not what, which is
    a bug report rather than a message. The commonest failure by far on a fresh
    machine is a provider package that was never installed, so name it.
    """
    if isinstance(e, ModuleNotFoundError):
        return (f"the '{e.name}' package isn't installed in the backend venv. "
                "Run backend/setup_local.sh, or see /api/health/models.")
    text = str(e).strip()
    return f"{type(e).__name__}: {text}" if text else type(e).__name__
''',
    "def _why(e: Exception)",
)

patch(
    "backend/app/api.py",
    '        raise HTTPException(502, f"The picture couldn\'t be made ({type(e).__name__}).") from None\n',
    '        raise HTTPException(502, f"The picture couldn\'t be made \\u2014 {_why(e)}") from None\n',
    'picture couldn\'t be made \\u2014',
)

patch(
    "backend/app/api.py",
    '        raise HTTPException(502, f"Voice failed ({type(e).__name__}).") from None\n',
    '        raise HTTPException(502, f"Voice failed \\u2014 {_why(e)}") from None\n',
    "Voice failed \\u2014",
)

patch(
    "backend/app/api.py",
    '        raise HTTPException(502, f"Couldn\'t make out the recording ({type(e).__name__}).") from None\n',
    '        raise HTTPException(502, f"Couldn\'t make out the recording \\u2014 {_why(e)}") from None\n',
    "make out the recording \\u2014",
)

patch(
    "backend/app/api.py",
    '        "wakeWord": settings().wake_word or None,\n',
    '''        "wakeWord": settings().wake_word or None,
        # When a capability is off, say why in the same breath. The browser
        # shows this instead of hiding a button with no explanation.
        "imagesWhy": getattr(request.app.state, "images_why", None),
        "voiceWhy": getattr(request.app.state, "speech_why", None),
        "picturesPerRecipe": getattr(settings(), "image_count", 3),
        "prefetch": bool(svc(request, "prefetch")),
''',
    '"imagesWhy":',
)

# =====================================================================
# backend/app/actions.py — a new shelf when there isn't one
# =====================================================================

patch(
    "backend/app/actions.py",
    '''def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:24]
    return "u_" + s if s else ""
''',
    '''def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:24]
    return "u_" + s if s else ""


# A shelf the kitchen doesn't have yet. "I bought 300 g of chicken" in a kitchen
# with no meat section should make the section, not quietly file it under Other:
# a catalogue that can't grow is why people stop using an app like this after a
# fortnight. The browser creates the shelf and persists it, same as a custom
# ingredient; nothing is stored here.
CAT_TINTS = ["var(--cat-1)", "var(--cat-2)", "var(--cat-3)",
             "var(--cat-4)", "var(--cat-5)", "var(--cat-6)"]


def _category(raw_name: Any) -> tuple[str, dict | None]:
    """(category id to use, the category to create first or None)."""
    name = str(raw_name or "").strip()
    if not name:
        return "other", None
    known = _cats()
    if name in known:
        return name, None
    slug = _slug(name)[2:]                      # _slug prefixes "u_"; shelves don't
    if slug in known:
        return slug, None
    if not slug:
        return "other", None
    tint = CAT_TINTS[sum(ord(c) for c in slug) % len(CAT_TINTS)]
    return slug, {"id": slug, "name": (name[:1].upper() + name[1:])[:32],
                  "tint": tint, "custom": True}
''',
    "def _category(raw_name",
)

patch(
    "backend/app/actions.py",
    '''            cat = raw.get("category") if raw.get("category") in _cats() else "other"
            unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
            action["create"] = {"id": iid, "name": nm, "category": cat, "unit": unit, "shelfLife": 14}
''',
    '''            unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
            cat, new_cat = _category(raw.get("category"))
            action["create"] = {"id": iid, "name": nm, "category": cat, "unit": unit, "shelfLife": 14}
            if new_cat:
                action["createCategory"] = new_cat
''',
    'action["createCategory"] = new_cat',
)

patch(
    "backend/app/actions.py",
    '''        cat = raw.get("category") if raw.get("category") in _cats() else "other"
        unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
''',
    '''        cat, new_cat = _category(raw.get("category"))
        unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
''',
    "\n        cat, new_cat = _category(",
)

patch(
    "backend/app/actions.py",
    '''        return ({"kind": "add_custom", "id": iid, "name": nm, "category": cat, "unit": unit, "shelfLife": life},
                f"Added {nm} to the catalogue (id {iid}).")
''',
    '''        made = {"kind": "add_custom", "id": iid, "name": nm, "category": cat,
                "unit": unit, "shelfLife": life}
        if new_cat:
            made["createCategory"] = new_cat
        return made, f"Added {nm} to the catalogue (id {iid})."
''',
    'made["createCategory"] = new_cat',
)

# =====================================================================
# backend/app/prompts.py — hearing "I bought"
# =====================================================================

patch(
    "backend/app/prompts.py",
    "    parts = [CHEF, _ids_block(ids, customs)]\n",
    "    parts = [CHEF, STOCK_TALK, _ids_block(ids, customs)]\n",
    "[CHEF, STOCK_TALK,",
)

patch(
    "backend/app/prompts.py",
    '''def step_image_prompt(name: str, step: str, cue: str | None) -> str:''',
    '''# Kept apart from CHEF so the original prompt stays readable and this can be
# tuned on its own. It exists because "I bought 300 grams of chicken" and "I
# used 300 grams of chicken" are opposite instructions that sound alike, and a
# 7B model that guesses wrong silently corrupts the kitchen.
STOCK_TALK = """Keeping the kitchen honest is part of the job, not an extra.

When they tell you something ARRIVED - "I bought 300 grams of chicken", "we
picked up two litres of milk", "there's a kilo of rice in the cupboard now" -
call add_stock straight away with the amount and unit they said, then reply in
one short line. Do not ask permission first.

If the thing has no id in the list, still call add_stock: pass `name`, a
sensible `unit` (g for what you weigh, ml for what you pour, u for what you
count) and the `category` you would actually put it under. If none of the
existing categories fits, name the one you want and it will be created - do
not force something into "other" to avoid inventing a shelf.

When they say something was USED, FINISHED or SPILLED, that is adjust_stock,
not add_stock. Listen for which of the two you are being told.

Never invent an amount. If they say "I bought chicken" with no quantity, add one
normal shop pack and say what you assumed, so they can correct you in a word."""


def step_image_prompt(name: str, step: str, cue: str | None) -> str:''',
    "STOCK_TALK = ",
)

# =====================================================================
# frontend/src/lib/api.js — the new calls
# =====================================================================

patch(
    "frontend/src/lib/api.js",
    "export const makeImage = (body) => post(\"/api/images\", body, 120000);\n",
    '''export const makeImage = (body) => post("/api/images", body, 120000);

/* ---- recipe pictures: a set, made on the server and kept there ---- */

export const makeRecipeImages = (recipe) => post("/api/recipes/images", {
  id: recipe.id, name: recipe.name || "",
  cuisine: recipe.cuisine || null, description: recipe.description || null
}, 20000);

export const recipeImages = (id) =>
  call(`/api/recipes/images/${encodeURIComponent(id)}`, {}, 10000).then(r => r.json());

export const clearRecipeImages = (id) =>
  call(`/api/recipes/images/${encodeURIComponent(id)}`, { method: "DELETE" }, 10000).then(r => r.json());

/* ---- answers written before the question ---- */

/* Fire and forget: it warms the cache for a step, and a failure costs nothing. */
export const prefetchStep = (recipe, step, serves) =>
  post("/api/cook/prefetch", { recipe, step, serves }, 8000).catch(() => null);

/* A parked answer, or null. On a short leash on purpose — waiting on the
   endpoint that exists to remove a delay would be a poor trade. */
export async function quickAnswer(recipeId, step, question, ms = 1200) {
  try {
    const res = await call("/api/cook/quick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipeId, step, question })
    }, ms);
    if (res.status === 204) return null;
    return await res.json();
  } catch { return null; }
}

/* ---- a photograph of a till receipt ---- */

export async function readReceipt(file, custom) {
  const form = new FormData();
  form.append("photo", file, file.name || "receipt.jpg");
  form.append("custom", JSON.stringify(custom || {}));
  return (await call("/api/receipt/read", { method: "POST", body: form }, 120000)).json();
}

/* What is actually loaded, and for anything off, the reason in words. */
export const modelHealth = () => call("/api/health/models", {}, 8000).then(r => r.json());
''',
    "makeRecipeImages",
)

# =====================================================================
# frontend/src/lib/voice.js — don't read the same reply twice
# =====================================================================

patch(
    "frontend/src/lib/voice.js",
    "let current = null;\nlet currentResolve = null;\n",
    '''let current = null;
let currentResolve = null;

/* When the streaming queue (lib/speechqueue.js) has just read a reply aloud
   sentence by sentence, the same text arriving here a moment later — because
   useChat speaks the finished reply — is an echo, not a second thing to say.
   The queue marks what it said; speak() swallows the repeat once. */
let spokenMark = "";
let spokenAt = 0;

const flat = (t) => String(t || "").replace(/[*_#`>]/g, "").replace(/\\s+/g, " ").trim().toLowerCase();

export function markSpoken(text) {
  spokenMark = flat(text);
  spokenAt = Date.now();
}

function isEcho(text) {
  if (!spokenMark || Date.now() - spokenAt > 8000) return false;
  const said = flat(text);
  if (!said) return false;
  const nearly = (a, b) => a.startsWith(b.slice(0, Math.max(12, Math.floor(b.length * 0.9))));
  const same = said === spokenMark || nearly(spokenMark, said) || nearly(said, spokenMark);
  if (same) { spokenMark = ""; return true; }   // consumed: a real repeat still speaks
  return false;
}
''',
    "export function markSpoken",
)

patch(
    "frontend/src/lib/voice.js",
    '''export function speak(text, useServer) {
  stopSpeaking();
  const clean = String(text).replace(/[*_#`>]/g, "").trim();
  if (!clean) return Promise.resolve();
''',
    '''export function speak(text, useServer) {
  const clean = String(text).replace(/[*_#`>]/g, "").trim();
  if (clean && isEcho(clean)) return Promise.resolve();   // the stream already said it
  stopSpeaking();
  if (!clean) return Promise.resolve();
''',
    "if (clean && isEcho(clean))",
)

# =====================================================================
# frontend/src/components/Chat.jsx — feed the speech queue
# =====================================================================

patch(
    "frontend/src/components/Chat.jsx",
    'import * as voice from "../lib/voice.js";\n',
    'import * as voice from "../lib/voice.js";\nimport * as sq from "../lib/speechqueue.js";\n',
    'from "../lib/speechqueue.js"',
)

patch(
    "frontend/src/components/Chat.jsx",
    '        if (ev.type === "delta") { reply += ev.text; patchLast(() => ({ content: reply, status: null })); }\n',
    '        if (ev.type === "delta") { reply += ev.text; sq.feed(ev.text); patchLast(() => ({ content: reply, status: null })); }\n',
    "sq.feed(ev.text)",
)

# =====================================================================
# frontend/src/screens/CookMode.jsx
# =====================================================================

patch(
    "frontend/src/screens/CookMode.jsx",
    'import { useChat } from "../components/Chat.jsx";\n',
    '''import { useChat } from "../components/Chat.jsx";
import { useApplyAction } from "../state/actions.jsx";
import * as sq from "../lib/speechqueue.js";
import "../styles/happy-extra.css";
''',
    "useApplyAction",
)

patch(
    "frontend/src/screens/CookMode.jsx",
    """  const chat = useChat(context, null);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;
""",
    """  /* Third argument: the chef may change the kitchen while you cook. Without it
     "I bought 300 g of chicken" mid-recipe was heard, understood and dropped —
     cooking was the one screen that never carried an action out. */
  const applyAction = useApplyAction();
  const chat = useChat(context, null, applyAction);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;
  const [instant, setInstant] = useState(false);

  /* While a step is on screen, have the server write the answers this step
     invites — and the next step's too, because you almost always walk forward.
     It costs one background request and turns most questions into no request. */
  useEffect(() => {
    if (ui.server.chat) api.prefetchStep(recipe, i, serves);
  }, [i, recipe, serves, ui.server.chat]);

  /* Ask the chef. Parked answers first: a hit comes back in milliseconds with
     no model call at all. On a miss the reply streams, and the speech queue
     reads each finished sentence aloud rather than waiting for the last one. */
  const askChef = useCallback(async (said, aloud) => {
    if (ui.server.chat) {
      const hit = await api.quickAnswer(recipe.id, iRef.current, said);
      if (hit && hit.answer) {
        setInstant(true);
        if (aloud) { setVstate("speaking"); await voice.speak(hit.answer, useServer); }
        return hit.answer;
      }
    }
    setInstant(false);
    const spoken = aloud ? sq.arm({ useServer }) : null;
    try {
      const reply = await sendRef.current(said);
      if (spoken) { sq.disarm(); await spoken; }
      return reply;
    } catch (e) {
      sq.cancel();
      throw e;
    }
  }, [recipe, useServer, ui.server.chat]);
""",
    "const askChef = useCallback",
)

patch(
    "frontend/src/screens/CookMode.jsx",
    "  useEffect(() => () => voice.stopSpeaking(), []);\n",
    "  useEffect(() => () => { sq.cancel(); voice.stopSpeaking(); }, []);\n",
    "sq.cancel(); voice.stopSpeaking(); }, []);",
)

patch(
    "frontend/src/screens/CookMode.jsx",
    '          if (c.cmd === "stopChef")    { voice.stopSpeaking(); return hearNext(); }\n',
    '          if (c.cmd === "stopChef")    { sq.cancel(); voice.stopSpeaking(); return hearNext(); }\n',
    'sq.cancel(); voice.stopSpeaking(); return hearNext();',
)

patch(
    "frontend/src/screens/CookMode.jsx",
    """        // A real question — the chef answers out loud, shown as a short caption.
        setVstate("thinking");
        const reply = await sendRef.current(said);
        setAnswer(reply || "");
        hearNext();
""",
    """        // A real question — the chef answers out loud, shown as a short caption.
        setVstate("thinking");
        const reply = await askChef(said, true);
        setAnswer(reply || "");
        hearNext();
""",
    "await askChef(said, true)",
)

patch(
    "frontend/src/screens/CookMode.jsx",
    """    setVstate("thinking");
    const reply = await chat.send(q);
    setAnswer(reply || "");
    setVstate(live ? "listening" : "");
""",
    """    setVstate("thinking");
    const reply = await askChef(q, false);
    setAnswer(reply || "");
    setVstate(live ? "listening" : "");
""",
    "await askChef(q, false)",
)

patch(
    "frontend/src/screens/CookMode.jsx",
    """        {answer && (
          <div className="cook-answer">
            <span className="cook-answer-ic"><Icon name="chef" size={20} /></span>
            <p>{answer}</p>
          </div>
        )}
""",
    """        {answer && (
          <div className={"cook-answer" + (instant ? " is-instant" : "")}>
            <span className="cook-answer-ic"><Icon name="chef" size={20} /></span>
            <p>{answer}{instant && <span className="answer-instant">ready</span>}</p>
          </div>
        )}
""",
    'className="answer-instant"',
)

# =====================================================================
# frontend/src/screens/Kitchen.jsx — categories looked up live
# =====================================================================

patch(
    "frontend/src/screens/Kitchen.jsx",
    '''const CAT = Object.fromEntries(CATEGORIES.map(c => [c.id, c]));
const OTHER = CAT.other || { id: "other", name: "Other", tint: "var(--cat-other)" };

/* An ingredient id -> its category record {id, name, tint}. Safe on unknown
   or missing ids (the receipt passes "__none" for an unmatched line). */
export const catOf = (id) => CAT[E.ref(id).category] || OTHER;
''',
    '''const OTHER = { id: "other", name: "Other", tint: "var(--cat-other)" };

/* Looked up live rather than frozen into a map at import time: KitchenBuddy can
   invent a shelf mid-sentence ("I bought 300 g of chicken" in a kitchen with no
   meat section), and a snapshot taken when this module loaded would never see
   it. The list is short; the find is free. */
const catById = (id) => CATEGORIES.find(c => c.id === id);

/* An ingredient id -> its category record {id, name, tint}. Safe on unknown
   or missing ids (the receipt passes "__none" for an unmatched line). */
export const catOf = (id) => catById(E.ref(id).category) || catById("other") || OTHER;
''',
    "const catById = (id) =>",
)

# =====================================================================
# frontend/src/state/kitchen.jsx — remember invented shelves
# =====================================================================

patch(
    "frontend/src/state/kitchen.jsx",
    'import { INGREDIENTS, RECIPES, STARTING_STOCK } from "../data/index.js";\n',
    'import { CATEGORIES, INGREDIENTS, RECIPES, STARTING_STOCK } from "../data/index.js";\n',
    "CATEGORIES, INGREDIENTS, RECIPES",
)

patch(
    "frontend/src/state/kitchen.jsx",
    "  corrections: {}, customs: {}, taste: E.EMPTY_TASTE, filters: {}, myRecipes: [], history: [],\n",
    "  corrections: {}, customs: {}, customCats: {}, taste: E.EMPTY_TASTE, filters: {}, myRecipes: [], history: [],\n",
    "customCats: {},",
)

patch(
    "frontend/src/state/kitchen.jsx",
    """  Object.assign(INGREDIENTS, s.customs);
  return { ...s, receipt: null, ready: true };
""",
    """  Object.assign(INGREDIENTS, s.customs);
  /* Shelves the household invented, back on the list before anything renders —
     otherwise their items sit under "Other" until the next reload. */
  for (const c of Object.values(s.customCats || {})) {
    if (!CATEGORIES.some(x => x.id === c.id)) CATEGORIES.push(c);
  }
  return { ...s, receipt: null, ready: true };
""",
    "s.customCats || {}",
)

patch(
    "frontend/src/state/kitchen.jsx",
    "      addToShopping(ids) {\n",
    """      /* A new shelf. CATEGORIES is mutated in place because screens read it
         directly; the copy in customCats is what survives a reload. */
      addCategory(cat) {
        if (!cat || !cat.id) return null;
        if (!CATEGORIES.some(c => c.id === cat.id)) CATEGORIES.push(cat);
        set({ customCats: { ...get().customCats, [cat.id]: cat } });
        return cat.id;
      },

      addToShopping(ids) {
""",
    "addCategory(cat) {",
)

# =====================================================================
# frontend/src/state/actions.jsx — carry the new shelf out
# =====================================================================

patch(
    "frontend/src/state/actions.jsx",
    """      case "add_stock": {
        if (action.create) K.addCustom(action.create);
""",
    """      case "add_stock": {
        if (action.createCategory) K.addCategory(action.createCategory);
        if (action.create) K.addCustom(action.create);
""",
    "K.addCategory(action.createCategory);\n        if (action.create)",
)

patch(
    "frontend/src/state/actions.jsx",
    """      case "add_custom": {
        K.addCustom(action);
        return { text: `Added ${action.name} to the catalogue` };
      }
""",
    """      case "add_custom": {
        if (action.createCategory) K.addCategory(action.createCategory);
        K.addCustom(action);
        return { text: `Added ${action.name} to the catalogue` };
      }
""",
    "K.addCategory(action.createCategory);\n        K.addCustom(action);",
)

# =====================================================================
# frontend/src/screens/Shopping.jsx — the receipt button
# =====================================================================

patch(
    "frontend/src/screens/Shopping.jsx",
    'import { nameOf } from "./Today.jsx";\n',
    '''import { nameOf } from "./Today.jsx";
import ReceiptButton from "../components/ReceiptButton.jsx";
import "../styles/happy-extra.css";
''',
    "ReceiptButton",
)

patch(
    "frontend/src/screens/Shopping.jsx",
    '''      <h1 className="screen-title">Shopping</h1>
      <p className="empty-note">This fills itself from the meals you plan. Open a recipe and tap “Plan it” to start.</p>
''',
    '''      <h1 className="screen-title">Shopping</h1>
      <p className="empty-note">This fills itself from the meals you plan. Open a recipe and tap “Plan it” to start.</p>
      <ReceiptButton />
''',
    "<ReceiptButton />",
)

patch(
    "frontend/src/screens/Shopping.jsx",
    '''      <h1 className="screen-title">Shopping</h1>
      <section className="panel gauge">
''',
    '''      <h1 className="screen-title">Shopping</h1>
      <ReceiptButton compact />
      <section className="panel gauge">
''',
    "<ReceiptButton compact />",
)

# =====================================================================
# frontend/src/screens/Receipt.jsx — the real reader
# =====================================================================

patch(
    "frontend/src/screens/Receipt.jsx",
    'import * as E from "../core/engine.js";\n',
    'import * as E from "../core/engine.js";\nimport * as api from "../lib/api.js";\n',
    'import * as api from "../lib/api.js";',
)

patch(
    "frontend/src/screens/Receipt.jsx",
    '  const onFile = () => { ui.say("Reading the receipt"); setTimeout(load, 700); };\n',
    '''  /* The real reader, at last. The photo goes to the server: tesseract turns it
     into text and the assistant maps each line to the catalogue. Everything it
     isn't sure about lands in "needs a look" exactly as before, and a line you
     correct once stays corrected. The sample is still one tap away — it remains
     the fastest way to see this screen work. */
  const onFile = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (e.target) e.target.value = "";
    if (!file) return;
    ui.say("Reading the receipt\\u2026");
    try {
      const r = await api.readReceipt(file, k.customs);
      for (const l of r.lines) {
        if (l.raw in k.corrections) {
          const id = k.corrections[l.raw];
          Object.assign(l, id ? { id, confidence: 1, rejected: false }
                              : { id: null, rejected: true, confidence: 0 });
        }
      }
      setReceipt(r);
      ui.say(`${r.lines.length} lines read`);
    } catch (err) {
      ui.say(err.message || "That receipt couldn't be read.");
    }
  };
''',
    "api.readReceipt(file, k.customs)",
)

# =====================================================================
# frontend/src/sheets/RecipeSheet.jsx — a set of pictures
# =====================================================================

patch(
    "frontend/src/sheets/RecipeSheet.jsx",
    "export function PhotoButton({ recipe, onDone }) {\n",
    '''import PhotoStrip from "../components/PhotoStrip.jsx";
import "../styles/happy-extra.css";

export function PhotoButton({ recipe, onDone }) {
''',
    "PhotoStrip",
)

patch(
    "frontend/src/sheets/RecipeSheet.jsx",
    '''  const go = async () => {
    setBusy(true);
    try {
      const { url } = await api.makeImage({ kind: "dish", name: recipe.name, cuisine: recipe.cuisine, description: recipe.description });
      onDone(url);
    } catch (e) { ui.say(e.message); }
    setBusy(false);
  };
''',
    '''  const go = async () => {
    setBusy(true);
    try {
      /* A set, not a single take. One generated photo is a coin flip; three,
         framed and lit differently, give you something to choose between. The
         strip under the picture fills in as they arrive. */
      const { urls } = await api.makeRecipeImages(recipe);
      if (urls && urls.length) onDone(urls[0]);
      else ui.say("Painting a few pictures \\u2014 they'll appear in a moment.");
    } catch (e) { ui.say(e.message); }
    setBusy(false);
  };
''',
    "api.makeRecipeImages(recipe)",
)

patch(
    "frontend/src/sheets/RecipeSheet.jsx",
    '''            <div className="sheet-body">
              {r.description && <p className="lead">{r.description}</p>}
''',
    '''            <div className="sheet-body">
              <PhotoStrip recipe={r} />
              {r.description && <p className="lead">{r.description}</p>}
''',
    "<PhotoStrip recipe={r} />",
)

patch(
    "frontend/src/sheets/RecipeSheet.jsx",
    '''      <DishImage recipe={recipe} className="dish-photo dish-draft">
        <PhotoButton recipe={recipe} onDone={setPhoto} />
      </DishImage>
''',
    '''      <DishImage recipe={recipe} className="dish-photo dish-draft">
        <PhotoButton recipe={recipe} onDone={setPhoto} />
      </DishImage>
      <PhotoStrip recipe={recipe} auto={false} />
''',
    "<PhotoStrip recipe={recipe} auto={false} />",
)
