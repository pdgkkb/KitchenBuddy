"""Turn a recipe dataset into source-schema training examples.

Default: `Kaiser1308/CookingRecipes` from Hugging Face. Its `train` split is
streamed so the complete dataset can be processed without loading it into
memory.

The training target keeps the source dataset's recipe schema and every
ingredient string. It does not map ingredients to Happy Bite's catalogue.

    python build_dataset.py                 # loads the default dataset (streaming)
    python build_dataset.py --dataset owner/name
    python build_dataset.py --csv /path/to/recipes.csv
    python build_dataset.py --selftest      # offline: synthetic rows, no network

Writes data/train.jsonl and data/val.jsonl as chat examples:
    {"messages": [ {role:system}, {role:user}, {role:assistant} ]}
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import random
import re
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent

# Column names we look for, case-insensitively (see pick()).
TITLE = ("title", "name", "recipe_name")
INGR = ("ingredients", "cleaned_ingredients")            # quantity-bearing lines
STEPS = ("instructions", "directions", "steps", "method")

# Split a run-on instructions paragraph into steps at sentence ends, but
# not inside "1.5" or "Tbsp." — only where a space + capital/number follows.
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def as_list(value) -> list[str]:
    """Accept a real list, a JSON/Python-repr list string, or a paragraph."""
    if value is None or isinstance(value, float):
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    if not s:
        return []
    if s[0] in "[(":
        for parse in (json.loads, ast.literal_eval):
            try:
                out = parse(s)
                if isinstance(out, (list, tuple)):
                    return [str(v).strip() for v in out if str(v).strip()]
            except (ValueError, SyntaxError):
                pass
    return [ln.strip(" -*\t") for ln in re.split(r"[\n;]+", s) if ln.strip(" -*\t")]


def split_steps(lines: list[str]) -> list[str]:
    """One paragraph -> sentences. A real list of steps is left alone."""
    if len(lines) == 1:
        parts = [p.strip() for p in SENTENCE.split(lines[0]) if p.strip()]
        if len(parts) >= 2:
            return parts
    return lines


def pick(row: dict, names: tuple[str, ...]):
    lower = {str(k).lower(): k for k in row.keys()}
    for n in names:
        k = lower.get(n)
        if k is not None and row[k] not in (None, "", []):
            return row[k]
    return None


def to_recipe(row: dict) -> dict | None:
    title = pick(row, TITLE)
    lines = as_list(pick(row, INGR))
    steps_raw = split_steps(as_list(pick(row, STEPS)))
    if not title or not lines or not steps_raw:
        return None

    return {"Title": str(title), "Ingredients": lines, "Instructions": steps_raw}


def example(recipe: dict) -> dict:
    return {"messages": [
        {"role": "system", "content":
         "Return recipes using exactly this source schema: Title (string), "
         "Ingredients (array of complete ingredient strings), and Instructions "
         "(array of instruction strings). Preserve ingredients and quantities."},
        {"role": "user", "content": f"Give me the recipe for {recipe['Title']}."},
        {"role": "assistant", "content": json.dumps(recipe, ensure_ascii=False)},
    ]}


# ----------------------------------------------------------------- readers

def read_csv(path: str) -> Iterable[dict]:
    import pandas as pd
    for chunk in pd.read_csv(path, chunksize=2000):
        for _, r in chunk.iterrows():
            yield {k: r[k] for k in r.index}


def read_hf(name: str, split: str) -> Iterable[dict]:
    from datasets import load_dataset
    # streaming works for parquet-backed datasets and avoids a full download.
    ds = load_dataset(name, split=split, streaming=True, token=os.getenv("HF_TOKEN") or None)
    yield from ds


def read_selftest() -> Iterable[dict]:
    # Shaped like Hieu-Pham/kaggle_food_recipes: stringified ingredient list,
    # single-paragraph instructions.
    yield {"Title": "Courgette and Tomato Pasta",
           "Ingredients": "['400 g courgette, sliced', '2 tomatoes', '200 g pasta', "
                          "'2 tbsp olive oil', '1 tsp salt', '2 bay leaves']",
           "Instructions": "Boil the pasta until al dente. Fry the courgette in olive oil "
                           "until golden. Add the tomatoes and simmer for a few minutes. "
                           "Toss everything with the pasta and season to taste."}
    yield {"Title": "Goat Cheese Omelette",
           "Ingredients": "['3 eggs', '60 g goat cheese', 'pinch of black pepper', '1 tbsp butter']",
           "Instructions": "Beat the eggs well. Melt the butter in a nonstick pan over medium heat. "
                           "Pour in the eggs and let them set. Add the goat cheese and fold the omelette."}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.getenv("DATASET", "Kaiser1308/CookingRecipes"))
    default_csv = os.getenv("RECIPE_CSV") or (str(HERE / "full_dataset.csv")
                                               if (HERE / "full_dataset.csv").exists() else None)
    ap.add_argument("--csv", default=default_csv,
                    help="Read a local CSV instead of downloading")
    ap.add_argument("--split", default="train")
    ap.add_argument("--max", type=int, default=int(os.getenv("MAX_RECIPES", "0")),
                    help="Maximum kept recipes; 0 processes the entire source")
    ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--selftest", action="store_true", help="Offline: synthetic rows, no network")
    ap.add_argument("--peek", action="store_true",
                    help="Print the first 3 validated examples without writing files")
    ap.add_argument("--out", default=str(HERE / "data"))
    args = ap.parse_args()

    if args.selftest:
        source, label = read_selftest(), "self-test"
    elif args.csv:
        source, label = read_csv(args.csv), f"CSV {args.csv}"
    else:
        source, label = read_hf(args.dataset, args.split), args.dataset
    print(f"Reading {label}…")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, kept = [], 0
    val_count, train_count = 0, 0
    split_random = random.Random(args.seed)
    train_file = val_file = None
    if not args.peek:
        val_file = open(out / "val.jsonl", "w", encoding="utf-8")
        train_file = open(out / "train.jsonl", "w", encoding="utf-8")
    for row in source:
        raw = to_recipe(row)
        if not raw:
            continue
        item = example(raw)
        if args.peek:
            rows.append(item)
        elif split_random.random() < args.val:
            val_file.write(json.dumps(item, ensure_ascii=False) + "\n")
            val_count += 1
        else:
            train_file.write(json.dumps(item, ensure_ascii=False) + "\n")
            train_count += 1
        kept += 1
        if kept and kept % 500 == 0:
            print(f"  … {kept} kept")
        if (args.peek and kept >= 3) or (not args.peek and args.max > 0 and kept >= args.max):
            break

    if train_file is not None:
        train_file.close()
        val_file.close()
    print(f"Kept {kept} source recipes with the required fields.")
    if args.peek:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        return

    if kept < 20 and not args.selftest:
          print("Very few kept — check that the source has Title, Ingredients, "
              "and Instructions-like columns.")

    print(f"  {out/'val.jsonl'}: {val_count} examples")
    print(f"  {out/'train.jsonl'}: {train_count} examples")


if __name__ == "__main__":
    main()