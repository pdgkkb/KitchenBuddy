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
        "minutes": {"type": "integer", "description": "Total time"},
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
                    "prep": {"type": "string"},
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
            "description": "Ingredients with no id in the list, written as a shopper would: '2 bay leaves'",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "do": {"type": "string", "description": "One action, short imperative"},
                    "why": {"type": "string", "description": "Only where it changes the outcome"},
                    "heat": {"type": "string", "description": "Low | Medium-low | Medium | Medium-high | High | Oven 200 °C"},
                    "cue": {"type": "string", "description": "What to look for: 'oil shimmers'"},
                    "minutes": {"type": "number"},
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
- One action per step. If it needs "and then", split it. Four to eight steps.
- A step may only name ingredients that are in THIS recipe. Never mention a
  food the recipe does not contain: a rice dish does not talk about pasta.
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
        if len(nm) >= 4 and " " not in nm:
            names[nm.lower()] = nm
    out: list[str] = []
    for i, st in enumerate(steps, 1):
        for word in set(re.findall(r"[a-zA-Z]{4,}", (st.get("do", "") + " " + st.get("why", "")))):
            low = word.lower()
            hit = names.get(low) or names.get(low[:-1] if low.endswith("s") else low)
            if hit and hit.lower() not in used:
                line = f"step {i} mentions {hit.lower()}"
                if line not in out:
                    out.append(line)
    return out[:4]


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

    needs, seasoning, seen = [], [], set()
    moved_to_needs = 0
    for item, came_as_seasoning in offered:
        iid, q = item.get("id"), _num(item.get("qty"))
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
        entry = {"id": iid, "qty": round(min(q, 20000), 2)}
        if _text(item.get("prep"), 60):
            entry["prep"] = _text(item.get("prep"), 60)
        if item.get("flexible") is True:
            entry["flexible"] = True
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
            if _text(st.get(key), n):
                step[key] = _text(st.get(key), n)
        # What goes in the pan now. Only ids this recipe actually contains —
        # a step that claims to add something the recipe never bought is the
        # same invention as an invented id, and gets the same treatment.
        in_recipe = {x["id"] for x in needs} | {x["id"] for x in seasoning}
        uses = [u for u in (st.get("uses") or []) if isinstance(u, str) and u in in_recipe]
        if uses:
            step["uses"] = list(dict.fromkeys(uses))[:8]
        steps.append(step)
    steps = steps[:14]
    if len(steps) < 2:
        return None

    minutes = _num(raw.get("minutes"))
    types = [t for t in raw.get("types") or [] if t in MEAL_TYPES]
    serves = _num(raw.get("serves"))
    complexity = raw.get("complexity") if raw.get("complexity") in (1, 2, 3) else 2

    out = {
        "id": f"{origin[:3]}_{secrets.token_hex(4)}",
        "name": _text(raw["name"], 80),
        "minutes": int(max(1, min(600, minutes))) if minutes else max(1, sum(s["minutes"] for s in steps)),
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
    wrong = _contradictions(steps, used_names, known)
    if wrong:
        out["contradictions"] = wrong
        print("recipes: the method contradicts the ingredients — " + "; ".join(wrong))
    if extras:
        out["extras"] = extras
    if _text(raw.get("description"), 200):
        out["description"] = _text(raw.get("description"), 200)
    return out
