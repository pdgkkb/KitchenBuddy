"""The ingredient catalogue, shared with the frontend.

One file, `shared/catalog.json`, read by both sides. The browser needs it
to work offline; the server needs it to tell a model which ingredient ids
exist and to throw away the ones it invents.
"""

import json
from functools import lru_cache

from .config import ROOT

# Roughly how many characters of English go into one token. Used only to turn
# a character budget into something comparable with a context window; it does
# not need to be right, only consistently wrong in the safe direction.
CHARS_PER_TOKEN = 3.5


@lru_cache
def catalog() -> dict:
    return json.loads((ROOT / "shared" / "catalog.json").read_text("utf-8"))


# The cuisines the "understand" step may name. They used to be read from the
# recipes in shared/recipes.json, which no longer exists.
CUISINES = ["British", "Chinese", "Everyday", "French", "Greek", "Indian", "Italian", "Japanese",
            "Korean", "Levantine", "Mexican", "Spanish", "Thai", "Vietnamese"]


def ingredients(extra: dict | None = None) -> dict[str, dict]:
    """Catalogue ingredients plus any the household created from receipts.

    Extras arrive from the browser, so they're treated like any other
    input: only a name and a known category survive."""
    known = dict(catalog()["ingredients"])
    cats = {c["id"] for c in catalog()["categories"]}
    for iid, ing in (extra or {}).items():
        if not isinstance(ing, dict) or iid in known:
            continue
        name = str(ing.get("name", "")).strip()[:60]
        cat = ing.get("category") if ing.get("category") in cats else "other"
        unit = ing.get("unit") if ing.get("unit") in {"g", "ml", "cl", "l", "u"} else "g"
        if name and len(iid) <= 40:
            known[iid] = {"name": name, "category": cat, "unit": unit}
    return known


def _entry(iid: str, ing: dict) -> str:
    return f"{iid} = {ing.get('name', iid)} ({ing.get('unit', 'g')})"


def id_list(known: dict[str, dict], keep: list[str] | set[str] | None = None,
            limit: int = 0) -> str:
    """`u_goat = Goat's cheese (g), egg = Egg (u), …`

    The NAME beside it, and that is not decoration. This used to hand the
    model bare ids and units — `goat (g)` — and a model that cannot see what
    an id MEANS has to guess from the spelling. It guessed meat, wrote "add
    the goat and cook four minutes a side", and the household's goat's CHEESE
    went into a hot pan. Every quiet wrong-ingredient error in this app came
    through that gap: the id was valid, so nothing downstream could object.

    WHAT `keep` AND `limit` ARE FOR
    -------------------------------
    The old comment here said "the list gets longer; on a 128K context that
    costs nothing worth having". That is true of a 128K context and false of
    every other kind, and the app is aimed at models running on a laptop.

    A full catalogue is a few hundred entries at ~27 characters each — call it
    eight thousand characters, well over two thousand tokens — sent before the
    model writes a single word. Load a model with a 4096-token window (LM
    Studio's default, and Ollama's is smaller still) and the catalogue alone
    fills most of it. Add the rules, the schema and the room reserved for the
    answer and the request is arithmetically impossible, which the server
    reports as:

        The number of tokens to keep from the initial prompt is greater
        than the context length

    So the list can now be budgeted. `keep` is the ids that must be in it
    whatever happens — what is actually in the kitchen — and `limit` is a
    character ceiling. What survives, in order:

        1. everything in `keep`                (they can cook with it today)
        2. every seasoning                     (the recipe needs a seasoning list)
        3. as much of the rest as fits         (things they might buy)

    Trimming is SAFE, not a compromise, because of a rule the recipe prompt
    already carries: anything with no id goes into "extras" as plain text. An
    ingredient that falls off this list doesn't become unusable — it becomes
    "2 bay leaves" instead of "u_bay_leaf", which is what a shopping list
    wants anyway.
    """
    if not limit or limit <= 0:
        return ", ".join(_entry(i, ing) for i, ing in known.items())

    wanted = {k for k in (keep or []) if k in known}
    first = [i for i in known if i in wanted]
    seasoning = [i for i in known if i not in wanted
                 and known[i].get("category") == "seasoning"]
    rest = [i for i in known if i not in wanted
            and known[i].get("category") != "seasoning"]

    out: list[str] = []
    used = 0
    for iid in (*first, *seasoning, *rest):
        entry = _entry(iid, known[iid])
        cost = len(entry) + 2
        # The kitchen's own ingredients are never dropped. If they alone blow
        # the budget the caller has a bigger problem than this list, and
        # silently omitting what is on their shelves would be the worst
        # possible way to tell them about it.
        if used + cost > limit and iid not in wanted:
            break
        out.append(entry)
        used += cost
    return ", ".join(out)


def ids_budget(context_tokens: int, share: float = 0.35) -> int:
    """How many characters of ingredient list a context window can afford.

    Zero — meaning "no limit" — when the window isn't known, because guessing
    small would quietly shrink the catalogue for people running a model that
    has plenty of room.
    """
    if not context_tokens or context_tokens <= 0:
        return 0
    return max(600, int(context_tokens * share * CHARS_PER_TOKEN))