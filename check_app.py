#!/usr/bin/env python3
"""Happy Bite — which fixes are actually in your files?

Four times now I have guessed at the state of your code and been wrong, and
each guess cost you a round trip. This reads the files instead. It changes
nothing, installs nothing and sends nothing anywhere.

    python3 check_app.py

Run it from your KitchenBuddy folder. Paste the output into the chat.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

G, R, Y, D, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = O = ""

FRONT = "frontend/src"

# (group, file, what to look for, what it means when present)
CHECKS = [
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/lib/voice.js", "export function markSpoken",
     "voice.js can be told the stream already said it"),
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/lib/voice.js", "isEcho(clean)",
     "speak() skips a reply the stream already read out"),
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/components/Chat.jsx", "speechqueue.js",
     "Chat.jsx feeds the sentence-by-sentence speech queue"),
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/components/Chat.jsx", "sq.feed(ev.text)",
     "each delta reaches the queue, so speaking starts at sentence one"),
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/screens/CookMode.jsx", "speechqueue.js",
     "cooking mode can stop the queue, not just the current sentence"),
    ("Voice — why it echoes and why it is slow",
     f"{FRONT}/screens/CookMode.jsx", "useApplyAction",
     "the chef can change the kitchen while you cook"),

    ("Wake word (installer 8)",
     f"{FRONT}/components/VoiceAgent.jsx", "handsFree",
     "the wake word no longer demands the local voice server"),
    ("Wake word (installer 8)",
     f"{FRONT}/sheets/Misc.jsx", "Listen for the wake word",
     "the Hands-free switch exists in Assistant and voice"),

    ("The recipe panel (installers 8, 10, 11)",
     f"{FRONT}/sheets/Create.jsx", "saveRecipe({ ...draft })",
     "a new recipe is kept the moment it appears"),
    ("The recipe panel (installers 8, 10, 11)",
     f"{FRONT}/styles/draft.css", ".draft-cols",
     "the landscape two-column draft"),
    ("The recipe panel (installers 8, 10, 11)",
     f"{FRONT}/sheets/RecipeSheet.jsx", 'name={rr.category}',
     "icons on the ingredient rows"),

    ("The recipe gate (installers 10, 11)",
     "backend/app/recipes.py", "_is_seasoning",
     "the catalogue decides what a seasoning is, both directions"),
    ("The recipe gate (installers 10, 11)",
     "backend/app/recipes.py", "_as_known_id",
     "an id written into extras is recognised"),
    ("The recipe gate (installers 10, 11)",
     "backend/app/recipes.py", "_contradictions",
     "a method that mentions a food the recipe lacks is flagged"),
    ("The recipe gate (installers 10, 11)",
     "backend/app/recipes.py", "_need_qty",
     "quantities nobody could eat are brought down and reported"),
    ("The recipe gate (installers 10, 11)",
     "backend/app/catalog.py", "ing.get('name', iid)",
     "the model is told what each id MEANS, not just its spelling"),

    ("The model (installer 9)",
     "backend/app/providers/llm.py", "_note_rate",
     "the provider measures how fast the model writes"),
    ("The model (installer 9)",
     "backend/app/providers/llm.py", "_budget_for",
     "a token budget the clock cannot deliver is refused"),
    ("The model (installer 9)",
     "backend/app/providers/llm.py", "CLOSE_ONLY_RX",
     "a reasoning block with no opening tag is stripped"),
]


def find_root(start: Path) -> Path | None:
    for c in (start, start / "happy-bite", start.parent, start.parent / "happy-bite",
              start / "KitchenBuddy" / "happy-bite"):
        try:
            if (c / "backend" / "app").is_dir() and (c / FRONT).is_dir():
                return c.resolve()
        except OSError:
            continue
    return None


def main() -> int:
    root = find_root(Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser())
    if not root:
        print(f"{R}Can't find the app.{O} Run this from the folder holding backend/ and frontend/.")
        return 2
    print(f"{root}\n")

    missing, group = [], None
    for grp, rel, needle, meaning in CHECKS:
        if grp != group:
            group = grp
            print(f"{grp}")
        path = root / rel
        if not path.is_file():
            print(f"  {R}?{O} {rel} — file not found")
            missing.append(meaning)
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  {R}?{O} {rel} — {e}")
            continue
        if needle in body:
            print(f"  {G}yes{O}  {meaning}")
        else:
            print(f"  {R}NO {O}  {meaning}   {D}({rel}){O}")
            missing.append(meaning)
    print()

    # Settings that decide speed
    print("Settings")
    env = root / "backend" / ".env"
    if env.is_file():
        vals = dict(re.findall(r"^([A-Z_]+)=(.*)$", env.read_text(encoding="utf-8"), re.M))
        for key in ("LLM_MODEL", "OPENAI_BASE_URL", "LLM_JSON_TOKENS", "LLM_TIMEOUT",
                    "LLM_THINK", "SPEECH_PROVIDER", "IMAGE_PROVIDER"):
            print(f"  {key:<18} {vals.get(key, D + '(not set)' + O)}")
    else:
        print(f"  {R}backend/.env not found{O}")

    # The catalogue: is anything still filed as a seasoning that shouldn't be?
    print("\nCatalogue")
    cat = root / "shared" / "catalog.json"
    if cat.is_file():
        try:
            data = json.loads(cat.read_text(encoding="utf-8"))
            ing = data.get("ingredients") or {}
            seas = sorted(v.get("name", k) for k, v in ing.items()
                          if isinstance(v, dict) and v.get("category") == "seasoning")
            print(f"  {len(ing)} ingredients, {len(seas)} filed as seasoning:")
            print("    " + ", ".join(seas))
        except ValueError as e:
            print(f"  {R}catalog.json isn't valid JSON: {e}{O}")
    else:
        print(f"  {D}shared/catalog.json not found{O}")

    corpus = root / "shared" / "recipes.json"
    if corpus.is_file():
        try:
            rs = json.loads(corpus.read_text(encoding="utf-8")).get("recipes") or []
            withuses = sum(1 for r in rs for s in r.get("steps", []) if s.get("uses"))
            print(f"  {len(rs)} reference recipes, {withuses} steps carrying 'uses'")
        except ValueError:
            print(f"  {R}shared/recipes.json isn't valid JSON{O}")

    print()
    if missing:
        print(f"{Y}{len(missing)} fix(es) are not in your files.{O} Every NO above is a symptom")
        print("with a known cause — paste this whole output into the chat.")
        return 1
    print(f"{G}Every fix is in place.{O} Paste this into the chat anyway: the settings and")
    print("the catalogue lines say things the file checks cannot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())