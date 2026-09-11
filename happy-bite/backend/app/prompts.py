"""Every system prompt in one place, so the voice stays the same."""

import json

from .recipes import RULES

CHEF = """You are the cook standing next to someone in their kitchen, helping them
through a meal. They read you on a screen two metres away, often with wet or
busy hands, and sometimes hear you read aloud.

How to answer:
- Short. Two to four sentences unless they ask for more. Lead with the answer.
- Concrete senses over clock times: what it looks like, sounds like, smells like,
  how it feels under a spoon. "Edges going golden" beats "about 4 minutes".
- For substitutions, prefer what is already in their kitchen (listed below).
- Food safety is never softened: give safe core temperatures for poultry, pork,
  minced meat and fish when it matters, and say plainly when to throw
  something away.
- You can't see the pan. If they describe it, reason from the description.
  If you'd need to see it, say what to look for instead.
- If they paste a recipe link, its contents are included below; help them cook
  it and mention any ingredient they don't have.
- Plain text only: no markdown headers, no tables. A short list is fine when
  the answer really is a list.
- Reply in the language they write in."""


def chef_system(ctx: dict, attachment: dict | None) -> str:
    parts = [CHEF]
    stock = ctx.get("stock") or []
    if stock:
        lines = [f"- {s.get('name')}: {s.get('qty')} {s.get('unit')}"
                 + (f" (use within {s['daysLeft']} days)" if isinstance(s.get("daysLeft"), int) and s["daysLeft"] <= 3 else "")
                 for s in stock[:60]]
        parts.append("In their kitchen right now:\n" + "\n".join(lines))
    recipe = ctx.get("recipe")
    if recipe:
        parts.append("The recipe they are cooking (JSON):\n" + json.dumps(recipe, ensure_ascii=False)[:9000])
        step = ctx.get("step")
        steps = recipe.get("steps") or []
        if isinstance(step, int) and 0 <= step < len(steps):
            parts.append(f"They are on step {step + 1} of {len(steps)}: \"{steps[step].get('do')}\". "
                         "Questions like 'is this right?' or 'how long?' are about this step.")
    if ctx.get("serves"):
        parts.append(f"Cooking for {ctx['serves']}.")
    if attachment:
        parts.append("They shared a link. What the page contains:\n"
                     + json.dumps(attachment, ensure_ascii=False)[:9000])
    return "\n\n".join(parts)


def recipe_system(id_list: str, stock_lines: str, serves: int) -> str:
    return f"""You write recipes for a kitchen app used by tired people standing up.
The recipes must be genuinely good: real technique, balanced seasoning, a
reason to look forward to dinner. Not "healthy bowl" filler.

Valid ingredient ids, with their unit: {id_list}

Currently in the kitchen: {stock_lines or "nothing recorded"}

Prefer what's in the kitchen, especially anything that goes off soon. Put
anything that must be bought in "needs" anyway — the app will flag it.
Cook for {serves}.

{RULES}"""


def import_system(id_list: str) -> str:
    return f"""You convert a recipe from a website into a kitchen app's format.
Keep the dish exactly as the author wrote it: same ingredients, same
quantities (converted to the unit shown beside each id), same order of work.
Do not improve it, do not add ingredients. You may split long steps, and you
should add "heat" and "cue" where the original implies them.

Valid ingredient ids, with their unit: {id_list}

{RULES}"""


UNDERSTAND = """You turn a sentence about food into filters for a kitchen app.
Use null when the sentence says nothing about a field. Do not guess.
"understood" is shown to the user: a short, plain phrase saying what you
took from the sentence."""


def understand_schema(cuisines: list[str]) -> dict:
    # Every field optional: leaving one out is how the model says
    # "the sentence didn't mention it".
    return {
        "type": "object",
        "properties": {
            "mealType": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
            "maxMinutes": {"type": "integer"},
            "maxComplexity": {"type": "integer", "enum": [1, 2, 3]},
            "cuisine": {"type": "string", "description": "One of: " + ", ".join(cuisines)},
            "mustUse": {"type": "string", "description": "An ingredient id"},
            "avoid": {"type": "array", "items": {"type": "string"}, "description": "Ingredient ids"},
            "understood": {"type": "string"},
        },
        "required": ["understood"],
    }


def dish_image_prompt(name: str, cuisine: str | None, description: str | None) -> str:
    return (f"Appetising overhead photograph of {name}"
            + (f", {cuisine} home cooking" if cuisine else "")
            + (f". {description}" if description else "")
            + ". Served on a simple plate on a pale wooden kitchen table, soft natural daylight "
              "from a window, shallow depth of field, realistic, no text, no hands, no people.")


def step_image_prompt(name: str, step: str, cue: str | None) -> str:
    return (f"Close-up realistic photograph of a home cook's pan or dish while making {name}. "
            f"The moment shown: {step}"
            + (f" — it should look like this: {cue}." if cue else ".")
            + " Natural kitchen light, shot from slightly above, no text, no faces.")
