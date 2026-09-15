"""Cooking a recipe with the equipment you actually own.

A recipe is written for a kitchen the writer imagined. Yours is the one you
have. This module is the gate between the two: it knows the small vocabulary of
appliances the app talks about, works out which ones a recipe leans on, and
validates the model's proposed adaptation the same way `recipes.clean_recipe`
validates a recipe — MODEL OUTPUT IS UNTRUSTED INPUT.

The one rule that matters here: **"no" is a valid answer.** A model asked to
adapt a roast for a microwave will cheerfully invent something, and the result
is a ruined dinner rather than a wasted minute. `possible: false` is therefore a
first-class outcome with its own field, and a "yes" that rewrites no steps at
all is rejected as the non-answer it is.
"""

from __future__ import annotations

import re

# The whole vocabulary. Deliberately short: these are the things that change how
# a step is cooked, not an inventory of the drawer. The browser shows the same
# ids from frontend/src/core/equipment.js — keep the two lists in step.
EQUIPMENT: dict[str, str] = {
    "hob": "hob or stovetop",
    "pan": "frying pan or skillet",
    "saucepan": "saucepan or pot",
    "oven": "oven",
    "grill": "grill or broiler",
    "airfryer": "air fryer",
    "microwave": "microwave",
    "pressure": "pressure cooker",
    "slowcooker": "slow cooker",
    "steamer": "steamer",
    "bbq": "barbecue",
    "wok": "wok",
    "blender": "blender or stick blender",
    "processor": "food processor",
    "ricecooker": "rice cooker",
    "toaster": "toaster",
}

# What a step's words say about the kit it wants. First match wins per pattern;
# a step can want more than one. Kept blunt on purpose — a wrong guess here only
# shows an offer to adapt, never changes a recipe.
WANTS: list[tuple[str, re.Pattern[str]]] = [
    ("oven", re.compile(r"\boven|\bbake[ds]?\b|\bbaking\b|\broast(?:ed|ing)?\b|\bgratin|"
                        r"\b(?:180|190|200|210|220|230|240)\s*°?\s*c\b", re.I)),
    ("grill", re.compile(r"\bgrill(?:ed|ing)?\b|\bbroil(?:ed|er|ing)?\b|\bunder the grill\b", re.I)),
    ("airfryer", re.compile(r"\bair[- ]?fry(?:er|ing)?\b", re.I)),
    ("microwave", re.compile(r"\bmicrowave[ds]?\b", re.I)),
    ("pressure", re.compile(r"\bpressure cook|\binstant pot\b", re.I)),
    ("slowcooker", re.compile(r"\bslow cook(?:er|ed|ing)?\b|\bcrock ?pot\b", re.I)),
    ("steamer", re.compile(r"\bsteam(?:ed|er|ing)\b", re.I)),
    ("bbq", re.compile(r"\bbarbecue|\bbbq\b|\bover coals\b", re.I)),
    ("wok", re.compile(r"\bwok\b|\bstir[- ]?fry", re.I)),
    ("blender", re.compile(r"\bblend(?:ed|er|ing)?\b|\bpur[ée]e[ds]?\b|\bliquidi[sz]e", re.I)),
    ("processor", re.compile(r"\bfood processor\b|\bpulse[ds]?\b(?!.*\bpulses\b)", re.I)),
    ("pan", re.compile(r"\bfrying pan\b|\bskillet\b|\bsear(?:ed|ing)?\b|\bfry\b|\bfrying\b|"
                       r"\bsaut[ée](?:ed|ing)?\b|\bpan[- ]fry", re.I)),
    ("saucepan", re.compile(r"\bsaucepan\b|\bboil(?:ed|ing)?\b|\bsimmer(?:ed|ing)?\b|"
                            r"\bpot\b|\bstock ?pot\b", re.I)),
    ("hob", re.compile(r"\bhob\b|\bstove ?top\b|\bmedium[- ]high heat\b|\bover (?:a )?low heat\b", re.I)),
]

# A pan or a saucepan implies a hob; saying both back to a user is noise.
IMPLIES: dict[str, str] = {"pan": "hob", "saucepan": "hob", "wok": "hob"}


def wanted_by(recipe: dict) -> list[str]:
    """Every appliance the recipe's own words ask for, most specific first.

    Reads `heat` as well as `do`, because "Oven 200 °C" lives in `heat` and is
    the single clearest signal a recipe gives about its equipment.
    """
    found: list[str] = []
    for step in (recipe.get("steps") or []):
        if not isinstance(step, dict):
            continue
        text = " ".join(str(step.get(k) or "") for k in ("do", "heat", "cue", "why"))
        for key, rx in WANTS:
            if key not in found and rx.search(text):
                found.append(key)
    for key in list(found):
        implied = IMPLIES.get(key)
        if implied and implied not in found:
            found.append(implied)
    return found


def missing_for(recipe: dict, owned: list[str] | None) -> list[str]:
    """What the recipe wants that this kitchen hasn't got.

    `owned` of None means the household has never said — that is NOT the same as
    owning nothing, so nothing is reported missing and no panel is offered.
    """
    if owned is None:
        return []
    have = {str(x) for x in owned}
    return [k for k in wanted_by(recipe) if k not in have]


ADAPT_SCHEMA = {
    "type": "object",
    "properties": {
        "possible": {
            "type": "string", "enum": ["yes", "partly", "no"],
            "description": "Can the dish be cooked well with only the equipment listed?",
        },
        "verdict": {
            "type": "string",
            "description": "One short sentence, said to the cook. For 'no', say plainly why.",
        },
        "using": {
            "type": "array", "items": {"type": "string"},
            "description": "Equipment ids the adaptation relies on. From the owned list only.",
        },
        "insteadOf": {
            "type": "array", "items": {"type": "string"},
            "description": "Equipment ids being replaced.",
        },
        "changes": {
            "type": "array",
            "description": "One entry per step that changes. Leave a step out if it is unchanged.",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer", "description": "1-based step number in the recipe"},
                    "do": {"type": "string", "description": "The step, rewritten as one action"},
                    "heat": {"type": "string", "description": "e.g. 'Air fryer 190 °C' or 'Medium-high'"},
                    "minutes": {"type": "number"},
                    "cue": {"type": "string", "description": "What to look for instead of a timer"},
                    "why": {"type": "string", "description": "Only where it changes the outcome"},
                },
                "required": ["step", "do"],
            },
        },
        "watch": {
            "type": "array", "items": {"type": "string"},
            "description": "Honest differences in the result — browning, texture, crispness.",
        },
    },
    "required": ["possible", "verdict"],
}


def _text(v, n: int) -> str | None:
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:n] if s else None


def clean_adaptation(raw: dict, recipe: dict, owned: list[str]) -> dict | None:
    """The same gate `clean_recipe` applies, for an adaptation.

    Drops what it doesn't recognise rather than repairing it: a step number the
    recipe hasn't got, equipment the kitchen hasn't got, a forty-hour timing.
    Returns None when nothing survives, so the caller can say so honestly
    instead of showing an empty panel.
    """
    if not isinstance(raw, dict):
        return None

    possible = raw.get("possible")
    if possible not in ("yes", "partly", "no"):
        return None
    verdict = _text(raw.get("verdict"), 260)
    if not verdict:
        return None

    have = {str(x) for x in owned if str(x) in EQUIPMENT}
    using = [k for k in dict.fromkeys(raw.get("using") or []) if k in have][:6]
    instead = [k for k in dict.fromkeys(raw.get("insteadOf") or []) if k in EQUIPMENT][:6]

    total = len(recipe.get("steps") or [])
    changes = []
    seen: set[int] = set()
    for c in (raw.get("changes") or []):
        if not isinstance(c, dict):
            continue
        try:
            n = int(c.get("step"))
        except (TypeError, ValueError):
            continue
        do = _text(c.get("do"), 240)
        if not do or not (1 <= n <= total) or n in seen:
            continue
        seen.add(n)
        item: dict = {"step": n, "do": do}
        for key, cap in (("heat", 28), ("cue", 90), ("why", 200)):
            if _text(c.get(key), cap):
                item[key] = _text(c.get(key), cap)
        try:
            minutes = float(c.get("minutes"))
            if minutes == minutes:                      # NaN guard
                item["minutes"] = int(max(0, min(240, round(minutes))))
        except (TypeError, ValueError):
            pass
        changes.append(item)
    changes.sort(key=lambda c: c["step"])
    changes = changes[:14]

    # A "yes" that rewrites nothing is not an answer, it is the model agreeing
    # with the question. The one case where it is legitimate is a recipe that
    # needed no oven in the first place — and that panel is never offered.
    if possible in ("yes", "partly") and not changes:
        return None

    watch = [t for t in (_text(w, 160) for w in (raw.get("watch") or [])) if t][:5]

    return {
        "possible": possible,
        "verdict": verdict,
        "using": using,
        "insteadOf": instead,
        "changes": changes,
        "watch": watch,
        "equipment": sorted(have),
    }
