"""Recipe shape, and the gate every recipe passes before the app sees it.

MODEL OUTPUT IS UNTRUSTED INPUT. A model will invent an ingredient id,
send a negative quantity or forty steps. `clean_recipe` drops what it
doesn't recognise rather than repairing it — a silently "corrected"
recipe is worse than a rejected one. Same rules as the old
`validateRecipe` in the browser, now on the side that holds the key."""

import math
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
        "minutes": {"type": "integer", "description": "Total time start to finish: the step minutes added up"},
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
                    "prep": {"type": "string", "description": "How it is cut or readied, a few words: 'diced', 'beaten'. Omit if nothing."},
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
            # No example here. With one ("2 bay leaves") a 2B model put two bay
            # leaves in a spinach sauté, because the example was the only extra
            # it had ever been shown.
            "description": "Only ingredients this recipe uses that have no id in the list, with an amount, as a shopper would write them. Usually empty.",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "do": {"type": "string", "description": "One action, short imperative, naming what it happens in and what goes in: 'In a bowl, toss the chicken with the olive oil, garlic and oregano'"},
                    "why": {"type": "string", "description": "Only where it changes the outcome"},
                    "heat": {"type": "string", "description": "Only for a step on the hob or in the oven: Low | Medium-low | Medium | Medium-high | High | Oven 200 °C. Omit otherwise."},
                    "cue": {"type": "string", "description": "What to look for: 'oil shimmers'. Omit if nothing."},
                    "minutes": {"type": "number", "description": "How long this step really takes. Chopping an onion is 2, not 10."},
                    "uses": {"type": "array", "items": {"type": "string"},
                             "description": "Ingredient ids that go IN at this step, from this recipe's own needs and seasoning. Empty for a step that adds nothing."},
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
                    "stars": {"type": "number", "description": "Difficulty from 0.5 to 5 in half steps, judged on how many ingredients, how many go in at once, how many pans, bowls and boards to wash, and how long it takes. 0.5 an egg fried in one pan, 1 a plain omelette, 2.5 a stir-fry with rice, 4 a lasagne."},
                    "cuisine": {"type": "string"},
                },
                "required": ["name", "description", "minutes", "stars", "cuisine"],
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
- One action per step. If it needs "and then", split it. As few steps as the
  dish needs: three to eight.
- A step may only name ingredients that are in THIS recipe. Never mention a
  food the recipe does not contain: a rice dish does not talk about pasta.
  If a step uses it, it is in "needs", "seasoning" or "extras".
- Each "do" says what it happens in and names every ingredient that goes in at
  that step: "In a bowl, toss the chicken with the olive oil, garlic and
  oregano", never just "Marinate the chicken". It is read aloud to someone who
  can't look at the screen. The container must fit the action: whisking,
  beating, mixing and marinating happen in a BOWL; a frying pan or pot is only
  for what cooks. Never "in a frying pan, whisk the eggs".
- Work in the order a cook would: a marinade, sauce or mixture is made in the
  step BEFORE the one that uses it.
- The LAST step is how it is served or eaten: "Spoon onto warm plates and eat
  straight away". A method that stops at "remove from the heat" is unfinished.
- Amounts are in the unit beside each id. An id in g wants GRAMS even for
  things you count: a green onion is about 15 g, a clove of garlic 5 g — never
  1 g.
- "minutes" on a step is the time that step really takes, and any time written
  in "do" says the same number. The recipe's "minutes" is the steps added up.
- Each id is given as `id = Name (unit)`. Write about what the id MEANS, not
  what it is spelled like. `u_goat = Goat's cheese` is a cheese: it is
  crumbled over at the end, never seared four minutes a side.
- Oils, butter for frying and vinegars: a few spoonfuls, and "flexible": true.
  Never hundreds of millilitres.
- Portions a person really eats, per person: meat or fish 150 g, potatoes or
  other vegetables 150-200 g each, dry pasta or rice 80-100 g, eggs 2. For two
  people that is 300 g of chicken, not 800.
- "uses" lists the ids that go into the pan AT that step, so the cook knows
  what to reach for. Nothing that was added earlier.
- For anything seared, fried or grilled, give the time PER SIDE in "do":
  "four minutes on the first side, three on the second". A single total is
  useless to somebody standing over a pan.
- Every hob or oven step gets a "heat" and, where it helps, a "cue" — what you
  see, hear or smell. Cues beat timers: "oil shimmers", "edges going golden",
  "a drop of water skitters".
- "why" only where skipping it ruins the dish. Plain words, one line.
- Quantities for the number of people asked for (default 4)."""


# A seasoning is whatever the CATALOGUE says it is, and the catalogue decides
# in both directions. The old rule only moved one way — a seasoning filed under
# needs was moved across — so a whole vegetable the model called a seasoning
# stayed there. That is how garlic, onions and tomatoes ended up in the spice
# list. A model's opinion about what a seasoning is carries no weight here.
def _is_seasoning(iid: str, known: dict) -> bool:
    return known.get(iid, {}).get("category") == "seasoning"


# The most of a seasoning one person could plausibly eat. A 4B model guesses
# grams badly: it asked for 380 g of salt for three people, which is not a
# seasoning mistake, it is a medical one. Above this the number is dropped and
# the app says "to taste" — honest about not knowing, rather than confident and
# wrong. 8 g of salt a head is already generous; 8 g of paprika is a lot.
SEASON_PER_PERSON = {"g": 8.0, "ml": 10.0, "cl": 1.0, "l": 0.05, "u": 1.0}

# The same treatment for ordinary ingredients, which needed it just as badly:
# 1.7 kg of courgettes for three people, and 150 cl — a litre and a half — of
# olive oil. Generous rather than mean, because a cap that fires on a correct
# recipe is worse than no cap: 400 g a head of any one thing is a large plate.
NEED_PER_PERSON = {"g": 400.0, "ml": 400.0, "cl": 40.0, "l": 0.4, "u": 3.0}

# Things you pour rather than weigh. No sane recipe uses 400 ml of olive oil
# for four, and the model reaches for that number constantly, so these get a
# much tighter ceiling and are marked flexible — the app already knows how to
# say "a splash". Matched on the name because the catalogue has no field for
# it; crude, and still right far more often than 150 cl of oil.
POUR_WORDS = ("oil", "vinegar", "huile", "vinaigre")
POUR_PER_PERSON = {"g": 15.0, "ml": 15.0, "cl": 1.5, "l": 0.015, "u": 1.0}


# Weighed ingredients by shelf, per person: (the most that is still a plate
# of food, what to bring it down to). 400 g a head for everything let 800 g of
# chicken and 800 g of potatoes through for two, and trimming to the ceiling
# kept it there. Over the ceiling, an amount now comes down to an ordinary
# portion instead.
PORTION_G = {
    "meat": (250.0, 150.0), "seafood": (250.0, 150.0),
    "produce": (250.0, 175.0), "frozen": (250.0, 150.0),
    "pantry": (150.0, 90.0), "bakery": (200.0, 100.0), "dairy": (250.0, 100.0),
}


def _need_qty(q: float, ing: dict, serves: int) -> tuple[float, bool]:
    unit = ing.get("unit", "g")
    name = str(ing.get("name", "")).lower()
    heads = max(1, serves)
    if unit == "g" and not any(w in name for w in POUR_WORDS) and ing.get("category") in PORTION_G:
        most, usual = PORTION_G[ing["category"]]
        return (round(q, 2), False) if q <= most * heads else (round(usual * heads, 2), True)
    table = POUR_PER_PERSON if any(w in name for w in POUR_WORDS) else NEED_PER_PERSON
    cap = table.get(unit, 400.0) * heads
    return (round(q, 2), False) if q <= cap else (round(cap, 2), True)


def _season_qty(q: float, unit: str, serves: int) -> tuple[float, bool]:
    cap = SEASON_PER_PERSON.get(unit, 8.0) * max(1, serves)
    return (round(q, 2), False) if q <= cap else (round(cap, 2), True)


def _slugish(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _as_known_id(text: str, known: dict) -> str | None:
    """Is this "extra" actually an ingredient id, or the name of one?

    The model was told to use ids for anything in the list and plain shopper's
    text for anything else. It did neither: it wrote `u_mozarella`, `goat` and
    `courgette` into extras — ids, for ingredients that ARE in the kitchen.
    Printed as written, the app showed a household "u_mozarella" on its
    shopping list, and the mozzarella they had just cooked with never counted
    as an ingredient they had used.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw in known:
        return raw
    slug = _slugish(raw)
    for candidate in (slug, f"u_{slug}"):
        if candidate in known:
            return candidate
    for iid, ing in known.items():
        if _slugish(ing.get("name", "")) == slug:
            return iid
    return None


def _resolve_id(value, known: dict) -> str | None:
    """`"Spinach (g)"` -> `spinach`.

    The id list is written `spinach = Spinach (g)`, and a 2B model copies the
    right-hand side: every id in its recipe was a name with a unit on it, the
    whole recipe cleaned down to nothing, and the cook got "didn't hold
    together" for a dinner that was fine. Only an exact id or an exact name
    is accepted, the same as for extras — nothing is guessed at."""
    if not isinstance(value, str):
        return None
    if value in known:
        return value
    return _as_known_id(re.sub(r"\s*\([^)]*\)\s*$", "", value), known)


def _undoubled(word: str) -> str:
    return re.sub(r"(.)\1+", r"\1", word)


def _contradictions(steps: list[dict], used: set[str], known: dict, dish: str = "") -> list[str]:
    """Steps that name an ingredient this recipe does not contain.

    "Boil rice in salted water" followed by "cooking the pasta separately
    ensures it absorbs the sauce" is one dish described as two, and no amount
    of prompting reliably stops a small model doing it. It is never silently
    repaired — a rewritten method is a method nobody wrote — but it is said out
    loud, where the cook can see it before the pan is hot.
    """
    names = {}
    for ing in known.values():
        nm = str(ing.get("name", "")).strip()
        if len(nm) >= 4 and " " not in nm and nm.lower() not in NOT_A_FOOD:
            names[nm.lower()] = nm
    # "Rice" is in a recipe that uses "Basmati rice"; "egg" is in one that uses
    # "Eggs". Every word of every name used counts, singular and plural.
    used_words: set[str] = set()
    # The dish's own name isn't a missing ingredient: "the curry" in a chickpea
    # curry, "the tortilla" in a tortilla, "caramel" in caramelised pork.
    for w in re.findall(r"[a-z]{4,}", dish.lower()):
        used_words |= {w, w[:-1] if w.endswith("s") else w + "s", w[:5]}
    for name in used:
        for w in re.findall(r"[a-z]{3,}", name.lower()):
            used_words |= {w, w[:-1] if w.endswith("s") else w + "s"}
    # Spelling: the household's "Mozarella" (off a receipt) is the step's
    # "mozzarella". Doubled letters are the usual slip, so both sides are
    # compared with them collapsed.
    used_words |= {_undoubled(w) for w in used_words}
    out: list[str] = []
    for i, st in enumerate(steps, 1):
        # "Heat olive oil" names the oil for the pan, which is left unlisted as
        # often as salt is — not the olives nobody bought.
        said = re.sub(r"\bolive oil\b", "oil", st.get("do", "") + " " + st.get("why", ""), flags=re.I)
        for word in set(re.findall(r"[a-zA-Z]{4,}", said)):
            low = word.lower()
            hit = names.get(low) or names.get(low[:-1] if low.endswith("s") else low)
            if (hit and hit.lower() not in used and low not in used_words and hit.lower() not in used_words
                    and low[:5] not in used_words and _undoubled(low) not in used_words
                    and _undoubled(low[:-1] if low.endswith("s") else low) not in used_words):
                line = f"step {i} mentions {low}"
                if line not in out:
                    out.append(line)
    return out[:4]


# Catalogue names that are also ordinary words in a method. "Garnish with…",
# "season to taste", "boil some water" name no missing ingredient, and a check
# that now sends a recipe back to be rewritten cannot afford to think they do.
# The second half came from the corpus: single-word catalogue names that turn
# up in a method without being in that recipe's ingredients most of the time.
NOT_A_FOOD = frozenset("""
batter bone broth dough dressing filling garnish glaze green herb juice liquid
marinade meat fish pepper roast round salt sauce season serve spice stock topping
vegetable water oil ice mixture crumb pulp zest skin seed leftover fillet piece
chunk strip slice cube wedge half sheet plate bowl heat cook drain rinse cover
brown golden chop mince dash pinch sprig handful sugar flour fat cream
ingredient sprinkle roll each cake crust firm seasoning fruit muffin salad toothpick
recipe fine pastry jelly frosting gravy icing toast whole meal meatball meringue
pancake mashed cupcake custard plain cornbread savory savoury bitter cheese base
""".split())

# A cue or a heat of "n/a" is a form the model filled in, not a thing to watch
# for — the app printed "until n/a" under every step.
EMPTY_WORDS = {"n/a", "na", "none", "nothing", "null", "-", "—", "no", "not applicable", "off",
               "no cue", "none needed", "not needed", "no heat", "nothing specific", "n a"}

# Words that put a step on the hob or in the oven, and words that plainly keep
# it off. A heat is dropped only from an off-hob step that says nothing about
# cooking: "Wash the courgettes" on Low loses it, "Add the tomatoes" on Medium
# keeps it — a hob step doesn't have to say "fry". Same rule as the browser's
# core/brief.js.
HOB_WORDS = re.compile(
    r"\b(fr(y|ies|ied|ying)|boil|simmer|saut|sear|heat|bak|roast|grill|toast|brown|melt|"
    r"steam|poach|cook|pan|wok|oven|skillet|reduc|warm|scrambl|blanch|char|carameli|"
    r"crisp|wilt|stir|°|broil|hob|flame)", re.I)
OFF_HOB_WORDS = re.compile(
    r"\b(bowl|wash|rinse|pat\b|marinat|mix\b|mixing|whisk|beat\b|combine|thread|skewer|chop|slice|dice|"
    r"cut\b|mince|grate|peel|blend|mash|knead)", re.I)


def _prep(v) -> str | None:
    """"diced", "beaten", "cut into strips" — not "Prepare the hummus mixture".

    The model put whole method sentences in prep, about other ingredients:
    flour was "Prepare the bread crumbs", eggs were "Prepare the hummus
    mixture". Printed under the ingredient, that is misinformation."""
    text = _text(v, 60)
    if not text:
        return None
    words = text.lower().strip(" .").split()
    if (len(words) > 5 or text.rstrip().endswith(".") or words[0] in {"prepare", "wash", "use", "mix"}
            or {"the", "a", "an", "for", "to"} & set(words) or " ".join(words) in EMPTY_WORDS):
        return None
    return text


_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
                 "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "sixty": 60}
_TIME_IN_TEXT = re.compile(
    r"\b(\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
    r"twenty|thirty|forty-five|forty|sixty)(?:\s*(?:-|to|or)\s*(\d+|[a-z]+))?\s*"
    r"(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b(\s+(?:per|a|on each|each)\s+side)?", re.I)


def text_minutes(text: str) -> float:
    """The minutes a step's own words give it, added up.

    "Roast for 12 minutes on the first side" is 12, "four minutes on the first
    side, three on the second" is 7, "3-4 minutes each side" is 8. A step whose
    words say twelve minutes and whose `minutes` says two was shown as "watch
    closely", got no timer, and made a 30-minute tray bake read 15."""
    total = 0.0
    for low, high, unit, per_side in _TIME_IN_TEXT.findall(str(text or "")):
        n = high or low
        n = float(n) if re.fullmatch(r"\d+(?:\.\d+)?", n) else _NUMBER_WORDS.get(n.lower())
        if n is None:
            continue
        u = unit.lower()
        mins = n * 60 if u.startswith("h") else n / 60 if u.startswith("s") else n
        total += mins * (2 if per_side else 1)
    # "four minutes on the first side, three on the second": the second number
    # has no unit of its own.
    second = re.search(r"minutes? on the first side,?\s+(?:and\s+)?(\w+)\s+on the (?:second|other)", str(text or ""), re.I)
    if second:
        n = second.group(1)
        total += float(n) if n.isdigit() else _NUMBER_WORDS.get(n.lower(), 0)
    return total


_OVEN_STEP = re.compile(r"\b(roast|bake|baking|oven)", re.I)
_OVEN_ON = re.compile(r"\b(preheat|turn the oven on|heat the oven|oven on to|switch the oven on)", re.I)


def uses_oven(steps: list[dict]) -> bool:
    return any(_OVEN_STEP.search(st.get("do", "")) or "oven" in str(st.get("heat", "")).lower() for st in steps)


def heats_oven(steps: list[dict]) -> bool:
    return any(_OVEN_ON.search(st.get("do", "")) for st in steps)


def _total_minutes(stated: float | None, steps: list[dict]) -> int:
    """What the steps add up to, unless the stated total is close to it.

    A recipe said 360 minutes over steps adding up to 295. The clock the cook
    lives through is the steps; a stated total is allowed a quarter of an hour
    on top of them (resting, the oven coming up), and nothing else. An oven no
    step turns on still has to get hot: ten minutes, counted."""
    summed = sum(s["minutes"] for s in steps)
    if summed and uses_oven(steps) and not heats_oven(steps):
        summed += 10
    if not summed:
        return int(max(1, min(600, stated))) if stated else 15
    if stated and summed <= stated <= summed + 15:
        return int(min(600, stated))
    return int(max(1, min(600, summed)))


# ------------------------------------------------------------------ time budget
#
# Almost everyone opening this app at the end of a day wants dinner in fifteen
# or twenty minutes, not a project. A 2B model left to itself wrote four hours
# of boiled courgettes. So every request gets a budget unless it asks for time.

DEFAULT_MINUTES = 20

_SLOW = re.compile(
    r"\b(slow|braise[ds]?|stew|roast|weekend|sunday|no rush|take (my|our|your) time|"
    r"all afternoon|project|long cook|oven[- ]baked|casserole|lasagne|lasagna|cake)\b", re.I)
_TIRED = re.compile(r"\b(drained|exhausted|tired|knackered|shattered|wiped out|destroyed|done in|beat tonight|dead on my feet|no energy)\b", re.I)


def time_budget(text: str, explicit: int | None = None, named_dish: bool = False,
                default: int = None) -> int | None:
    """Minutes the recipe must fit in, or None when they asked for something slow.

    "Give me 15 minutes" -> 15. "Half an hour" -> 30. "I'm drained" -> 15.
    "A slow Sunday roast" -> None. Anything else -> 20.

    A named dish ("make mochi", "a lasagne") takes the time it takes: only a
    number they said limits it. The 20-minute default is for "something for
    tonight", where the choice of dish is ours to make quick."""
    if explicit:
        return int(max(5, min(600, explicit)))
    t = str(text or "").lower()
    hours = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\b", t)
    mins = re.search(r"\b(\d{1,3})\s*(?:m|min|mins|minutes?)\b", t)
    if mins:
        return int(max(5, min(600, int(mins.group(1)) + (60 * float(hours.group(1)) if hours else 0))))
    if hours:
        return int(max(5, min(600, 60 * float(hours.group(1)))))
    if re.search(r"\bhalf an? hour\b", t):
        return 30
    if re.search(r"\b(an|one) hour and a half\b", t):
        return 90
    if re.search(r"\b(an|one) hour\b", t):
        return 60
    if named_dish:
        return None
    if _SLOW.search(t):
        return None
    if _TIRED.search(t) or re.search(r"\b(quick|fast|asap|hurry|in a rush)\b", t):
        return 15
    return default or DEFAULT_MINUTES


# A step that names a pan and only mixes. "In a frying pan, whisk the egg",
# then "in a bowl, combine the cheese with the egg", then back to the pan: the
# model filled the container into the sentence without thinking about it.
_PAN = re.compile(r"\b(frying pan|pan|wok|skillet|saucepan|pot)\b", re.I)
_MIXING = re.compile(r"\b(whisk|beat(?!en)|marinat|combine|mix(?!ture))\w*", re.I)
_COOKING = re.compile(r"\b(fry|fries|fried|frying|cook|scrambl|saut|sear|heat|melt|simmer|boil|toast|brown|"
                      r"wilt|stir-?fry|pour|add|tip|return|transfer|put)\w*", re.I)


def _wrong_container(steps: list[dict]) -> list[str]:
    out = []
    for i, st in enumerate(steps, 1):
        text = st.get("do", "")
        without_pan_names = re.sub(r"\bfrying pan\b", "pan", text, flags=re.I)
        if _PAN.search(text) and _MIXING.search(text) and not _COOKING.search(without_pan_names):
            verb = _MIXING.search(text).group(0).lower()
            out.append(f"step {i} says to {verb} in a pan; whisking, beating and mixing happen in a bowl, "
                       "the pan is only for what cooks")
    return out


# (what gets made, how a step that USES it says so)
# Whole words only: "in a saucepan" is not the sauce.
_MADE_THINGS = (("marinade", r"marinat"), ("sauce", r"sauces?\b"), ("dressing", r"dressing\b"),
                ("mixture", r"mixture\b"), ("batter", r"batter\b"), ("glaze", r"glaz"))


def _out_of_order(steps: list[dict]) -> list[str]:
    """"Marinate the chicken" at step 1, "Mix the marinade" at step 2.

    A model that writes steps as a list of things to happen, not an order to do
    them in. Caught only for things a recipe MAKES and then uses — marinades,
    sauces, dressings — where the order is unambiguous from the words."""
    out = []
    texts = [str(st.get("do", "")).lower() for st in steps]
    for thing, used in _MADE_THINGS:
        made = next((n for n, t in enumerate(texts)
                     if re.search(rf"\b(mix|make|prepare|combine|whisk|stir together)\b.*\b{thing}", t)), None)
        if made is None:
            continue
        early = next((n for n in range(made) if re.search(rf"\b{used}", texts[n])), None)
        if early is not None:
            out.append(f"step {early + 1} uses the {thing} before step {made + 1} makes it; make it first")
    return out


# ------------------------------------------------ against the real recipe
#
# A named dish is written from a real recipe (rag.find_dish), and a small model
# still loses what matters in the retelling: asked for mochi, it mapped
# "glutinous rice flour" onto plain flour and dropped the microwave step, so
# the mochi was never cooked. With the real recipe in hand both are checkable.

# A head noun that says little on its own: "rice FLOUR" needs its "rice".
_GENERIC_HEADS = {"flour", "sugar", "sauce", "oil", "vinegar", "milk", "cheese", "powder", "paste",
                  "rice", "pepper", "salt", "stock", "cream", "wine", "juice", "noodles", "noodle"}
_NOT_KEY = {"water", "salt", "pepper", "sugar", "oil", "butter", "ice", "cooking", "spray", "optional",
            "garnish", "taste", "boiling", "cold", "warm", "hot", "unsalted", "salted", "roasted",
            "skinned", "large", "small", "plain", "white", "brown", "extra", "virgin", "light", "heavy",
            "to", "tbsp", "tsp", "tablespoon", "tablespoons", "teaspoon", "teaspoons", "cup", "cups",
            "about", "and", "or", "of", "for", "freshly", "finely", "coarsely", "lightly", "beaten"}

_COOKING_FAMILIES = {
    "microwave it": re.compile(r"\bmicrowav", re.I),
    "steam it": re.compile(r"\bsteam", re.I),
    "bake it": re.compile(r"\b(bak|oven|roast)", re.I),
    "fry it": re.compile(r"\b(fr(y|ies|ied|ying)|saut|sear|pan)", re.I),
    "boil or simmer it": re.compile(r"\b(boil(?!ing water)|simmer|poach)", re.I),
    "grill it": re.compile(r"\b(grill|broil)", re.I),
}


def _key_words(line: str) -> list[list[str]]:
    """What an ingredient line can't lose, as alternatives — any one will do.

    "glutinous rice flour" -> [["glutinous"]]: a generic head ("flour",
    "cheese") says nothing, its modifier says everything, and "parmesan cheese"
    is satisfied by an ingredient called just "Parmesan".
    "1/4 cup roasted unsalted peanuts" -> [["peanut"]].
    "2 to 3 tbsp milk or light cream" -> [["milk"], ["cream"]]."""
    from .template import _food                # imported late: template imports nothing of ours
    text = _food(line).replace("-", " ")
    out = []
    for part in re.split(r"\bor\b|/", text):
        words = [w for w in part.split() if len(w) > 2 and w not in _NOT_KEY]
        if not words:
            continue
        last = words[-1]
        head = last[:-1] if last.endswith("s") and len(last) > 3 else last
        if last in _GENERIC_HEADS or head in _GENERIC_HEADS:
            # "glutinous rice flour": past every generic word to the one that
            # isn't — "glutinous", not "rice".
            specific = next((w for w in reversed(words) if w not in _GENERIC_HEADS
                             and w.rstrip("s") not in _GENERIC_HEADS), None)
            out.append([specific or head])
        else:
            out.append([head])
    # A line that is only water, salt or sugar has nothing to keep.
    return [alt for alt in out if alt and alt[0] not in _NOT_KEY]


# A pasta shape is pasta: "spaghetti" in the real recipe is kept by an
# ingredient called "Pasta".
_SAME_AS = {w: "pasta" for w in ("spaghetti", "penne", "fusilli", "linguine", "tagliatelle", "fettuccine",
                                  "rigatoni", "macaroni", "farfalle", "bucatini", "vermicelli")}


def against_reference(recipe: dict, reference: dict, known: dict) -> list[str]:
    out: list[str] = []
    have = " ".join([str(known.get(n["id"], {}).get("name", n["id"])) for n in recipe.get("needs") or []]
                    + [str(known.get(n["id"], {}).get("name", n["id"])) for n in recipe.get("seasoning") or []]
                    + list(recipe.get("extras") or [])).lower().replace("-", " ")
    have += " " + " ".join(sorted({v for k, v in _SAME_AS.items() if k in have}))
    have += " " + " ".join(sorted({k for k, v in _SAME_AS.items() if v in have}))
    for line in str(reference.get("ingredients") or "").split("\n"):
        key = _key_words(line)
        if key and not any(all(re.search(rf"\b{re.escape(w)}", have) for w in alt) for alt in key):
            from .template import _food
            out.append(f"the real recipe uses {_food(line).replace('-', ' ')}, which yours leaves out or "
                       "swaps for something else; keep it (in \"extras\" if it has no id)")
    method = " ".join(st.get("do", "") + " " + str(st.get("heat", "")) for st in recipe.get("steps") or [])
    ref_method = str(reference.get("instructions") or "")
    for does, rx in _COOKING_FAMILIES.items():
        if rx.search(ref_method) and not rx.search(method) and not any(
                r.search(method) for d, r in _COOKING_FAMILIES.items() if r.search(ref_method) and d != does):
            out.append(f"the real recipe says to {does}, and no step of yours does; keep that step")
            break
    return out[:4]


# The last step should put the food in front of someone. "Remove the eggs from
# the heat and stir in the green onion" — and then what?
_SERVES = re.compile(
    r"\b(serv|plate|plating|divide|eat|enjoy|spoon (it |them )?(onto|over|into)|ladle|top (it |them |each )?with|"
    r"scatter|garnish|sprinkle|drizzle|dust|slice (it |them )?(and|into)|cut (it |them )?into|pour (it |them )?(into|over)|"
    r"transfer to (a |the |warm )?(plate|serving|bowl)|pile|arrange on|dish up|bowls|plates|toast)", re.I)

# Less of a real ingredient than anyone could taste, per recipe. The unit is
# the usual cause: "Green Onion" kept in grams, and the model wrote 1 meaning
# one onion. Herbs by the gram and seasonings are left alone.
# Not the cupboard: half a gram of nutmeg or baking powder is right.
_CRUMB_G = {"produce": 3.0, "meat": 20.0, "seafood": 20.0, "dairy": 5.0, "bakery": 10.0, "frozen": 10.0}


def _crumbs(recipe: dict, known: dict) -> list[str]:
    out = []
    for n in recipe.get("needs") or []:
        ing = known.get(n["id"]) or {}
        floor = _CRUMB_G.get(ing.get("category"))
        if ing.get("unit") == "g" and floor and 0 < float(n.get("qty") or 0) < floor:
            out.append(f"{n['qty']:g} g of {str(ing.get('name', n['id'])).lower()} is a crumb; "
                       f"{n['id']} is weighed in grams, so give the weight a person would use")
    return out[:3]


# ------------------------------------------------ what they asked for
#
# "Make a pasta with bechamel" came back as vegetable pasta: the bechamel was
# simply dropped, and nothing checked. The foods a request names are now
# required in the recipe. A food is a catalogue ingredient ("pasta",
# "mushrooms", "pesto") or one of the sauces and components below that the
# catalogue doesn't list; "meat", "fish" and "vegetables" mean a shelf.
# Describing words ("creamy", "quick", "warm") are none of these, which is why
# the corpus can't be used to decide: it puts "creamy" in 13,000 ingredient
# lines.

COMPONENTS = frozenset("""
bechamel béchamel hollandaise bolognese ragu ragout carbonara marinara alfredo arrabbiata puttanesca
gravy aioli mayonnaise vinaigrette tzatziki salsa guacamole chutney raita dal dhal pesto
roux custard meringue ganache caramel crumble pastry dough batter tempura gnocchi ravioli tortellini
risotto polenta couscous quinoa bulgur falafel dumplings dumpling noodles ramen udon soba
omelette frittata quiche souffle gratin lasagne lasagna curry stew chowder bisque broth
teriyaki satay katsu kimchi miso sushi tacos taco burrito quesadilla nachos enchiladas
pancakes crepes waffles scones muffins brownies flatbread naan pitta focaccia
""".split())

# The same thing by another name: a recipe that makes a "white sauce" from
# butter, flour and milk has made the bechamel.
SAME_THING = {
    "bechamel": ("white sauce",), "ragu": ("bolognese",), "bolognese": ("ragu", "meat sauce"),
    "aioli": ("garlic mayonnaise",), "dal": ("dhal", "lentil"), "dhal": ("dal", "lentil"),
    "lasagne": ("lasagna",), "lasagna": ("lasagne",), "crepes": ("pancakes",),
}

CATEGORY_WORDS = {
    "meat": {"meat"}, "fish": {"seafood"}, "seafood": {"seafood"},
    "vegetable": {"produce"}, "vegetables": {"produce"}, "veg": {"produce"}, "veggie": {"produce"},
    "veggies": {"produce"}, "greens": {"produce"},
}

_NEGATION = re.compile(r"\b(without|no|not|avoid|except|minus|hold the|skip the|but no)\b[^,.;]*", re.I)


def _fold_accents(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(text)) if not unicodedata.combining(c))


def asked_foods(text: str, known: dict) -> list[str]:
    """The foods a request names, as it named them. "Make a pasta with
    bechamel, no mushrooms" -> ["pasta", "bechamel"]."""
    from .rag import NOT_A_DISH, STOPWORDS     # imported late: rag imports nothing of ours at load
    text = _NEGATION.sub(" ", _fold_accents(str(text or "")).lower())
    names = {}
    for ing in known.values():
        nm = _fold_accents(str(ing.get("name", "")).strip().lower())
        if nm and nm not in NOT_A_FOOD:
            names[nm] = True
    words = re.findall(r"[a-z]+", text)
    out: list[str] = []
    used = set()
    for n in (2, 1):                           # "coconut milk" before "milk"
        for i in range(len(words) - n + 1):
            if any(j in used for j in range(i, i + n)):
                continue
            chunk = words[i:i + n]
            phrase = " ".join(chunk)
            if n == 1 and phrase in CATEGORY_WORDS:       # "fish" is a shelf, before it's a non-food word
                out.append(phrase)
                used.add(i)
                continue
            if any(w in STOPWORDS or w in NOT_A_DISH or w in NOT_A_FOOD for w in chunk):
                continue
            single = phrase[:-1] if phrase.endswith("s") else phrase
            if (phrase in names or single in names or phrase + "s" in names or phrase + "es" in names
                    or phrase in COMPONENTS or single in COMPONENTS):
                out.append(phrase)
                used.update(range(i, i + n))
    return list(dict.fromkeys(out))[:6]


def missing_asked(recipe: dict, asked: list[str], known: dict) -> list[str]:
    parts = [recipe.get("name", "")] + list(recipe.get("extras") or [])
    parts += [str((known.get(n["id"]) or {}).get("name", n["id"])) for n in (recipe.get("needs") or []) + (recipe.get("seasoning") or [])]
    parts += [st.get("do", "") for st in recipe.get("steps") or []]
    have = _fold_accents(" ".join(parts)).lower()
    shelves = {(known.get(n["id"]) or {}).get("category") for n in recipe.get("needs") or []}
    out = []
    for food in asked:
        if food in CATEGORY_WORDS:
            if not (CATEGORY_WORDS[food] & shelves):
                out.append(f"they asked for {food} and this recipe has none")
            continue
        stem = food[:-1] if food.endswith("s") else food
        if any(alias in have for alias in SAME_THING.get(food, ())):
            continue
        if not re.search(rf"\b{re.escape(stem)}", have) and not re.search(rf"\b{re.escape(_undoubled(stem))}", _undoubled(have)):
            out.append(f"they asked for {food} and this recipe has none; the dish must have it")
    return out


def recipe_problems(recipe: dict, budget: int | None, stock_ids: set[str] | None = None,
                    reference: dict | None = None, known: dict | None = None,
                    asked: list[str] | None = None) -> list[str]:
    """What is wrong with a cleaned recipe, in words the model can act on.

    Empty means it can go to the cook. Anything here sends it back to be
    written again once, with this list as the reason — the model is told what
    it got wrong rather than asked to roll the dice a second time."""
    out: list[str] = []
    steps = recipe.get("steps") or []
    total = sum(s.get("minutes") or 0 for s in steps)
    if budget:
        if total > budget + max(3, budget // 5):
            out.append(f"the steps add up to {total} minutes; they only have {budget}")
        # The prompt asks for three or four. Six still cooks in time, and is not
        # worth another twenty seconds of waiting to shave off.
        if len(steps) > (6 if budget <= 20 else 8):
            out.append(f"{len(steps)} steps is too many for {budget} minutes; use at most four")
    ceiling = budget or 240
    for i, st in enumerate(steps, 1):
        for n, unit in re.findall(r"(\d{1,4})\s*(min|minute|minutes|hour|hours|hr|hrs)\b", st.get("do", ""), re.I):
            said = int(n) * (60 if unit.lower().startswith("h") else 1)
            if said > ceiling:
                out.append(f"step {i} says {n} {unit}, far too long")
    for problem in _out_of_order(steps):
        out.append(problem)
    out += _wrong_container(steps)
    # "Preheat oven to 350 C": a Fahrenheit number with a Celsius letter. No
    # home oven goes past about 260 °C.
    for i, st in enumerate(steps, 1):
        for n in re.findall(r"(\d{3})\s*°?\s*C\b", st.get("do", "") + " " + str(st.get("heat", ""))):
            if int(n) > 260:
                out.append(f"step {i} says {n} °C, which is a Fahrenheit number; {n} °F is "
                           f"{round((int(n) - 32) * 5 / 9 / 5) * 5} °C")
                break
    if uses_oven(steps):
        for i, st in enumerate(steps, 1):
            heat = str(st.get("heat", ""))
            if (re.search(r"\b(roast|bake)", st.get("do", ""), re.I) and heat
                    and not re.search(r"oven|°", heat, re.I)):
                out.append(f"step {i} roasts or bakes but its heat is '{heat}', a hob setting; "
                           "give it an oven temperature like 'Oven 200 °C'")
        if not heats_oven(steps):
            out.append("nothing turns the oven on; make the first step 'Turn the oven on to 200 °C'")
    if asked:
        out += missing_asked(recipe, asked, known or {})
    if reference:
        out += against_reference(recipe, reference, known or {})
    out += _crumbs(recipe, known or {})
    if steps and not _SERVES.search(steps[-1].get("do", "")):
        out.append(f"the method stops at \"{steps[-1].get('do', '')[:60]}\" and never says how it is served; "
                   "end with a step like \"Spoon onto warm plates and eat straight away\"")
    for line in recipe.get("contradictions") or []:
        out.append(f"{line}, which is not in the ingredients — list it or don't use it")
    if budget and budget <= 30 and stock_ids:
        missing = [n["id"] for n in recipe.get("needs") or []
                   if n["id"] not in stock_ids and not n.get("flexible")]
        if missing:
            out.append("it needs " + ", ".join(missing[:4]) + ", which they haven't got; "
                       "use only what is in the kitchen")
    return out[:6]


def clean_stars(raw: dict) -> tuple[float, int]:
    """(stars, complexity) from whatever the model sent.

    Difficulty is half a star to five. `complexity` (1-3) is still stored
    because the app's filters use it, and it is read off the stars so the two
    can never disagree. A model that sent only the old field gets stars from
    it; one that sent neither gets two stars, the middle of an ordinary dinner."""
    s = _num(raw.get("stars"))
    if s is None or s <= 0:
        s = {1: 1.0, 2: 2.5, 3: 4.0}.get(raw.get("complexity"), 2.0)
    s = min(5.0, max(0.5, round(s * 2) / 2))
    return s, (1 if s <= 1.5 else 2 if s <= 3 else 3)


# ---- difficulty, worked out rather than asked for --------------------------
#
# A model's star rating was a guess, and a plain omelette came back at two
# stars. Difficulty is what the cook lives through, and four things decide it:
#   - how many ingredients there are to find, weigh and prepare
#   - how many go in at once, at the busiest step
#   - how many things there are to wash afterwards
#   - how long it all takes
# An egg fried in one pan is half a star; a lasagne is about four. Technique
# on its own is NOT counted: croissants score on their hours and their rolling
# pin, not on the lamination. Mirrored in the browser as starsFromMethod in
# frontend/src/core/engine.js — change both together.

_WASH = (  # (what gets washed, the words that mean it is used)
    ("pan", r"\b(frying pan|skillet|griddle)\b|(?<!sauce)\bpan\b"),
    ("saucepan", r"\b(saucepan|pot|casserole|dutch oven|stockpot)\b"),
    ("wok", r"\bwok\b"),
    ("bowl", r"(?<!serving )(?<!warm )\bbowl\b"),
    ("oven dish", r"\b(baking|roasting|oven|ovenproof|gratin) (dish|tray|tin|sheet|pan)\b|\bbaking paper\b|\b(loaf|cake|pie|muffin) tin\b"),
    ("board and knife", r"\b(chop|dice|slice|mince|cube|halve|quarter|shred|julienne|peel|trim|cut)\w*"),
    ("grater", r"\b(grate|grated|grater|zest)\b"),
    ("colander", r"\b(drain|colander|sieve|sift|strain)\w*"),
    ("blender", r"\b(blend|blender|food processor|whizz|puree|purée)\w*"),
    ("mixer", r"\b(mixer|stand mixer|electric whisk)\b"),
    ("rolling pin", r"\b(rolling pin|roll out|roll it out)\b"),
    ("steamer", r"\bsteamer\b"),
)
_ANOTHER = re.compile(r"\b(another|second|separate|clean|large|small) (frying pan|pan|saucepan|pot|bowl)\b", re.I)


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))


def stars_from_method(recipe: dict) -> float:
    needs = recipe.get("needs") or []
    seasoning = recipe.get("seasoning") or []
    steps = recipe.get("steps") or []
    salt = {x.get("id") for x in seasoning}
    # Salt and pepper are not a shopping trip: a seasoning counts half.
    n = len(needs) + len(recipe.get("extras") or []) + 0.5 * len(seasoning)
    busiest = max((sum(0.5 if u in salt else 1 for u in st.get("uses") or []) for st in steps), default=1)
    text = " ".join(f"{st.get('do', '')} {st.get('heat', '')}" for st in steps)
    wash = sum(1 for _, words in _WASH if re.search(words, text, re.I))
    wash += len(_ANOTHER.findall(text))
    minutes = recipe.get("minutes") or sum(st.get("minutes") or 0 for st in steps) or 15

    score = (0.30 * _clamp01((n - 1) / 14)                     # 1 thing .. 15
             + 0.20 * _clamp01((busiest - 1) / 5)             # 1 at once .. 6
             + 0.25 * _clamp01((max(1, wash) - 1) / 6)        # 1 pan .. 7 things
             + 0.25 * _clamp01(math.log(max(minutes, 5) / 5) / math.log(36)))  # 5 min .. 3 h
    return min(5.0, max(0.5, round((0.5 + 4.5 * score) * 2) / 2))


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

    serves_n = int(max(1, min(12, _num(raw.get("serves")) or 4)))

    # Everything the model offered, from either list, sorted by what the
    # CATALOGUE says each thing is rather than by which array it arrived in.
    offered: list[tuple[dict, bool]] = [
        (n, False) for n in raw.get("needs") or [] if isinstance(n, dict)
    ] + [
        (s, True) for s in raw.get("seasoning") or [] if isinstance(s, dict)
    ]

    adjusted: list[str] = []
    needs, seasoning, seen = [], [], set()
    moved_to_needs = 0
    for item, came_as_seasoning in offered:
        iid, q = _resolve_id(item.get("id"), known), _num(item.get("qty"))
        if iid not in known or iid in seen or not q or q <= 0:
            continue
        seen.add(iid)
        if _is_seasoning(iid, known):
            qty, capped = _season_qty(q, known[iid].get("unit", "g"), serves_n)
            entry = {"id": iid, "qty": qty, "essential": item.get("essential") is True}
            if capped:
                # The number was not believable, so the app will not print one.
                entry["toTaste"] = True
            seasoning.append(entry)
            continue
        if came_as_seasoning:
            moved_to_needs += 1
        qty, trimmed = _need_qty(q, known[iid], serves_n)
        entry = {"id": iid, "qty": qty}
        if _prep(item.get("prep")):
            entry["prep"] = _prep(item.get("prep"))
        if item.get("flexible") is True or trimmed:
            entry["flexible"] = True
        if trimmed:
            # Recorded, not hidden. The number the model wrote was not
            # believable, and the cook is told which ones were brought down.
            adjusted.append(f"{known[iid].get('name', iid)}: "
                            f"{round(q, 2)} -> {qty} {known[iid].get('unit', 'g')}")
        needs.append(entry)
    needs, seasoning = needs[:14], seasoning[:10]

    # An "extra" that is really an id belongs with the ingredients, not on a
    # list of things to buy. No quantity is invented for it: qty 0 with
    # flexible set is the app's way of saying "you need this, the recipe never
    # said how much", and the panel prints no number rather than a made-up one.
    extras = []
    for raw_extra in raw.get("extras") or []:
        text = _text(raw_extra, 80)
        if not text:
            continue
        iid = _as_known_id(text, known)
        if iid and iid not in seen:
            seen.add(iid)
            if _is_seasoning(iid, known):
                seasoning.append({"id": iid, "qty": 0, "essential": False, "toTaste": True})
            else:
                needs.append({"id": iid, "qty": 0, "flexible": True})
        elif not iid:
            extras.append(text)
    needs, seasoning, extras = needs[:16], seasoning[:12], extras[:12]

    if moved_to_needs:
        print(f"recipes: {moved_to_needs} thing(s) the model called seasoning are not, "
              "by the catalogue — moved to the ingredients.")

    if not needs and not extras:
        return None

    steps = []
    for st in raw["steps"]:
        if not isinstance(st, dict) or not _text(st.get("do"), 240):
            continue
        m = _num(st.get("minutes")) or 0
        # The words win when they say longer: "roast for 12 minutes" is 12.
        m = max(m, text_minutes(st.get("do")))
        step = {"do": _text(st["do"], 240), "minutes": int(max(0, min(240, round(m))))}
        for key, n in (("why", 200), ("heat", 24), ("cue", 90)):
            value = _text(st.get(key), n)
            if value and value.lower().strip(" .") not in EMPTY_WORDS:
                step[key] = value
        # "Wash the courgettes" on Low heat: a heat on a step that goes nowhere
        # near the hob is a filled-in form field, and the app draws it as a
        # flame. The words of the step decide, not the model's form-filling.
        if ("heat" in step and OFF_HOB_WORDS.search(step["do"])
                and not HOB_WORDS.search(step["do"] + " " + step["heat"])):
            del step["heat"]
        # What goes in the pan now. Only ids this recipe actually contains —
        # a step that claims to add something the recipe never bought is the
        # same invention as an invented id, and gets the same treatment.
        in_recipe = {x["id"] for x in needs} | {x["id"] for x in seasoning}
        uses = [u for u in (_resolve_id(x, known) for x in st.get("uses") or []) if u in in_recipe]
        if uses:
            step["uses"] = list(dict.fromkeys(uses))[:8]
        steps.append(step)
    steps = steps[:14]
    if len(steps) < 2:
        return None

    types = [t for t in raw.get("types") or [] if t in MEAL_TYPES]
    serves = _num(raw.get("serves"))
    out = {
        "id": f"{origin[:3]}_{secrets.token_hex(4)}",
        "name": _text(raw["name"], 80),
        "minutes": _total_minutes(_num(raw.get("minutes")), steps),
        "types": types or ["dinner"],
        "cuisine": _text(raw.get("cuisine"), 30) or "Everyday",
        "serves": int(max(1, min(12, serves))) if serves else 4,
        "needs": needs,
        "seasoning": seasoning,
        "steps": steps,
        "origin": origin,
    }
    used_names = {str(known[x["id"]].get("name", "")).lower()
                  for x in needs + seasoning if x["id"] in known}
    used_names |= {e.lower() for e in extras}
    if adjusted:
        out["adjusted"] = adjusted[:6]
        print("recipes: quantities brought down to something edible — " + "; ".join(adjusted))
    wrong = _contradictions(steps, used_names, known, out["name"] or "")
    if wrong:
        out["contradictions"] = wrong
        print("recipes: the method contradicts the ingredients — " + "; ".join(wrong))
    if extras:
        out["extras"] = extras
    out["stars"], out["complexity"] = clean_stars({"stars": stars_from_method(out)})
    if _text(raw.get("description"), 200):
        out["description"] = _text(raw.get("description"), 200)
    return out
