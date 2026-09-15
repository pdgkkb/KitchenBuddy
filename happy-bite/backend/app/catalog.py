"""The ingredient catalogue, shared with the frontend.

One file, `shared/catalog.json`, read by both sides. The browser needs it
to work offline; the server needs it to tell a model which ingredient ids
exist and to throw away the ones it invents."""

import json
from functools import lru_cache

from .config import ROOT


@lru_cache
def catalog() -> dict:
    return json.loads((ROOT / "shared" / "catalog.json").read_text("utf-8"))


@lru_cache
def recipe_book() -> dict:
    return json.loads((ROOT / "shared" / "recipes.json").read_text("utf-8"))


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


def id_list(known: dict[str, dict]) -> str:
    """`u_goat = Goat's cheese (g), egg = Egg (u), …`

    The NAME beside it, and that is not decoration. This used to hand the
    model bare ids and units — `goat (g)` — and a model that cannot see what
    an id MEANS has to guess from the spelling. It guessed meat, wrote "add
    the goat and cook four minutes a side", and the household's goat's CHEESE
    went into a hot pan. Every quiet wrong-ingredient error in this app came
    through that gap: the id was valid, so nothing downstream could object.

    The list gets longer. On a 128K context that costs nothing worth having.
    """
    return ", ".join(
        f"{iid} = {ing.get('name', iid)} ({ing.get('unit', 'g')})"
        for iid, ing in known.items())
