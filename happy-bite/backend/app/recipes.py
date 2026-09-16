"""Recipe shape, and the gate every recipe passes before the app sees it.

MODEL OUTPUT IS UNTRUSTED INPUT. A model will invent an ingredient id,
send a negative quantity or forty steps. `clean_recipe` drops what it
doesn't recognise rather than repairing it — a silently "corrected"
recipe is worse than a rejected one. Same rules as the old
`validateRecipe` in the browser, now on the side that holds the key."""

import re
import secrets

MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
HEATS = ["Low", "Medium-low", "Medium", "Medium-high", "High"]

# Handed to the model as a tool schema (Anthropic) or described in the
# prompt (OpenAI-compatible). The model fills it; `clean_recipe` checks it.
RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Short dish name"},
        "minutes": {"type": "integer", "description": "Total time start to finish: the step minutes added up"},
        "complexity": {"type": "integer", "enum": [1, 2, 3]},
        "types": {"type": "array", "items": {"type": "string", "enum": sorted(MEAL_TYPES)}},
        "cuisine": {"type": "string"},
        "serves": {"type": "integer"},
        "description": {"type": "string", "description": "One appetising sentence"},
        "needs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Ingredient id from the list"},
                    "qty": {"type": "number", "description": "In the unit the id uses"},
                    "prep": {"type": "string", "description": "How it is cut or readied, a few words: 'diced', 'beaten'. Omit if nothing."},
                    "flexible": {"type": "boolean", "description": "true for a splash of oil etc."},
                },
                "required": ["id", "qty"],
            },
        },
        "seasoning": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "qty": {"type": "number"},
                    "essential": {"type": "boolean"},
                },
                "required": ["id", "qty"],
            },
        },
        "extras": {
            "type": "array", "items": {"type": "string"},
            # No example here. With one ("2 bay leaves") a 2B model put two bay
            # leaves in a spinach sauté, because the example was the only extra
            # it had ever been shown.
            "description": "Only ingredients this recipe uses that have no id in the list, with an amount, as a shopper would write them. Usually empty.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "do": {"type": "string", "description": "One action, short imperative"},
                    "why": {"type": "string", "description": "Only where it changes the outcome"},
                    "heat": {"type": "string", "description": "Only for a step on the hob or in the oven: Low | Medium-low | Medium | Medium-high | High | Oven 200 °C. Omit otherwise."},
                    "cue": {"type": "string", "description": "What to look for: 'oil shimmers'. Omit if nothing."},
                    "minutes": {"type": "number", "description": "How long this step really takes. Chopping an onion is 2, not 10."},
                    "uses": {"type": "array", "items": {"type": "string"},
                             "description": "Ingredient ids that go IN at this step, from this recipe's own needs and seasoning. Empty for a step that adds nothing."},
                },
                "required": ["do", "minutes"],
            },
        },
    },
    "required": ["name", "minutes", "needs", "steps"],
}

RECIPE_OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "recipes": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": RECIPE_SCHEMA,
        }
    },
    "required": ["recipes"],
}

RECIPE_IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "recipes": {
            "type": "array", "minItems": 2, "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "minutes": {"type": "integer"},
                    "complexity": {"type": "integer", "enum": [1, 2, 3]},
                    "cuisine": {"type": "string"},
                },
                "required": ["name", "description", "minutes", "complexity", "cuisine"],
            },
        }
    },
    "required": ["recipes"],
}

RULES = """Rules for the recipe:
- Ingredient ids ONLY from the list, quantities in the unit shown beside each id.
  Anything with no id goes in "extras" as plain text. Never invent an id.
- Seasonings (salt, spices, sauces) go in "seasoning"; "essential": true only if
  the dish fails without it.
- One action per step. If it needs "and then", split it. As few steps as the
  dish needs: three to eight.
- A step may only name ingredients that are in THIS recipe. Never mention a
  food the recipe does not contain: a rice dish does not talk about pasta.
  If a step uses it, it is in "needs", "seasoning" or "extras".
- "minutes" on a step is the time that step really takes, and any time written
  in "do" says the same number. The recipe's "minutes" is the steps added up.
- Each id is given as `id = Name (unit)`. Write about what the id MEANS, not
  what it is spelled like. `u_goat = Goat's cheese` is a cheese: it is
  crumbled over at the end, never seared four minutes a side.
- Oils, butter for frying and vinegars: a few spoonfuls, and "flexible": true.
  Never hundreds of millilitres.
- Quantities a person could eat: roughly 150-250 g a head of the main
  ingredient, not a kilo.
- "uses" lists the ids that go into the pan AT that step, so the cook knows
  what to reach for. Nothing that was added earlier.
- For anything seared, fried or grilled, give the time PER SIDE in "do":
  "four minutes on the first side, three on the second". A single total is
  useless to somebody standing over a pan.
- Every hob or oven step gets a "heat" and, where it helps, a "cue" — what you
  see, hear or smell. Cues beat timers: "oil shimmers", "edges going golden",
  "a drop of water skitters".
- "why" only where skipping it ruins the dish. Plain words, one line.
- Quantities for the number of people asked for (default 4)."""


# A seasoning is whatever the CATALOGUE says it is, and the catalogue decides
# in both directions. The old rule only moved one way — a seasoning filed under
# needs was moved across — so a whole vegetable the model called a seasoning
# stayed there. That is how garlic, onions and tomatoes ended up in the spice
# list. A model's opinion about what a seasoning is carries no weight here.
def _is_seasoning(iid: str, known: dict) -> bool:
    return known.get(iid, {}).get("category") == "seasoning"


# The most of a seasoning one person could plausibly eat. A 4B model guesses
# grams badly: it asked for 380 g of salt for three people, which is not a
# seasoning mistake, it is a medical one. Above this the number is dropped and
# the app says "to taste" — honest about not knowing, rather than confident and
# wrong. 8 g of salt a head is already generous; 8 g of paprika is a lot.
SEASON_PER_PERSON = {"g": 8.0, "ml": 10.0, "cl": 1.0, "l": 0.05, "u": 1.0}

# The same treatment for ordinary ingredients, which needed it just as badly:
# 1.7 kg of courgettes for three people, and 150 cl — a litre and a half — of
# olive oil. Generous rather than mean, because a cap that fires on a correct
# recipe is worse than no cap: 400 g a head of any one thing is a large plate.
NEED_PER_PERSON = {"g": 400.0, "ml": 400.0, "cl": 40.0, "l": 0.4, "u": 4.0}

# Things you pour rather than weigh. No sane recipe uses 400 ml of olive oil
# for four, and the model reaches for that number constantly, so these get a
# much tighter ceiling and are marked flexible — the app already knows how to
# say "a splash". Matched on the name because the catalogue has no field for
# it; crude, and still right far more often than 150 cl of oil.
POUR_WORDS = ("oil", "vinegar", "huile", "vinaigre")
POUR_PER_PERSON = {"g": 20.0, "ml": 20.0, "cl": 2.0, "l": 0.02, "u": 1.0}


def _need_qty(q: float, ing: dict, serves: int) -> tuple[float, bool]:
    unit = ing.get("unit", "g")
    name = str(ing.get("name", "")).lower()
    table = POUR_PER_PERSON if any(w in name for w in POUR_WORDS) else NEED_PER_PERSON
    cap = table.get(unit, 400.0) * max(1, serves)
    return (round(q, 2), False) if q <= cap else (round(cap, 2), True)


def _season_qty(q: float, unit: str, serves: int) -> tuple[float, bool]:
    cap = SEASON_PER_PERSON.get(unit, 8.0) * max(1, serves)
    return (round(q, 2), False) if q <= cap else (round(cap, 2), True)


def _slugish(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _as_known_id(text: str, known: dict) -> str | None:
    """Is this "extra" actually an ingredient id, or the name of one?

    The model was told to use ids for anything in the list and plain shopper's
    text for anything else. It did neither: it wrote `u_mozarella`, `goat` and
    `courgette` into extras — ids, for ingredients that ARE in the kitchen.
    Printed as written, the app showed a household "u_mozarella" on its
    shopping list, and the mozzarella they had just cooked with never counted
    as an ingredient they had used.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw in known:
        return raw
    slug = _slugish(raw)
    for candidate in (slug, f"u_{slug}"):
        if candidate in known:
            return candidate
    for iid, ing in known.items():
        if _slugish(ing.get("name", "")) == slug:
            return iid
    return None


def _resolve_id(value, known: dict) -> str | None:
    """`"Spinach (g)"` -> `spinach`.

    The id list is written `spinach = Spinach (g)`, and a 2B model copies the
    right-hand side: every id in its recipe was a name with a unit on it, the
    whole recipe cleaned down to nothing, and the cook got "didn't hold
    together" for a dinner that was fine. Only an exact id or an exact name
    is accepted, the same as for extras — nothing is guessed at."""
    if not isinstance(value, str):
        return None
    if value in known:
        return value
    return _as_known_id(re.sub(r"\s*\([^)]*\)\s*$", "", value), known)


def _contradictions(steps: list[dict], used: set[str], known: dict) -> list[str]:
    """Steps that name an ingredient this recipe does not contain.

    "Boil rice in salted water" followed by "cooking the pasta separately
    ensures it absorbs the sauce" is one dish described as two, and no amount
    of prompting reliably stops a small model doing it. It is never silently
    repaired — a rewritten method is a method nobody wrote — but it is said out
    loud, where the cook can see it before the pan is hot.
    """
    names = {}
    for ing in known.values():
        nm = str(ing.get("name", "")).strip()
        if len(nm) >= 4 and " " not in nm and nm.lower() not in NOT_A_FOOD:
            names[nm.lower()] = nm
    # "Rice" is in a recipe that uses "Basmati rice"; "egg" is in one that uses
    # "Eggs". Every word of every name used counts, singular and plural.
    used_words: set[str] = set()
    for name in used:
        for w in re.findall(r"[a-z]{3,}", name.lower()):
            used_words |= {w, w[:-1] if w.endswith("s") else w + "s"}
    out: list[str] = []
    for i, st in enumerate(steps, 1):
        # "Heat olive oil" names the oil for the pan, which is left unlisted as
        # often as salt is — not the olives nobody bought.
        said = re.sub(r"\bolive oil\b", "oil", st.get("do", "") + " " + st.get("why", ""), flags=re.I)
        for word in set(re.findall(r"[a-zA-Z]{4,}", said)):
            low = word.lower()
            hit = names.get(low) or names.get(low[:-1] if low.endswith("s") else low)
            if hit and hit.lower() not in used and low not in used_words and hit.lower() not in used_words:
                line = f"step {i} mentions {low}"
                if line not in out:
                    out.append(line)
    return out[:4]


# Catalogue names that are also ordinary words in a method. "Garnish with…",
# "season to taste", "boil some water" name no missing ingredient, and a check
# that now sends a recipe back to be rewritten cannot afford to think they do.
# The second half came from the corpus: single-word catalogue names that turn
# up in a method without being in that recipe's ingredients most of the time.
NOT_A_FOOD = frozenset("""
batter bone broth dough dressing filling garnish glaze green herb juice liquid
marinade meat fish pepper roast round salt sauce season serve spice stock topping
vegetable water oil ice mixture crumb pulp zest skin seed leftover fillet piece
chunk strip slice cube wedge half sheet plate bowl heat cook drain rinse cover
brown golden chop mince dash pinch sprig handful sugar flour fat cream
ingredient sprinkle roll each cake crust firm seasoning fruit muffin salad toothpick
recipe fine pastry jelly frosting gravy icing toast whole meal meatball meringue
pancake mashed cupcake custard plain cornbread savory savoury
""".split())

# A cue or a heat of "n/a" is a form the model filled in, not a thing to watch
# for — the app printed "until n/a" under every step.
EMPTY_WORDS = {"n/a", "na", "none", "nothing", "null", "-", "—", "no", "not applicable", "off"}

# Words that put a step on the hob or in the oven. A heat is only kept on a
# step that says one of them.
HOB_WORDS = re.compile(
    r"\b(fr(y|ies|ied|ying)|boil|simmer|saut|sear|heat|bak|roast|grill|toast|brown|melt|"
    r"steam|poach|cook|pan|wok|oven|skillet|reduc|warm|scrambl|blanch|char|carameli|"
    r"crisp|wilt|stir|°|broil|hob|flame)", re.I)


def _prep(v) -> str | None:
    """"diced", "beaten", "cut into strips" — not "Prepare the hummus mixture".

    The model put whole method sentences in prep, about other ingredients:
    flour was "Prepare the bread crumbs", eggs were "Prepare the hummus
    mixture". Printed under the ingredient, that is misinformation."""
    text = _text(v, 60)
    if not text:
        return None
    words = text.lower().strip(" .").split()
    if (len(words) > 5 or text.rstrip().endswith(".") or words[0] in {"prepare", "wash", "use", "mix"}
            or {"the", "a", "an", "for", "to"} & set(words) or " ".join(words) in EMPTY_WORDS):
        return None
    return text


def _total_minutes(stated: float | None, steps: list[dict]) -> int:
    """What the steps add up to, unless the stated total is close to it.

    A recipe said 360 minutes over steps adding up to 295. The clock the cook
    lives through is the steps; a stated total is allowed a quarter of an hour
    on top of them (resting, the oven coming up), and nothing else."""
    summed = sum(s["minutes"] for s in steps)
    if not summed:
        return int(max(1, min(600, stated))) if stated else 15
    if stated and summed <= stated <= summed + 15:
        return int(min(600, stated))
    return int(max(1, min(600, summed)))


# ------------------------------------------------------------------ time budget
#
# Almost everyone opening this app at the end of a day wants dinner in fifteen
# or twenty minutes, not a project. A 2B model left to itself wrote four hours
# of boiled courgettes. So every request gets a budget unless it asks for time.

DEFAULT_MINUTES = 20

_SLOW = re.compile(
    r"\b(slow|braise[ds]?|stew|roast|weekend|sunday|no rush|take (my|our|your) time|"
    r"all afternoon|project|long cook|oven[- ]baked|casserole|lasagne|lasagna|cake)\b", re.I)
_TIRED = re.compile(r"\b(drained|exhausted|tired|knackered|shattered|wiped out|destroyed|done in|beat tonight|dead on my feet|no energy)\b", re.I)


def time_budget(text: str, explicit: int | None = None) -> int | None:
    """Minutes the recipe must fit in, or None when they asked for something slow.

    "Give me 15 minutes" -> 15. "Half an hour" -> 30. "I'm drained" -> 15.
    "A slow Sunday roast" -> None. Anything else -> 20."""
    if explicit:
        return int(max(5, min(600, explicit)))
    t = str(text or "").lower()
    hours = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\b", t)
    mins = re.search(r"\b(\d{1,3})\s*(?:m|min|mins|minutes?)\b", t)
    if mins:
        return int(max(5, min(600, int(mins.group(1)) + (60 * float(hours.group(1)) if hours else 0))))
    if hours:
        return int(max(5, min(600, 60 * float(hours.group(1)))))
    if re.search(r"\bhalf an? hour\b", t):
        return 30
    if re.search(r"\b(an|one) hour and a half\b", t):
        return 90
    if re.search(r"\b(an|one) hour\b", t):
        return 60
    if _SLOW.search(t):
        return None
    if _TIRED.search(t) or re.search(r"\b(quick|fast|asap|hurry|in a rush)\b", t):
        return 15
    return DEFAULT_MINUTES


def recipe_problems(recipe: dict, budget: int | None, stock_ids: set[str] | None = None) -> list[str]:
    """What is wrong with a cleaned recipe, in words the model can act on.

    Empty means it can go to the cook. Anything here sends it back to be
    written again once, with this list as the reason — the model is told what
    it got wrong rather than asked to roll the dice a second time."""
    out: list[str] = []
    steps = recipe.get("steps") or []
    total = sum(s.get("minutes") or 0 for s in steps)
    if budget:
        if total > budget + max(3, budget // 5):
            out.append(f"the steps add up to {total} minutes; they only have {budget}")
        # The prompt asks for three or four. Six still cooks in time, and is not
        # worth another twenty seconds of waiting to shave off.
        if len(steps) > (6 if budget <= 20 else 8):
            out.append(f"{len(steps)} steps is too many for {budget} minutes; use at most four")
    ceiling = budget or 240
    for i, st in enumerate(steps, 1):
        for n, unit in re.findall(r"(\d{1,4})\s*(min|minute|minutes|hour|hours|hr|hrs)\b", st.get("do", ""), re.I):
            said = int(n) * (60 if unit.lower().startswith("h") else 1)
            if said > ceiling:
                out.append(f"step {i} says {n} {unit}, far too long")
    for line in recipe.get("contradictions") or []:
        out.append(f"{line}, which is not in the ingredients — list it or don't use it")
    if budget and budget <= 30 and stock_ids:
        missing = [n["id"] for n in recipe.get("needs") or []
                   if n["id"] not in stock_ids and not n.get("flexible")]
        if missing:
            out.append("it needs " + ", ".join(missing[:4]) + ", which they haven't got; "
                       "use only what is in the kitchen")
    return out[:6]


def _text(v, n: int) -> str | None:
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:n] if s else None


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def clean_recipe(raw: dict, known: dict[str, dict], origin: str = "assistant") -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) \
            or not isinstance(raw.get("steps"), list):
        return None

    serves_n = int(max(1, min(12, _num(raw.get("serves")) or 4)))

    # Everything the model offered, from either list, sorted by what the
    # CATALOGUE says each thing is rather than by which array it arrived in.
    offered: list[tuple[dict, bool]] = [
        (n, False) for n in raw.get("needs") or [] if isinstance(n, dict)
    ] + [
        (s, True) for s in raw.get("seasoning") or [] if isinstance(s, dict)
    ]

    adjusted: list[str] = []
    needs, seasoning, seen = [], [], set()
    moved_to_needs = 0
    for item, came_as_seasoning in offered:
        iid, q = _resolve_id(item.get("id"), known), _num(item.get("qty"))
        if iid not in known or iid in seen or not q or q <= 0:
            continue
        seen.add(iid)
        if _is_seasoning(iid, known):
            qty, capped = _season_qty(q, known[iid].get("unit", "g"), serves_n)
            entry = {"id": iid, "qty": qty, "essential": item.get("essential") is True}
            if capped:
                # The number was not believable, so the app will not print one.
                entry["toTaste"] = True
            seasoning.append(entry)
            continue
        if came_as_seasoning:
            moved_to_needs += 1
        qty, trimmed = _need_qty(q, known[iid], serves_n)
        entry = {"id": iid, "qty": qty}
        if _prep(item.get("prep")):
            entry["prep"] = _prep(item.get("prep"))
        if item.get("flexible") is True or trimmed:
            entry["flexible"] = True
        if trimmed:
            # Recorded, not hidden. The number the model wrote was not
            # believable, and the cook is told which ones were brought down.
            adjusted.append(f"{known[iid].get('name', iid)}: "
                            f"{round(q, 2)} -> {qty} {known[iid].get('unit', 'g')}")
        needs.append(entry)
    needs, seasoning = needs[:14], seasoning[:10]

    # An "extra" that is really an id belongs with the ingredients, not on a
    # list of things to buy. No quantity is invented for it: qty 0 with
    # flexible set is the app's way of saying "you need this, the recipe never
    # said how much", and the panel prints no number rather than a made-up one.
    extras = []
    for raw_extra in raw.get("extras") or []:
        text = _text(raw_extra, 80)
        if not text:
            continue
        iid = _as_known_id(text, known)
        if iid and iid not in seen:
            seen.add(iid)
            if _is_seasoning(iid, known):
                seasoning.append({"id": iid, "qty": 0, "essential": False, "toTaste": True})
            else:
                needs.append({"id": iid, "qty": 0, "flexible": True})
        elif not iid:
            extras.append(text)
    needs, seasoning, extras = needs[:16], seasoning[:12], extras[:12]

    if moved_to_needs:
        print(f"recipes: {moved_to_needs} thing(s) the model called seasoning are not, "
              "by the catalogue — moved to the ingredients.")

    if not needs and not extras:
        return None

    steps = []
    for st in raw["steps"]:
        if not isinstance(st, dict) or not _text(st.get("do"), 240):
            continue
        m = _num(st.get("minutes")) or 0
        step = {"do": _text(st["do"], 240), "minutes": int(max(0, min(240, round(m))))}
        for key, n in (("why", 200), ("heat", 24), ("cue", 90)):
            value = _text(st.get(key), n)
            if value and value.lower().strip(" .") not in EMPTY_WORDS:
                step[key] = value
        # "Wash the courgettes" on Low heat: a heat on a step that goes nowhere
        # near the hob is a filled-in form field, and the app draws it as a
        # flame. The words of the step decide, not the model's form-filling.
        if "heat" in step and not HOB_WORDS.search(step["do"] + " " + step["heat"]):
            del step["heat"]
        # What goes in the pan now. Only ids this recipe actually contains —
        # a step that claims to add something the recipe never bought is the
        # same invention as an invented id, and gets the same treatment.
        in_recipe = {x["id"] for x in needs} | {x["id"] for x in seasoning}
        uses = [u for u in (_resolve_id(x, known) for x in st.get("uses") or []) if u in in_recipe]
        if uses:
            step["uses"] = list(dict.fromkeys(uses))[:8]
        steps.append(step)
    steps = steps[:14]
    if len(steps) < 2:
        return None

    types = [t for t in raw.get("types") or [] if t in MEAL_TYPES]
    serves = _num(raw.get("serves"))
    complexity = raw.get("complexity") if raw.get("complexity") in (1, 2, 3) else 2

    out = {
        "id": f"{origin[:3]}_{secrets.token_hex(4)}",
        "name": _text(raw["name"], 80),
        "minutes": _total_minutes(_num(raw.get("minutes")), steps),
        "complexity": complexity,
        "types": types or ["dinner"],
        "cuisine": _text(raw.get("cuisine"), 30) or "Everyday",
        "serves": int(max(1, min(12, serves))) if serves else 4,
        "needs": needs,
        "seasoning": seasoning,
        "steps": steps,
        "origin": origin,
    }
    used_names = {str(known[x["id"]].get("name", "")).lower()
                  for x in needs + seasoning if x["id"] in known}
    used_names |= {e.lower() for e in extras}
    if adjusted:
        out["adjusted"] = adjusted[:6]
        print("recipes: quantities brought down to something edible — " + "; ".join(adjusted))
    wrong = _contradictions(steps, used_names, known)
    if wrong:
        out["contradictions"] = wrong
        print("recipes: the method contradicts the ingredients — " + "; ".join(wrong))
    if extras:
        out["extras"] = extras
    if _text(raw.get("description"), 200):
        out["description"] = _text(raw.get("description"), 200)
    return out
