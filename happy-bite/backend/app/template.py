"""Cook the recipe that already exists, instead of writing a new one.

WHY
---
Writing a recipe from nothing is the most expensive thing this server asks of
a local model: it has to invent a dish, choose ingredients, get the quantities
right, order the work, and put heat and cues on every step — all of it as
free composition, all of it token by token, on a laptop. That is the minute or
two you wait on "Writing the method".

And it is usually unnecessary. The corpus in `shared/kaiser_recipes.jsonl` has
hundreds of thousands of real recipes, written by people who cooked them. If
one of them uses what is already in this kitchen, the model's job stops being
"invent a good dinner" and becomes "convert this one into our format" —
a mechanical, constrained task that a small model does far better and far
faster, because almost every decision has already been made for it.

So: retrieve several candidates, score each by HOW MUCH OF IT THE KITCHEN
ALREADY HAS, and if one clears the bar, hand that recipe to the model as the
thing to convert. The result is still adapted — scaled to the number of people
eating, mapped onto this household's ingredient ids, split into steps with
heat and cues — but nothing is invented, so there is far less to go wrong and
far less to wait for.

If nothing clears the bar, this returns None and the old free-composition path
runs exactly as before. A shortcut that has to work is not a shortcut.
"""

from __future__ import annotations

import re
import time

# How much of a candidate's ingredient list has to be in the kitchen before we
# call it "already yours". 0.72 is deliberately short of 1.0: every real recipe
# names water, salt, oil or pepper, and a household that is one clove of garlic
# short does not want a different dinner, it wants this one.
FLOOR = 0.72

# A recipe with two ingredients is a serving suggestion; one with twenty-five is
# a project. Neither is what "dinner from what's in the kitchen" means.
MIN_LINES, MAX_LINES = 3, 16

# How many corpus hits to score. Retrieval is a SQLite FTS query on an index
# already in memory — asking for eight instead of one costs microseconds, and
# the first hit by bm25 is very often not the one this kitchen can cook.
CANDIDATES = 8

# Converted recipes, kept by (title, serves). Ask twice for the same dinner and
# the second one is free. Small on purpose: this is a cache, not a database.
_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
_CACHE_TTL = 6 * 3600
_CACHE_MAX = 64


# --------------------------------------------------------------- reading lines

# "2 tablespoons olive oil, divided" -> "olive oil"
_QTY = re.compile(r"^[\s\-\*•]*[\d¼-¾\./\s]+")
_UNITS = re.compile(
    r"^(?:g|kg|mg|ml|cl|dl|l|lb|lbs|oz|ounces?|pounds?|grams?|kilos?|litres?|liters?|"
    r"cups?|tbsps?|tbs|tablespoons?|tsps?|teaspoons?|pinch(?:es)?|cloves?|cans?|tins?|"
    r"packages?|packets?|slices?|pieces?|sprigs?|bunch(?:es)?|large|small|medium|fresh|"
    r"frozen|dried|ground|chopped|minced|sliced|diced|of)\b\s*", re.I)
_TAIL = re.compile(r"\s*[,(].*$")


def _food(line: str) -> str:
    """What a shopper would call the thing on this line."""
    text = _TAIL.sub("", str(line or "").strip().lower())
    text = _QTY.sub("", text)
    for _ in range(4):                       # "2 large fresh chopped tomatoes"
        cut = _UNITS.sub("", text)
        if cut == text:
            break
        text = cut
    return re.sub(r"[^a-z' ]+", " ", text).strip()


def _lines(raw: str) -> list[str]:
    """The candidate's ingredient lines.

    The index stores them newline-separated (see rag.py). An index built by the
    older code joined them with spaces and cannot be split back apart, so that
    case degrades to one long line — which scores badly and simply means no
    template is chosen until the index is rebuilt. Wrong answers are worse than
    no answer.
    """
    parts = [p.strip() for p in str(raw or "").split("\n")]
    return [p for p in parts if p][:40]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']{3,}", text.lower())}


# --------------------------------------------------------------------- scoring

def pantry_words(stock_names: list[str]) -> set[str]:
    """Every word that names something on the shelf, plus its singular."""
    out: set[str] = set()
    for name in stock_names:
        for w in _words(str(name)):
            out.add(w)
            if w.endswith("es") and len(w) > 5:
                out.add(w[:-2])
            if w.endswith("ies") and len(w) > 5:
                out.add(w[:-3] + "y")
            if w.endswith("s") and len(w) > 4:
                out.add(w[:-1])
            # "tomato" has to reach "tomatoes" and "cherry" has to reach
            # "cherries", or a kitchen full of tomatoes reads as missing them.
            out.add(w + "s")
            out.add(w + "es")
            if w.endswith("y") and len(w) > 3:
                out.add(w[:-1] + "ies")
    return out


# Things nobody counts as missing. A recipe is not ruled out because the corpus
# wrote down "water".
ASSUMED = {
    "water", "salt", "pepper", "oil", "sugar", "flour", "butter", "ice",
    "sel", "poivre", "eau", "huile", "sucre", "farine", "beurre",
}


def score(candidate: dict, pantry: set[str]) -> dict:
    lines = _lines(candidate.get("ingredients"))
    if not (MIN_LINES <= len(lines) <= MAX_LINES):
        return {"ratio": 0.0, "have": [], "missing": [], "lines": lines}
    have, missing = [], []
    for line in lines:
        food = _food(line)
        if not food:
            continue
        ws = _words(food)
        if not ws:
            continue
        if ws & pantry or ws & ASSUMED:
            have.append(line)
        else:
            missing.append(food)
    counted = len(have) + len(missing)
    return {
        "ratio": (len(have) / counted) if counted else 0.0,
        "have": have, "missing": missing, "lines": lines,
    }


# "10 to 15 minutes", "1 hour", "2-3 hrs". The upper end of a range counts.
_TIMES = re.compile(r"(\d+)(?:\s*(?:-|to|or)\s*(\d+))?\s*(min|minutes?|mins|hours?|hrs?)\b", re.I)
_SLOW_WORDS = re.compile(r"\b(overnight|marinate|refrigerate|chill|freeze|rise|slow cooker|crock ?pot)\b", re.I)


def fits_time(instructions: str, budget: int | None) -> bool:
    """Could this method be cooked in `budget` minutes?

    The corpus says nothing about total time, but its methods do: a recipe
    that bakes for 45 minutes, or marinates overnight, is not dinner in
    fifteen however well it matches the fridge. Conservative on purpose —
    the times written down are a floor, since chopping never is."""
    if not budget:
        return True
    text = str(instructions or "")
    if _SLOW_WORDS.search(text):
        return False
    total = 0
    for low, high, unit in _TIMES.findall(text):
        n = int(high or low) * (60 if unit.lower().startswith("h") else 1)
        if n > budget:
            return False
        total += n
    return total <= budget


async def pick(rag, query: str, stock_names: list[str], floor: float = FLOOR,
               candidates: int = CANDIDATES, max_minutes: int | None = None) -> dict | None:
    """The corpus recipe this kitchen can already cook, or None.

    `rag` is the RecipeRetriever. A retriever that is off, still building or
    empty simply yields None, and the caller writes a recipe the old way.
    """
    if not rag or not getattr(rag, "ready", False) or not stock_names:
        return None
    hits = await rag.search(query, max(2, min(24, int(candidates or CANDIDATES))))
    if not hits:
        return None
    pantry = pantry_words(stock_names)
    best, best_score = None, None
    for hit in hits:
        if not fits_time(hit.get("instructions"), max_minutes):
            continue
        s = score(hit, pantry)
        if s["ratio"] < floor:
            continue
        # Among the ones that fit, prefer the one that uses MORE of the kitchen:
        # a six-ingredient dinner out of the fridge beats a three-ingredient one.
        rank = (s["ratio"], len(s["have"]))
        if best_score is None or rank > best_score:
            best, best_score = {**hit, **s}, rank
    return best


# ---------------------------------------------------------------------- prompts

def system(id_list: str, stock_lines: str, serves: int) -> str:
    return f"""You are converting a recipe that already exists into a kitchen
app's format. You are NOT inventing a dish and you are NOT improving one: the
recipe below was written by a cook who made it, and it stays their recipe.

Your job is mechanical, and doing it faithfully is the whole task:
- Keep the dish, its name (you may tidy the wording), its ingredients and the
  order of the work.
- Map each ingredient onto an id from the list, with the quantity converted
  into the unit shown beside that id. Anything with no id goes in "extras",
  written as a shopper would say it.
- Scale every quantity for {serves} people. The source recipe may be written
  for a different number; if it says how many it serves, scale from that,
  otherwise assume four.
- Split any step that says "and then" into separate steps, and add "heat" and
  "cue" where the original implies them. That is the only thing you may add.
- Do not add ingredients. Do not drop ingredients. Do not change the technique.

Valid ingredient ids, with their unit: {id_list}

Currently in the kitchen: {stock_lines or "nothing recorded"}

The household has most of this already — that is why this recipe was chosen.
Anything they are missing still goes in "needs"; the app flags it for them."""


def user(candidate: dict, brief: str, serves: int) -> str:
    ingredients = "\n".join(f"- {line}" for line in candidate.get("lines")
                            or _lines(candidate.get("ingredients")))
    method = str(candidate.get("instructions") or "").strip()[:4000]
    ask = str(brief or "").strip()
    return (f"Recipe to convert: {candidate.get('title')}\n\n"
            f"Ingredients as written:\n{ingredients}\n\n"
            f"Method as written:\n{method}\n\n"
            f"Convert it for {serves} people."
            + (f"\n\nWhat they asked for, so keep it in mind while converting "
               f"(without changing the dish): {ask}" if ask else ""))


# ------------------------------------------------------------------------ cache

def cached(title: str, serves: int) -> dict | None:
    key = (str(title or "").lower(), int(serves))
    got = _CACHE.get(key)
    if not got:
        return None
    at, recipe = got
    if time.time() - at > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return recipe


def remember(title: str, serves: int, recipe: dict) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[(str(title or "").lower(), int(serves))] = (time.time(), recipe)