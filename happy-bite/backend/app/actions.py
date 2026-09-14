"""KitchenBuddy's hands: the tools the chat model may call to change the kitchen.

The household's data lives in the browser, not here — so an "action" is never
executed on the server. The model asks for one (a tool call); this module
*validates and normalises* it against the catalogue; `api.chat` streams the
clean action to the browser as an `action` event; the browser applies it to
IndexedDB and the screen updates. Same principle as `clean_recipe`: MODEL
OUTPUT IS UNTRUSTED. An unknown ingredient id, a NaN quantity, forty timers —
all dropped here rather than trusted downstream.

The model's tool_result is synthetic ("ok, milk is lower") — a confirmation so
it can finish its sentence, not proof the browser did anything. The browser is
the real executor and, being optimistic, effectively always succeeds; if it
can't (private-mode storage), it says so in the toast, not here.
"""

from __future__ import annotations

from typing import Any

from .catalog import catalog
from .recipes import clean_recipe

# Volume/mass conversions to a canonical catalogue unit. Densities are ignored
# on purpose: for stock-keeping, "100 g of milk" and "100 ml of milk" are the
# same tenth of a litre, and a kitchen helper that argued the point would be
# useless. Cross-family conversions therefore assume ~1 g/ml.
_VOLUME = {"ml": 1.0, "cl": 10.0, "l": 1000.0}          # -> millilitres
_MASS = {"g": 1.0, "kg": 1000.0, "mg": 0.001}           # -> grams


def to_canonical(qty: float, from_unit: str | None, canonical: str) -> float | None:
    """A quantity in whatever unit the model used, expressed in the id's unit."""
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return None
    if q != q:                                          # NaN
        return None
    u = (from_unit or canonical or "g").lower().strip()
    if canonical == "u":                                # countable: units only
        return round(q, 2)
    if canonical in _VOLUME:
        ml = q * (_VOLUME.get(u) or _MASS.get(u) or _VOLUME[canonical])  # g≈ml
        return round(ml / _VOLUME[canonical], 3)
    # canonical is a mass (g)
    grams = q * (_MASS.get(u) or _VOLUME.get(u) or 1.0)  # ml≈g
    return round(grams, 2)


# ------------------------------------------------------------------ tool schema
# Anthropic tool format. `as_openai_tools()` reshapes the same list for the
# OpenAI/Ollama function-calling protocol, so the two providers stay in step.

KITCHEN_TOOLS: list[dict] = [
    {
        "name": "adjust_stock",
        "description": (
            "Change how much of an ingredient is in the kitchen. Use this the "
            "moment the user says they used, opened, finished or spilled "
            "something ('I used 100 g of milk', 'we're out of eggs'). Give the "
            "amount in the id's own unit; the app subtracts it and updates the "
            "screen. Never call this speculatively — only when the user states a "
            "real change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Ingredient id from the list you were given."},
                "change": {"type": "number", "description": "Signed amount in the id's unit. Negative to use up, positive to add back. E.g. milk is in litres, so 100 ml used is -0.1."},
                "set": {"type": "number", "description": "Absolute amount to set instead of a change (e.g. 'there's about 200 g left')."},
                "unit": {"type": "string", "description": "The unit you're speaking in, if not the id's own (g, kg, ml, cl, l, u)."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "add_stock",
        "description": (
            "Put an ingredient into the kitchen with a quantity — for something "
            "just bought or found. If the ingredient isn't in your id list, give "
            "a name and category too and it will be created as a custom item you "
            "can use from then on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Ingredient id if it exists; otherwise a short new id like 'u_maple_syrup'."},
                "qty": {"type": "number", "description": "Amount, in the id's unit (or `unit`)."},
                "unit": {"type": "string", "description": "Unit you're speaking in, if not the id's own."},
                "name": {"type": "string", "description": "Display name — required only when creating a new custom item."},
                "category": {"type": "string", "description": "One of the catalogue categories — only for a new custom item."},
                "daysLeft": {"type": "integer", "description": "Optional: days until it goes off, if the user said."},
            },
            "required": ["id", "qty"],
        },
    },
    {
        "name": "set_expiry",
        "description": "Record when something goes off, when the user reads a use-by date aloud ('the yoghurt is good until Friday', 'these expire in 2 days').",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "days": {"type": "integer", "description": "Whole days from today until it's no longer good."},
            },
            "required": ["id", "days"],
        },
    },
    {
        "name": "add_custom_ingredient",
        "description": "Teach the kitchen a new ingredient that isn't in the id list, without setting a quantity yet. After this you may reference its id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "A short new id, lowercase with underscores, e.g. 'u_maple_syrup'."},
                "name": {"type": "string"},
                "category": {"type": "string", "description": "One of the catalogue categories."},
                "unit": {"type": "string", "enum": ["g", "ml", "cl", "l", "u"]},
                "shelfLife": {"type": "integer", "description": "Rough days it keeps."},
            },
            "required": ["name", "category"],
        },
    },
    {
        "name": "save_recipe",
        "description": (
            "Write a recipe into the user's recipe book so they can cook it later. "
            "Use the full recipe schema. Prefer ingredient ids from the list; "
            "anything with no id goes in `extras` as plain text. Call this when "
            "the user asks you to save, keep, or note down a dish you've worked out "
            "together."
        ),
        "input_schema": None,   # filled from RECIPE_SCHEMA below to avoid duplication
    },
    {
        "name": "add_to_shopping",
        "description": "Add one or more things to the shopping list ('put eggs on the list', 'we need more rice').",
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "Ingredient ids to buy."},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "set_timer",
        "description": "Start a kitchen timer ('set a timer for 12 minutes', 'remind me in half an hour').",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number"},
                "label": {"type": "string", "description": "What it's for, e.g. 'pasta'."},
            },
            "required": ["minutes"],
        },
    },
    {
        "name": "open_screen",
        "description": (
            "Take the user to a screen when they ask to see or go somewhere: "
            "'show me the kitchen', 'what's going off soon', 'open the shopping list', "
            "'take me to the recipes', 'what's for tonight', 'let's make a new recipe'. "
            "Do it, then say one short line about what you opened."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["today", "kitchen", "expiring", "recipes", "shopping", "receipt", "create_recipe", "chat", "close"],
                    "description": ("today = tonight's suggestion; kitchen = everything in stock; "
                                    "expiring = the kitchen, showing what goes off first; recipes = the recipe book; "
                                    "shopping = the shopping list; receipt = scan a receipt; "
                                    "create_recipe = the 'create a recipe' panel; chat = open this chef chat; "
                                    "close = put the chat away / go back to the app ('remove the chat', 'hide the chat')."),
                },
            },
            "required": ["view"],
        },
    },
]


def _fill_recipe_schema() -> None:
    from .recipes import RECIPE_SCHEMA
    for t in KITCHEN_TOOLS:
        if t["name"] == "save_recipe":
            t["input_schema"] = RECIPE_SCHEMA


_fill_recipe_schema()


def as_openai_tools() -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in KITCHEN_TOOLS]


# ------------------------------------------------------------------ validation

def _cats() -> set[str]:
    return {c["id"] for c in catalog()["categories"]}


def _slug(name: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:24]
    return "u_" + s if s else ""


def normalize(name: str, raw: dict, known: dict[str, dict]) -> tuple[dict | None, str]:
    """Turn one tool call into a clean action for the browser, plus a short
    acknowledgement for the model. Returns (None, reason) when it can't be
    honoured, so the model can explain or retry rather than pretend it worked."""
    raw = raw if isinstance(raw, dict) else {}

    if name == "adjust_stock":
        iid = raw.get("id")
        if iid not in known:
            return None, f"There is no ingredient called '{iid}'. Ask the user, or add it first."
        unit = known[iid].get("unit", "g")
        if raw.get("set") is not None:
            amount = to_canonical(raw["set"], raw.get("unit"), unit)
            if amount is None or amount < 0:
                return None, "That amount didn't make sense."
            return ({"kind": "set_stock", "id": iid, "qty": amount},
                    f"Set {known[iid]['name']} to {amount:g} {unit}.")
        delta = to_canonical(raw.get("change"), raw.get("unit"), unit)
        if delta is None or delta == 0:
            return None, "Say how much changed."
        return ({"kind": "adjust_stock", "id": iid, "delta": delta},
                f"{'Used' if delta < 0 else 'Added'} {abs(delta):g} {unit} of {known[iid]['name']}.")

    if name == "add_stock":
        iid = raw.get("id") or _slug(raw.get("name", ""))
        action: dict[str, Any] = {"kind": "add_stock", "id": iid}
        if iid not in known:                      # inline custom creation
            nm = str(raw.get("name") or "").strip()[:60]
            if not nm:
                return None, "That ingredient isn't known — give it a name and category to create it."
            cat = raw.get("category") if raw.get("category") in _cats() else "other"
            unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
            action["create"] = {"id": iid, "name": nm, "category": cat, "unit": unit, "shelfLife": 14}
            canon = unit
            label = nm
        else:
            canon = known[iid].get("unit", "g")
            label = known[iid]["name"]
        qty = to_canonical(raw.get("qty"), raw.get("unit"), canon)
        if qty is None or qty <= 0:
            return None, "How much of it?"
        action["qty"] = qty
        action["unit"] = canon
        if isinstance(raw.get("daysLeft"), int) and 0 <= raw["daysLeft"] <= 3650:
            action["daysLeft"] = raw["daysLeft"]
        return action, f"Added {qty:g} {canon} of {label} to the kitchen."

    if name == "set_expiry":
        iid = raw.get("id")
        if iid not in known:
            return None, f"There is no ingredient called '{iid}'."
        days = raw.get("days")
        if not isinstance(days, int) or not (-3650 <= days <= 3650):
            return None, "How many days until it goes off?"
        return ({"kind": "set_expiry", "id": iid, "days": days},
                f"Noted — {known[iid]['name']} good for {days} more day{'s' if days != 1 else ''}.")

    if name == "add_custom_ingredient":
        nm = str(raw.get("name") or "").strip()[:60]
        if not nm:
            return None, "Give the new ingredient a name."
        iid = raw.get("id") if isinstance(raw.get("id"), str) and raw["id"][:2] in ("u_", "c_") else _slug(nm)
        if not iid:
            return None, "Couldn't make an id for that."
        cat = raw.get("category") if raw.get("category") in _cats() else "other"
        unit = raw.get("unit") if raw.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
        life = raw["shelfLife"] if isinstance(raw.get("shelfLife"), int) and 0 < raw["shelfLife"] <= 3650 else 14
        return ({"kind": "add_custom", "id": iid, "name": nm, "category": cat, "unit": unit, "shelfLife": life},
                f"Added {nm} to the catalogue (id {iid}).")

    if name == "save_recipe":
        recipe = clean_recipe(raw, known, origin="assistant")
        if not recipe:
            return None, "That recipe didn't hold together — check the ingredient ids and that it has at least two steps."
        return ({"kind": "save_recipe", "recipe": recipe},
                f"Saved '{recipe['name']}' to the recipe book.")

    if name == "add_to_shopping":
        ids = [i for i in (raw.get("ids") or []) if i in known][:20]
        if not ids:
            return None, "Nothing recognisable to add."
        names = ", ".join(known[i]["name"] for i in ids)
        return ({"kind": "add_to_shopping", "ids": ids}, f"Added to the shopping list: {names}.")

    if name == "set_timer":
        try:
            mins = float(raw.get("minutes"))
        except (TypeError, ValueError):
            return None, "How long?"
        if not (0 < mins <= 600):
            return None, "That timer length isn't usable."
        label = str(raw.get("label") or "").strip()[:40] or None
        action = {"kind": "set_timer", "minutes": round(mins, 2)}
        if label:
            action["label"] = label
        return action, f"Timer set for {mins:g} minutes{f' — {label}' if label else ''}."

    if name == "open_screen":
        views = {"today", "kitchen", "expiring", "recipes", "shopping", "receipt", "create_recipe", "chat", "close"}
        view = raw.get("view")
        if view not in views:
            return None, "I'm not sure which screen you mean."
        if view == "close":
            return ({"kind": "navigate", "view": "close"}, "Closing the chat.")
        label = {"today": "tonight's suggestion", "kitchen": "the kitchen", "expiring": "what's going off soon",
                 "recipes": "the recipes", "shopping": "the shopping list", "receipt": "the receipt scanner",
                 "create_recipe": "the recipe creator", "chat": "the chef"}[view]
        return ({"kind": "navigate", "view": view}, f"Opening {label}.")

    return None, f"Unknown tool '{name}'."
