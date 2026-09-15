#!/usr/bin/env python3
"""Happy Bite — teach the catalogue every ingredient the corpus knows.

The catalogue is the app's vocabulary. An ingredient that isn't in it cannot
be named by the assistant, can't be tracked in the kitchen, can't get an icon,
and makes `import_corpus.py` pass over every recipe that uses it. A few hundred
entries is a kitchen that only knows what it has already seen.

So this reads `shared/kaiser_recipes.jsonl`, pulls the ingredient out of every
line, tidies it into a name, and adds the ones that aren't there yet.

    python3 tools/build_catalogue.py --dry        look first, write nothing
    python3 tools/build_catalogue.py              add them
    python3 tools/build_catalogue.py --min 2      keep rarer ones too

TWO RULES IT WILL NOT BREAK:

  Nothing that already exists is touched. Not its id, not its name, not its
  category, not its unit. Your stock, your saved recipes and every correction
  you have made all point at those ids; renaming one silently empties a
  cupboard. New entries are added beside them and that is all.

  Every new entry is marked `"added": "corpus"` and carries how many recipes
  used it. That is not bookkeeping — the prompt cannot carry ten thousand
  ingredients (see --limit and docs/CATALOGUE.md), so the app needs to know
  which ones are worth sending.

Categories and units are guessed from the name by a keyword table, and the
guess is often crude. Anything it cannot place goes to "other" and is counted
at the end, so you can see how much it fudged rather than trusting it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

G, R, Y, D, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = O = ""

# Quantities, units, container words and prep notes — everything that is not
# the ingredient itself. "2 (14.5 ounce) cans diced tomatoes, drained" is the
# shape this has to survive.
NUM = r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?|\d*\s*[¼½¾⅓⅔⅛]"
MEASURES = (r"cups?|tablespoons?|teaspoons?|tbsps?|tsps?|ounces?|oz|pounds?|lbs?|"
            r"grams?|g|kilograms?|kg|millilit(?:er|re)s?|ml|lit(?:er|re)s?|l|"
            r"pinch(?:es)?|dash(?:es)?|cloves?|sprigs?|stalks?|heads?|bunch(?:es)?|"
            r"slices?|pieces?|packages?|packets?|cans?|jars?|bottles?|boxes?|"
            r"quarts?|pints?|gallons?|sticks?|cubes?|strips?|fillets?|large|medium|small|"
            r"inch(?:es)?|handfuls?|knobs?|squeezes?|splash(?:es)?|drops?")
PREP = (r"chopped|minced|diced|sliced|grated|shredded|crushed|peeled|seeded|cored|"
        r"drained|rinsed|beaten|melted|softened|divided|packed|thawed|frozen|fresh|"
        r"freshly|finely|coarsely|roughly|thinly|ground|to taste|optional|plus more|"
        r"for garnish|for serving|room temperature|cut into|halved|quartered|trimmed|"
        r"boneless|skinless|uncooked|cooked|canned|dried|toasted|warm|cold|hot|ripe|"
        r"or more|as needed|if desired|well|lightly|about|approximately|such as")

STOP_WORDS = {"of", "the", "a", "an", "and", "or", "in", "into", "with", "for",
              "to", "up", "your", "any", "some", "more", "very", "all", "purpose"}

# name fragment -> (category, unit). First match wins, so order matters:
# "coconut milk" must be tested before "coconut".
RULES: list[tuple[str, str, str]] = [
    # Compounds first: "cream cheese" is a cheese, not a cream.
    ("cream cheese", "dairy", "g"), ("sour cream", "dairy", "g"),
    ("ice cream", "dairy", "g"), ("coconut cream", "pantry", "ml"),
    ("peanut butter", "pantry", "g"), ("butter milk", "dairy", "ml"),
    ("buttermilk", "dairy", "ml"), ("milk chocolate", "pantry", "g"),
    ("egg", "dairy", "u"), ("milk", "dairy", "ml"), ("cream", "dairy", "cl"),
    ("butter", "dairy", "g"), ("cheese", "dairy", "g"), ("yoghurt", "dairy", "g"),
    ("yogurt", "dairy", "g"), ("parmesan", "dairy", "g"), ("mozzarella", "dairy", "g"),
    ("chicken", "meat", "g"), ("beef", "meat", "g"), ("pork", "meat", "g"),
    ("bacon", "meat", "g"), ("sausage", "meat", "g"), ("lamb", "meat", "g"),
    ("turkey", "meat", "g"), ("ham", "meat", "g"), ("veal", "meat", "g"),
    ("duck", "meat", "g"), ("mince", "meat", "g"), ("steak", "meat", "g"),
    ("salmon", "seafood", "g"), ("tuna", "seafood", "g"), ("cod", "seafood", "g"),
    ("shrimp", "seafood", "g"), ("prawn", "seafood", "g"), ("crab", "seafood", "g"),
    ("fish", "seafood", "g"), ("scallop", "seafood", "g"), ("mussel", "seafood", "g"),
    ("anchov", "seafood", "g"), ("squid", "seafood", "g"), ("lobster", "seafood", "g"),
    ("flour", "pantry", "g"), ("sugar", "pantry", "g"), ("rice", "pantry", "g"),
    ("pasta", "pantry", "g"), ("noodle", "pantry", "g"), ("bean", "pantry", "g"),
    ("lentil", "pantry", "g"), ("chickpea", "pantry", "g"), ("oat", "pantry", "g"),
    ("oil", "pantry", "cl"), ("vinegar", "pantry", "cl"), ("stock", "pantry", "ml"),
    ("broth", "pantry", "ml"), ("honey", "pantry", "g"), ("syrup", "pantry", "ml"),
    ("cornstarch", "pantry", "g"), ("cornflour", "pantry", "g"), ("yeast", "pantry", "g"),
    ("baking powder", "pantry", "g"), ("baking soda", "pantry", "g"),
    ("coconut milk", "pantry", "ml"), ("tahini", "pantry", "g"), ("nut", "pantry", "g"),
    ("seed", "pantry", "g"), ("raisin", "pantry", "g"), ("chocolate", "pantry", "g"),
    ("cocoa", "pantry", "g"), ("vanilla", "pantry", "ml"), ("gelatin", "pantry", "g"),
    ("bread", "bakery", "g"), ("tortilla", "bakery", "u"), ("bun", "bakery", "u"),
    ("crumbs", "bakery", "g"), ("pastry", "bakery", "g"), ("dough", "bakery", "g"),
    ("wine", "drinks", "cl"), ("beer", "drinks", "cl"), ("juice", "drinks", "ml"),
    ("water", "drinks", "ml"), ("coffee", "drinks", "g"), ("tea", "drinks", "g"),
    ("rum", "drinks", "cl"), ("brandy", "drinks", "cl"), ("whiskey", "drinks", "cl"),
    ("salt", "seasoning", "g"), ("pepper corn", "seasoning", "g"),
    ("black pepper", "seasoning", "g"), ("white pepper", "seasoning", "g"),
    ("cumin", "seasoning", "g"), ("paprika", "seasoning", "g"), ("cinnamon", "seasoning", "g"),
    ("nutmeg", "seasoning", "g"), ("oregano", "seasoning", "g"), ("basil", "seasoning", "g"),
    ("thyme", "seasoning", "g"), ("rosemary", "seasoning", "g"), ("sage", "seasoning", "g"),
    ("bay lea", "seasoning", "g"), ("curry powder", "seasoning", "g"),
    ("masala", "seasoning", "g"), ("turmeric", "seasoning", "g"), ("coriander seed", "seasoning", "g"),
    ("cardamom", "seasoning", "g"), ("clove", "seasoning", "g"), ("saffron", "seasoning", "g"),
    ("chili powder", "seasoning", "g"), ("chilli powder", "seasoning", "g"),
    ("cayenne", "seasoning", "g"), ("soy sauce", "seasoning", "ml"),
    ("fish sauce", "seasoning", "ml"), ("worcestershire", "seasoning", "ml"),
    ("mustard", "seasoning", "g"), ("ketchup", "seasoning", "g"), ("mayonnaise", "seasoning", "g"),
    ("hot sauce", "seasoning", "ml"), ("sesame", "seasoning", "g"),
    ("onion", "produce", "u"), ("garlic", "produce", "u"), ("tomato", "produce", "g"),
    ("potato", "produce", "g"), ("carrot", "produce", "g"), ("pepper", "produce", "u"),
    ("lemon", "produce", "u"), ("lime", "produce", "u"), ("apple", "produce", "u"),
    ("banana", "produce", "u"), ("orange", "produce", "u"), ("mushroom", "produce", "g"),
    ("spinach", "produce", "g"), ("lettuce", "produce", "g"), ("cabbage", "produce", "g"),
    ("broccoli", "produce", "g"), ("cauliflower", "produce", "g"), ("celery", "produce", "g"),
    ("courgette", "produce", "g"), ("zucchini", "produce", "g"), ("cucumber", "produce", "u"),
    ("aubergine", "produce", "g"), ("eggplant", "produce", "g"), ("pea", "produce", "g"),
    ("corn", "produce", "g"), ("berry", "produce", "g"), ("cherry", "produce", "g"),
    ("peach", "produce", "u"), ("pear", "produce", "u"), ("avocado", "produce", "u"),
    ("ginger", "produce", "g"), ("leek", "produce", "u"), ("shallot", "produce", "u"),
    ("parsley", "produce", "g"), ("cilantro", "produce", "g"), ("mint", "produce", "g"),
    ("chive", "produce", "g"), ("dill", "produce", "g"), ("scallion", "produce", "u"),
    ("squash", "produce", "g"), ("pumpkin", "produce", "g"), ("beet", "produce", "g"),
    ("turnip", "produce", "g"), ("radish", "produce", "g"), ("asparagus", "produce", "g"),
    ("olive", "produce", "g"), ("grape", "produce", "g"), ("mango", "produce", "u"),
    ("chili", "produce", "u"), ("chilli", "produce", "u"), ("jalapeno", "produce", "u"),
]

COUNTABLE = ("egg", "onion", "garlic", "lemon", "lime", "apple", "banana", "orange",
             "pepper", "cucumber", "avocado", "leek", "shallot", "scallion", "tortilla",
             "bun", "peach", "pear", "mango", "chili", "chilli", "jalapeno")


def clean_name(raw: str) -> str:
    s = str(raw or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)                       # (14.5 ounce)
    s = s.split(",")[0]                                    # ", drained"
    s = re.sub(rf"\b(?:{NUM})\b", " ", s)
    s = re.sub(rf"\b(?:{MEASURES})\b", " ", s)
    s = re.sub(rf"\b(?:{PREP})\b", " ", s)
    s = re.sub(r"[^a-z' ]+", " ", s)
    words = [w for w in s.split() if w and w not in STOP_WORDS and len(w) > 1]
    while words and words[-1] in {"or", "and", "of"}:
        words.pop()
    return " ".join(words[:4]).strip()


def singular(name: str) -> str:
    parts = name.split()
    if not parts:
        return name
    last = parts[-1]
    if last.endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.endswith("oes") and len(last) > 4:
        last = last[:-2]
    elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        last = last[:-1]
    parts[-1] = last
    return " ".join(parts)


def classify(name: str) -> tuple[str, str]:
    for fragment, category, unit in RULES:
        if fragment in name:
            if category != "seasoning" and any(c in name for c in COUNTABLE):
                return category, "u"
            return category, unit
    return "other", "g"


def slug(name: str) -> str:
    return "c_" + re.sub(r"[^a-z0-9]+", "_", name).strip("_")[:38]


# ------------------------------------------------------- reading the corpus

def _inner(row: dict) -> dict:
    """The recipe inside a line.

    kaiser_recipes.jsonl is not a flat list of recipes. Every line is a
    chat-training record, and the recipe is a JSON STRING inside the assistant
    message, with capitalised keys:

        {"messages": [..., {"role": "assistant",
                            "content": "{\\"Title\\": ..., \\"Ingredients\\": [...]}"}]}

    backend/app/rag.py has always known this. These tools did not, and read
    2.1 million lines while understanding none of them. Flat shapes are still
    accepted, so another corpus can be dropped in without editing anything.
    """
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                try:
                    inner = json.loads(m.get("content") or "{}")
                except ValueError:
                    return {}
                return inner if isinstance(inner, dict) else {}
    return row


def _listy(rec: dict, *names) -> list:
    for n in names:
        v = rec.get(n)
        if isinstance(v, list) and v:
            return [str(x) for x in v]
        if isinstance(v, str) and v.strip():
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except ValueError:
                pass
            return [p for p in re.split(r"[;\n]", v) if p.strip()]
    return []


def _texty(rec: dict, *names) -> str:
    for n in names:
        v = rec.get(n)
        if isinstance(v, list) and v:
            return " ".join(str(x) for x in v)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def recipe_parts(row: dict):
    """(title, ingredient lines, instructions) — whatever shape the line is."""
    rec = _inner(row)
    return (_texty(rec, "Title", "title", "name", "recipe_name"),
            _listy(rec, "Ingredients", "ingredients", "ingredient", "NER", "ingredients_raw"),
            _texty(rec, "Instructions", "instructions", "directions", "steps", "method"))


def shape_report(samples: list) -> str:
    """When nothing was understood, say what WAS there. A tool that reads two
    million lines, understands none of them and prints a cheerful zero is worse
    than one that crashes."""
    keys, inner_keys = set(), set()
    for row in samples:
        keys |= set(row.keys())
        got = _inner(row)
        if got is not row:
            inner_keys |= set(got.keys())
    out = "  top-level keys seen: " + (", ".join(sorted(keys)[:12]) or "none")
    if inner_keys:
        out += "\n  inside the assistant message: " + ", ".join(sorted(inner_keys)[:12])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="shared/kaiser_recipes.jsonl")
    ap.add_argument("--min", type=int, default=5,
                    help="keep an ingredient used in at least this many recipes")
    ap.add_argument("--top", type=int, default=3000,
                    help="keep at most this many, most-used first")
    ap.add_argument("--limit", type=int, default=0,
                    help="read only the first N lines — a quick look at 2 million")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    root = Path(".").resolve()
    for c in (root, root / "happy-bite", root.parent / "happy-bite"):
        if (c / "shared").is_dir() and (c / "backend").is_dir():
            root = c
            break
    src = root / a.source
    if not src.is_file():
        found = sorted(p.name for p in (root / "shared").glob("*.jsonl"))
        print(f"{R}No corpus at {src}{O}")
        if found:
            print(f"  shared/ holds: {', '.join(found)}")
            print(f"  Try:  python3 tools/build_catalogue.py --source shared/{found[0]}")
        return 2

    cat_path = root / "shared" / "catalog.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))
    known = cat["ingredients"]
    cats = {c["id"] for c in cat.get("categories") or []}
    existing_names = {str(v.get("name", "")).lower() for v in known.values()}
    existing_names |= {singular(n) for n in existing_names}

    print(f"{root}")
    print(f"{len(known)} ingredients in the catalogue, reading {src.name}\n")

    counts, lines, rows = Counter(), 0, 0
    samples = []
    with src.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            rows += 1
            if len(samples) < 5:
                samples.append(row)
            if a.limit and rows > a.limit:
                break
            if rows % 200000 == 0:
                print(f"  {D}{rows:,} lines read, {len(counts):,} names so far…{O}")
            for raw in recipe_parts(row)[1]:
                lines += 1
                name = singular(clean_name(raw))
                if len(name) >= 3 and not name.isdigit():
                    counts[name] += 1

    print(f"\n{rows:,} recipes, {lines:,} ingredient lines, {len(counts):,} distinct names\n")
    if rows and not lines:
        print(f"{R}Not one ingredient line was understood.{O} The corpus is not the")
        print("shape this expects. What it actually holds:\n")
        print(shape_report(samples))
        print("\nSend me those lines and I will fix the reader.")
        return 2

    added, skipped_known, by_cat = [], 0, Counter()
    for name, n in counts.most_common():
        if n < a.min or len(added) >= a.top:
            break
        if name in existing_names:
            skipped_known += 1
            continue
        iid = slug(name)
        if iid in known:
            skipped_known += 1
            continue
        category, unit = classify(name)
        if category not in cats:
            category = "other"
        entry = {"name": name[:1].upper() + name[1:], "category": category,
                 "unit": unit, "shelfLife": 14, "added": "corpus", "uses": n}
        known[iid] = entry
        added.append((iid, entry, n))
        by_cat[category] += 1

    print(f"{len(added):,} new, {skipped_known:,} already known "
          f"(used {a.min}+ times, at most {a.top:,} kept)\n")
    for category, n in by_cat.most_common():
        mark = f"{Y}" if category == "other" else f"{G}"
        print(f"  {mark}{n:>6}{O}  {category}")
    if by_cat.get("other"):
        share = 100 * by_cat["other"] / max(1, len(added))
        print(f"\n  {Y}{share:.0f}% could not be placed and went to 'other'.{O}")
        print("  Those still work — they just get the generic icon and a guessed unit.")

    print(f"\n{D}The twenty most used:{O}")
    for iid, entry, n in added[:20]:
        print(f"  {n:>5}  {entry['name'][:34]:<34} {entry['category']:<10} {entry['unit']}")

    if a.dry:
        print(f"\n{D}Nothing written (--dry).{O}")
        return 0
    if not added:
        print("\nNothing to add.")
        return 0

    shutil.copy2(cat_path, cat_path.with_suffix(".json.backup"))
    cat_path.write_text(json.dumps(cat, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{G}shared/catalog.json now holds {len(known)} ingredients.{O}")
    print(f"The original is at {cat_path.with_suffix('.json.backup').name}.")
    print("\nNothing that was already there was changed — ids, names, categories and")
    print("units are untouched, so your stock and saved recipes still point at them.")
    print(f"\n{Y}Now run upgrade_happy_bite_13.py{O}, or the assistant will try to send all")
    print(f"{len(known)} of these to the model in every single prompt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())