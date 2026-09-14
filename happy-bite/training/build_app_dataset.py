"""Turn the recipe corpus into APP-SCHEMA training examples.

`build_dataset.py` teaches Qwen the *source* schema (Title / Ingredients /
Instructions, free text). That's faithful to the data but it is NOT what the
app consumes: Happy Bite's engine (stock matching, shopping, scoring) speaks
ingredient *ids* from `shared/catalog.json`, structured `needs` / `seasoning`
/ `steps`, and puts anything with no id into `extras` as plain text.

This builder converts each recipe into that app schema, so the fine-tuned
model produces recipes the app can drop straight into `clean_recipe`. Nothing
is invented and nothing is thrown away: ingredients that map to a catalogue id
become `needs`/`seasoning`; everything else is preserved verbatim in `extras`.
That is exactly the "small catalogue + custom items" policy, learned by the
model instead of only enforced at runtime.

    # from training/, using the 2.2M-line file you already have:
    python build_app_dataset.py                        # reads data/kaiser_recipes.jsonl
    python build_app_dataset.py --jsonl data/mine.jsonl
    python build_app_dataset.py --dataset Kaiser1308/CookingRecipes   # via HF (streaming)
    python build_app_dataset.py --csv data/full_dataset.csv
    python build_app_dataset.py --max 50000            # cap kept recipes
    python build_app_dataset.py --peek                 # print 3 converted examples, no files

Writes data/app_train.jsonl and data/app_val.jsonl in the same chat shape
train_qlora.py already reads:  {"messages": [system, user, assistant]}.
Point train_qlora.py at these with --train/--val (or TRAIN_FILE/VAL_FILE).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parent / "shared" / "catalog.json"

# Reuse the source-schema parsing so both builders read datasets identically.
try:
    from build_dataset import as_list, pick, split_steps, read_csv, read_hf, TITLE, INGR, STEPS
except ImportError:  # running from another cwd
    import sys
    sys.path.insert(0, str(HERE))
    from build_dataset import as_list, pick, split_steps, read_csv, read_hf, TITLE, INGR, STEPS


# --------------------------------------------------------------- catalogue

def load_catalog() -> dict[str, dict]:
    data = json.loads(CATALOG.read_text("utf-8"))
    return data["ingredients"]


# A few obvious synonyms the stem match alone would miss. Extend freely — every
# entry here is one more ingredient the model learns to ground instead of
# spilling into extras.
SYNONYMS = {
    "aubergine": None, "eggplant": None,
    "scallion": "onion", "green onion": "onion", "spring onion": "onion",
    "olive oil": "oliveoil", "extra virgin": "oliveoil", "evoo": "oliveoil",
    "parmigiano": "parmesan", "parmesan cheese": "parmesan",
    "chickpeas": "chickpea", "garbanzo": "chickpea",
    "black pepper": "blackpepper", "pepper": None,        # ambiguous: skip bare "pepper"
    "coconut milk": "coconutmilk", "soy sauce": "soy", "fish sauce": "fishsauce",
    "chilli": "chilli", "chili": "chilli", "red pepper flakes": "chilli",
    "greek yoghurt": "yoghurt", "yogurt": "yoghurt", "yoghurt": "yoghurt",
    "goat cheese": "goat", "goat's cheese": "goat", "chevre": "goat",
    "cod fillet": "cod", "beef steak": "beef", "pork belly": "pork",
    "basmati": "rice", "spaghetti": "pasta", "penne": "pasta", "macaroni": "pasta",
    "lime": "limejuice", "gochugaru": "gochugaru", "garam masala": "garammasala",
}


# Generic words that appear across many ingredients and would mis-match if
# indexed on their own ("pepper" -> black or bell; "cheese" -> goat or parmesan).
STOP = {"pepper", "cheese", "sauce", "oil", "paste", "flakes", "fillet", "steak",
        "belly", "breast", "ground", "fresh", "dried", "semi", "skimmed", "whole",
        "plain", "smoked", "red", "green", "black", "white", "hot", "sweet"}


def build_matcher(cat: dict[str, dict]):
    """A cheap, deterministic free-text -> id matcher. Longest match wins.

    Each id is indexed by its own token and by every meaningful word of its
    display name (so 'Basmati rice' is reachable from 'rice'), minus a stoplist
    of words too generic to disambiguate on their own."""
    stems: list[tuple[str, str]] = []           # (stem, id)
    seen_stem: set[str] = set()
    for iid, ing in cat.items():
        words = re.sub(r"[^a-z ]", " ", ing["name"].lower()).split()
        for w in [iid, *words]:
            stem = w.rstrip("s")
            if len(stem) >= 4 and w not in STOP and stem not in seen_stem:
                seen_stem.add(stem)
                stems.append((stem, iid))
    stems.sort(key=lambda x: -len(x[0]))
    syn = {k.lower(): v for k, v in SYNONYMS.items()}

    def match(line: str) -> str | None:
        t = " " + re.sub(r"[^a-z ]", " ", line.lower()) + " "
        for phrase, iid in syn.items():
            if iid and (" " + phrase + " ") in t.replace("-", " "):
                return iid
        best = None
        for stem, iid in stems:
            if re.search(r"\b" + re.escape(stem), t):
                best = iid
                break                            # stems already longest-first
        return best

    return match


# --------------------------------------------------------------- quantities

_FRAC = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
# rough kitchen conversions to a canonical unit
_TO_G = {"g": 1, "gram": 1, "grams": 1, "kg": 1000, "oz": 28.35, "ounce": 28.35,
         "lb": 453.6, "pound": 453.6, "tbsp": 15, "tablespoon": 15, "tsp": 5, "teaspoon": 5,
         "cup": 120, "c": 120}
_TO_ML = {"ml": 1, "l": 1000, "litre": 1000, "liter": 1000, "cl": 10, "tbsp": 15, "tsp": 5,
          "cup": 240, "c": 240, "oz": 30, "fl": 30, "pint": 473, "pt": 473, "quart": 946}
DEFAULT = {"g": 100.0, "ml": 200.0, "cl": 20.0, "l": 0.5, "u": 1.0}


def lead_number(s: str) -> tuple[float | None, str]:
    """Pull a leading amount ('1 1/2', '2.5', '½') off an ingredient line."""
    s = s.strip()
    for ch, v in _FRAC.items():
        s = s.replace(ch, f" {v} ")
    m = re.match(r"\s*(\d+)\s+(\d+)\s*/\s*(\d+)", s)          # 1 1/2
    if m:
        a, b, c = map(float, m.groups())
        return a + b / c, s[m.end():]
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", s)                   # 1/2
    if m:
        a, b = map(float, m.groups())
        return a / b, s[m.end():]
    m = re.match(r"\s*(\d+(?:\.\d+)?)", s)                     # 2 or 2.5
    if m:
        return float(m.group(1)), s[m.end():]
    return None, s


def to_canonical_qty(line: str, unit: str) -> float:
    n, rest = lead_number(line)
    if n is None:
        return DEFAULT.get(unit, 100.0)
    tok = re.findall(r"[a-zA-Z]+", rest[:16].lower())
    u = tok[0] if tok else ""
    if unit == "u":
        return round(max(1, n))
    if unit in ("ml", "cl", "l"):
        ml = n * _TO_ML.get(u, 15 if u in ("tbsp", "tablespoon") else 200)
        return round(ml / {"ml": 1, "cl": 10, "l": 1000}[unit], 2)
    grams = n * _TO_G.get(u, 100)                              # canonical is g
    return round(grams, 1)


# --------------------------------------------------------------- conversion

def app_system(id_units: str) -> str:
    return (
        "You are Happy Bite's recipe writer. Return ONE JSON object in the app's "
        "recipe schema and nothing else.\n"
        "Fields: name (string), minutes (int), complexity (1|2|3), types (array of "
        "breakfast|lunch|dinner|snack), cuisine (string), serves (int), "
        "needs (array of {id, qty, prep?, flexible?}), seasoning (array of {id, qty, "
        "essential?}), extras (array of plain-text ingredient strings), steps (array "
        "of {do, minutes, heat?, cue?, why?}).\n"
        "Rules: use ONLY these ingredient ids, with quantities in the unit shown "
        "beside each. Anything with no id goes in extras as written — never invent an "
        "id. Seasonings go in seasoning. One action per step.\n"
        f"Valid ingredient ids: {id_units}"
    )


def to_app_recipe(raw: dict, cat: dict, match) -> dict | None:
    title = str(raw.get("Title") or "").strip()
    ings = as_list(raw.get("Ingredients"))
    steps_raw = split_steps(as_list(raw.get("Instructions")))
    if not title or not ings or len(steps_raw) < 2:
        return None

    needs, seasoning, extras, seen = [], [], [], set()
    for line in ings:
        iid = match(line)
        if iid and iid not in seen:
            seen.add(iid)
            unit = cat[iid]["unit"]
            qty = to_canonical_qty(line, unit)
            if cat[iid]["category"] == "seasoning":
                seasoning.append({"id": iid, "qty": qty, "essential": False})
            else:
                needs.append({"id": iid, "qty": qty})
        elif not iid:
            clean = re.sub(r"\s+", " ", line).strip()[:80]
            if clean:
                extras.append(clean)

    if not needs and not extras:
        return None
    steps = [{"do": re.sub(r"\s+", " ", s).strip()[:240], "minutes": 0} for s in steps_raw if s.strip()][:14]
    if len(steps) < 2:
        return None

    recipe = {
        "name": title[:80], "minutes": max(1, 8 * len(steps)), "complexity": 2,
        "types": ["dinner"], "cuisine": "Everyday", "serves": 4,
        "needs": needs[:14], "seasoning": seasoning[:10], "steps": steps,
    }
    if extras:
        recipe["extras"] = extras[:12]
    return recipe


def example(recipe: dict, title: str, system: str) -> dict:
    return {"messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Give me the recipe for {title}."},
        {"role": "assistant", "content": json.dumps(recipe, ensure_ascii=False)},
    ]}


# --------------------------------------------------------------- readers

def read_jsonl(path: str) -> Iterable[dict]:
    """Your kaiser_recipes.jsonl: each line is {'messages':[sys,user,assistant]}
    whose assistant content is a JSON {Title, Ingredients, Instructions}."""
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "messages" in obj:
                assistant = next((m for m in reversed(obj["messages"]) if m.get("role") == "assistant"), None)
                if not assistant:
                    continue
                try:
                    yield json.loads(assistant["content"])
                except (json.JSONDecodeError, TypeError):
                    continue
            elif isinstance(obj, dict):
                yield obj


def source_rows(args) -> tuple[Iterable[dict], str, bool]:
    """Returns (rows, label, already_source_schema). When rows already carry
    Title/Ingredients/Instructions keys we use them directly; a CSV/HF row is
    mapped through the source-schema picker first."""
    if args.jsonl:
        return read_jsonl(args.jsonl), f"JSONL {args.jsonl}", True
    if args.csv:
        return read_csv(args.csv), f"CSV {args.csv}", False
    return read_hf(args.dataset, args.split), args.dataset, False


def as_source(row: dict, direct: bool) -> dict | None:
    if direct:
        return row if row.get("Title") else None
    title = pick(row, TITLE)
    if not title:
        return None
    return {"Title": title, "Ingredients": pick(row, INGR), "Instructions": pick(row, STEPS)}


# --------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    default_jsonl = str(HERE / "data" / "kaiser_recipes.jsonl")
    ap.add_argument("--jsonl", default=default_jsonl if Path(default_jsonl).exists() else None,
                    help="Your recipes as {messages:[...]} or {Title,Ingredients,Instructions} per line")
    ap.add_argument("--dataset", default=os.getenv("DATASET", "Kaiser1308/CookingRecipes"))
    ap.add_argument("--csv", default=os.getenv("RECIPE_CSV"))
    ap.add_argument("--split", default="train")
    ap.add_argument("--max", type=int, default=int(os.getenv("MAX_RECIPES", "0")))
    ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--peek", action="store_true")
    ap.add_argument("--out", default=str(HERE / "data"))
    args = ap.parse_args()

    cat = load_catalog()
    match = build_matcher(cat)
    id_units = ", ".join(f"{iid} ({ing['unit']})" for iid, ing in cat.items())
    system = app_system(id_units)

    rows, label, direct = source_rows(args)
    print(f"Reading {label} … mapping to the app schema against {len(cat)} catalogue ids.")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(args.seed)
    tf = vf = None
    if not args.peek:
        tf = open(out / "app_train.jsonl", "w", encoding="utf-8")
        vf = open(out / "app_val.jsonl", "w", encoding="utf-8")

    seen_source = kept = matched_ings = total_ings = peeked = 0
    peek_rows = []
    for row in rows:
        src = as_source(row, direct)
        if not src:
            continue
        seen_source += 1
        recipe = to_app_recipe(src, cat, match)
        if not recipe:
            continue
        total_ings += len(recipe["needs"]) + len(recipe.get("seasoning", [])) + len(recipe.get("extras", []))
        matched_ings += len(recipe["needs"]) + len(recipe.get("seasoning", []))
        ex = example(recipe, src["Title"], system)
        if args.peek:
            peek_rows.append(ex)
            peeked += 1
            if peeked >= 3:
                break
        else:
            (vf if rnd.random() < args.val else tf).write(json.dumps(ex, ensure_ascii=False) + "\n")
        kept += 1
        if kept % 2000 == 0:
            print(f"  … {kept} kept ({seen_source} read)")
        if args.max and kept >= args.max:
            break

    if tf:
        tf.close(); vf.close()
    if args.peek:
        for r in peek_rows:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    rate = (matched_ings / total_ings * 100) if total_ings else 0
    print(f"Kept {kept} recipes from {seen_source} read.")
    print(f"Ingredient grounding: {rate:.0f}% mapped to a catalogue id, the rest kept in extras.")
    print(f"  {out/'app_train.jsonl'} and {out/'app_val.jsonl'} written.")
    print("Tip: a higher grounding rate = grow shared/catalog.json (or SYNONYMS here).")


if __name__ == "__main__":
    main()
