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
- Every hob or oven step gets a "heat" and, where it helps, a "cue" — what you
  see, hear or smell. Cues beat timers: "oil shimmers", "edges going golden",
  "a drop of water skitters".
- "why" only where skipping it ruins the dish. Plain words, one line.
- Quantities for the number of people asked for (default 4)."""


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

    needs = []
    for n in raw.get("needs") or []:
        if not isinstance(n, dict):
            continue
        q = _num(n.get("qty"))
        if n.get("id") in known and q and q > 0 and known[n["id"]].get("category") != "seasoning":
            item = {"id": n["id"], "qty": round(min(q, 20000), 2)}
            if _text(n.get("prep"), 60):
                item["prep"] = _text(n.get("prep"), 60)
            if n.get("flexible") is True:
                item["flexible"] = True
            needs.append(item)
    needs = needs[:14]

    seasoning = []
    for s in raw.get("seasoning") or []:
        q = _num(s.get("qty")) if isinstance(s, dict) else None
        if isinstance(s, dict) and s.get("id") in known and q and q > 0:
            seasoning.append({"id": s["id"], "qty": round(min(q, 500), 2),
                              "essential": s.get("essential") is True})
    # A seasoning the model filed under needs is moved, not lost.
    for n in raw.get("needs") or []:
        if isinstance(n, dict) and known.get(n.get("id"), {}).get("category") == "seasoning" \
                and not any(s["id"] == n["id"] for s in seasoning) and (_num(n.get("qty")) or 0) > 0:
            seasoning.append({"id": n["id"], "qty": round(_num(n["qty"]), 2), "essential": False})
    seasoning = seasoning[:10]

    extras = [t for t in (_text(e, 80) for e in raw.get("extras") or []) if t][:12]

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
    if extras:
        out["extras"] = extras
    if _text(raw.get("description"), 200):
        out["description"] = _text(raw.get("description"), 200)
    return out
