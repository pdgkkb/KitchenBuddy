"""HTTP surface. Everything the browser can ask of the server.

Nothing here is load-bearing for the kitchen itself: tonight's dish,
stock, expiry, receipts and shopping run in the browser and keep working
when this server is off. This is the "extra" layer — the assistant — and
the proxy that keeps the keys out of the browser.

The chat can now *act*. When the model calls a kitchen tool, the request is
validated in `actions.normalize` and streamed to the browser as an `action`
event; the browser applies it to its own store. The server never holds the
household's data — it only relays the instruction.
"""

import json
import re
import secrets
from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import actions, prompts, template
from . import context as context_window
from .actions import KITCHEN_TOOLS
from .catalog import CUISINES, id_list, ids_budget, ingredients
from .config import settings
from .importer import ImportErrorPublic, fetch, plain_recipe, read_page
# Problems that leave a recipe impossible to cook as written, as opposed to
# untidy: these earn a third attempt (see _write_recipe).
INCOMPLETE = re.compile(r"they asked for|no step makes|not in the ingredients|is never cooked|"
                        r"simmers the .* but no water|never says how it is served|leaves out or swaps|"
                        r"nothing turns the oven on")

from .recipes import AMOUNTS_SCHEMA, add_amounts, drop_unused_zeros, ingredient_gaps
from .recipes import (COMPONENTS, RECIPE_IDEAS_SCHEMA, RECIPE_OPTIONS_SCHEMA, RECIPE_SCHEMA, _as_known_id, asked_foods, clean_recipe,
                      clean_stars, recipe_problems, time_budget)

router = APIRouter(prefix="/api")
URL_RX = re.compile(r"https?://[^\s<>\"']+")


def svc(request: Request, name: str):
    return getattr(request.app.state, name, None)


def need(request: Request, name: str, what: str):
    s = svc(request, name)
    if not s:
        raise HTTPException(503, f"{what} is switched off on the server.")
    return s


def ids_for(known: dict, keep=None, limit: int = 0) -> str:
    """The ingredient-id list, sized for the model that has to read it.

    The whole catalogue is a few hundred entries — comfortably two thousand
    tokens — pasted in before the model writes a word. On a 128K context that
    is free. On a model loaded at 4096 it is most of the window, and the answer
    has nowhere to go; the server refuses the request outright with "the number
    of tokens to keep from the initial prompt is greater than the context
    length".

    So: LLM_IDS_CHARS if it is set, otherwise a share of LLM_CONTEXT if that is
    set, otherwise everything (which is what large-context users want). What
    gets kept is in catalog.id_list — the kitchen's own ingredients first, then
    the seasonings, then as much of the rest as fits.
    """
    s = settings()
    limit = limit or int(getattr(s, "llm_ids_chars", 0) or 0)
    if not limit:
        # The window the model was actually loaded with — asked of the server
        # by app/context.py, so it no longer depends on LLM_CONTEXT being set.
        limit = ids_budget(context_window.current(s),
                           float(getattr(s, "llm_reserve", 0.35) or 0.35))
    return id_list(known, keep=keep, limit=limit)


# ---------------------------------------------------------------- models

class StockLine(BaseModel):
    id: str
    name: str | None = None
    qty: float | None = None
    unit: str | None = None
    daysLeft: int | None = None


class Known(BaseModel):
    """Ingredients the household created from receipts, on top of the catalogue."""
    custom: dict[str, dict] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class ChatContext(BaseModel):
    mode: str = "general"
    recipe: dict | None = None
    step: int | None = None
    serves: int | None = None
    stock: list[StockLine] = Field(default_factory=list)
    # What they can cook ON. Empty means "they haven't said", not "they own
    # nothing" — see prompts.chef_system, which only mentions equipment when
    # this list has something in it.
    equipment: list[str] = Field(default_factory=list, max_length=40)


class ChatIn(Known):
    messages: list[ChatMessage] = Field(max_length=60)
    context: ChatContext = Field(default_factory=ChatContext)


class GenerateIn(Known):
    brief: str = Field("", max_length=1000)
    conversation: list[ChatMessage] = Field(default_factory=list, max_length=40)
    stock: list[StockLine] = Field(default_factory=list)
    serves: int = Field(4, ge=1, le=12)
    options: bool = True
    # Set false to insist on a freshly written recipe even when the corpus has
    # one that fits. "Surprise me" wants invention; "dinner from what's in the
    # fridge" does not.
    template: bool = True
    # Minutes the dish has to fit in. Left out, it is read from the brief —
    # "give me 15 minutes", "I'm drained" — and is 20 when the brief says
    # nothing. See recipes.time_budget.
    maxMinutes: int | None = Field(None, ge=5, le=600)
    # "Any ingredients": the best dish for the request, not the best one the
    # shelves can make. Nothing is held back for being out of stock; what is
    # missing goes on the shopping list.
    anyIngredients: bool = False


class ImportIn(Known):
    url: str = Field(max_length=2000)


class UnderstandIn(BaseModel):
    text: str = Field(max_length=500)


class ImageIn(BaseModel):
    kind: str = "dish"                     # dish | step
    name: str = Field(max_length=120)
    cuisine: str | None = None
    description: str | None = Field(None, max_length=300)
    step: str | None = Field(None, max_length=300)
    cue: str | None = Field(None, max_length=120)


class SpeakIn(BaseModel):
    text: str = Field(max_length=2000)
    voice: str | None = None


# ---------------------------------------------------------------- status

@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    llm = svc(request, "llm")
    return {
        "chat": bool(llm), "recipes": bool(llm),
        "images": bool(svc(request, "images")),
        "voice": bool(svc(request, "speech")),
        "actions": bool(llm),
        "model": llm.name if llm else None,
        "local": bool(llm and "(local)" in llm.name),
        "wakeWord": settings().wake_word or None,
    }


# ---------------------------------------------------------------- import

async def import_url(request: Request, url: str, known: dict) -> dict:
    page = read_page(await fetch(url), url)
    llm = svc(request, "llm")
    source = {"url": url, "site": page["site"]}

    if llm:
        material = (json.dumps({k: page[k] for k in ("name", "minutes", "serves", "cuisine", "ingredients", "steps")
                                if page.get(k)}, ensure_ascii=False)
                    if page["kind"] == "structured" else page["text"])
        try:
            await context_window.refresh(settings())
            raw = await llm.json(prompts.import_system(ids_for(known)), material, RECIPE_SCHEMA)
            recipe = clean_recipe(raw, known, origin="link")
            if recipe:
                return {"recipe": {**recipe, "source": source, "remotePhoto": page.get("image")},
                        "method": "assistant"}
        except Exception as e:  # noqa: BLE001 — fall through to the plain path
            print("import via model failed:", e)

    if page["kind"] != "structured":
        raise ImportErrorPublic("This page has no recipe the app can read on its own. "
                                "Turn the assistant on and it will read the page for you.")
    recipe = clean_recipe(plain_recipe(page, known), known, origin="link")
    if not recipe:
        raise ImportErrorPublic("The recipe on that page couldn't be read.")
    return {"recipe": {**recipe, "source": source, "remotePhoto": page.get("image")}, "method": "plain"}


@router.post("/recipes/import")
async def recipes_import(body: ImportIn, request: Request):
    try:
        return await import_url(request, body.url.strip(), ingredients(body.custom))
    except ImportErrorPublic as e:
        raise HTTPException(422, str(e)) from None


# ---------------------------------------------------------------- generate

# The stages the browser narrates. They are the real boundaries of the work
# below and nothing else — the old interface guessed at four phases on a
# stopwatch, which is why it sat on "Checking the ingredients" for twenty
# seconds while the model was in fact still writing the method, and why that
# check appeared to happen AFTER reading the kitchen when it is the last thing
# that happens, not the first. If a stage here stops being a real step in this
# function, delete it here too rather than leaving it to decorate the wait.
#
# "template" only happens when the corpus turned out to have a recipe this
# kitchen can already cook, so the browser must treat it as optional rather
# than as a step that is coming.
STAGES_IDEAS = ["kitchen", "corpus", "ideas"]
STAGES_METHOD = ["kitchen", "corpus", "template", "method", "check"]


async def _write_recipe(body: GenerateIn, request: Request):
    """The work, as an async generator of ("stage", key) then ("done", payload).

    One implementation, two doors: the plain JSON endpoint drains it and returns
    the payload, the SSE endpoint relays each stage as it is reached. Keeping it
    in one place is the point — a progress report that drifts from the work is
    worse than no progress report.
    """
    llm = need(request, "llm", "The assistant")
    s = settings()
    await context_window.refresh(s)

    yield "stage", "kitchen"
    known = ingredients(body.custom)
    # Whatever else gets trimmed out of the id list to fit the context window,
    # the things actually on their shelves stay in. A recipe prompt that omits
    # the kitchen is worse than no recipe prompt.
    keep_ids = [s_.id for s_ in body.stock if s_.id in known]
    stock = ", ".join(f"{s_.id} ({s_.qty:g} {s_.unit or ''}"
                      + (f", {s_.daysLeft} days left" if s_.daysLeft is not None and s_.daysLeft <= 3 else "") + ")"
                      for s_ in body.stock if s_.id in known and s_.qty is not None)
    user = body.brief or "Something good for tonight."
    asked = " ".join([body.brief] + [m.content for m in body.conversation if m.role == "user"])
    in_kitchen = {s_.id for s_ in body.stock if s_.id in known and (s_.qty is None or s_.qty > 0)}
    free = body.anyIngredients

    yield "stage", "corpus"
    rag = svc(request, "rag")
    stock_names = [str(known[s_.id].get("name", s_.id)) for s_ in body.stock if s_.id in known]

    # ---- Did they name a dish? ------------------------------------------------
    # "Make mochi" is searched on "mochi", by title — not on the request plus
    # the whole fridge, which once handed the model a bruschetta as its
    # reference for mochi. See rag.find_dish. A named dish gets its real
    # method in the prompt, the time it really takes, and whatever it needs
    # that the kitchen hasn't got goes on the shopping list.
    dish = await rag.find_dish(asked, stock_names) if rag else None
    if dish:
        print(f"recipes: they asked for {dish['dish']!r} — following {dish['title']!r}")
    # The foods they named must be in it — see recipes.asked_foods. Said to the
    # model up front, and checked afterwards like everything else.
    wanted = asked_foods(asked, known)
    # A sauce or component they named, that a small model may not know how to
    # make — "bechamel" came back as a creamy tomato sauce, twice, even when
    # told. It gets a real recipe for that part. One, to keep the prompt short.
    part = None
    if rag and not dish:
        for w in wanted:
            if w in COMPONENTS or w.rstrip("s") in COMPONENTS:
                part = await rag.find_titled(w, stock_names)
                if part:
                    print(f"recipes: they asked for {w!r} — showing how {part['title']!r} is made")
                    break
    # A dinner built around a sauce they named takes longer than "something for
    # tonight": half an hour by default, not twenty minutes and four steps.
    budget = time_budget(asked, body.maxMinutes, named_dish=bool(dish), default=30 if part else None)
    if wanted:
        user += "\n\nThey asked for: " + ", ".join(wanted) + ". The recipe must have every one of them."
        # Named on purpose: something the shelves haven't got is bought, not dropped.
        on_shelf = " ".join(stock_names).lower()
        if any(w.rstrip("s") not in on_shelf for w in wanted if w not in ("meat", "fish", "vegetables", "veg", "veggie", "veggies", "greens")):
            in_kitchen = set()
    if budget:
        user += f"\n\nIt has to be on the table in {budget} minutes."
    if body.conversation:
        talk = "\n".join(f"{m.role}: {m.content}" for m in body.conversation[-12:])
        user = f"Turn what we agreed in this conversation into the recipe.\n\n{talk}\n\nExtra wishes: {user}"
    if free:
        in_kitchen = set()          # they asked not to be held to the shelves
    if dish:
        in_kitchen = set()          # nothing-to-buy is a quick-dinner rule, not a mochi rule
        # The real recipe's ingredients that the catalogue has must be in the
        # id list the model sees, or it maps pecorino onto parmesan because
        # pecorino was trimmed out to fit the context window.
        for line in str(dish.get("ingredients") or "").split("\n"):
            iid = _as_known_id(template._food(line).replace("-", " "), known)
            if iid and iid not in keep_ids:
                keep_ids.append(iid)

    async def complete(recipe):
        """Fill the ingredient list's holes with one short question (recipes.add_amounts)."""
        if recipe:
            drop_unused_zeros(recipe, known)
        gaps = ingredient_gaps(recipe, known, keep_ids) if recipe else []
        if not gaps:
            return recipe
        try:
            got = await llm.json(prompts.AMOUNTS, prompts.amounts_user(recipe, gaps, known), AMOUNTS_SCHEMA, 300)
            answers = {str(a.get("id")): a.get("qty") for a in (got or {}).get("amounts") or [] if isinstance(a, dict)}
            added = add_amounts(recipe, {g: answers[g] for g in gaps if g in answers}, known)
            drop_unused_zeros(recipe, known)
            if added:
                print("recipes: completed the ingredient list — " + "; ".join(added))
        except Exception as e:  # noqa: BLE001 — the checks still say what is missing
            print(f"recipes: couldn't fill the missing amounts ({type(e).__name__}: {str(e)[:120]})")
        return recipe

    # ---- Is there already a recipe for this kitchen? -----------------------
    #
    # Writing a dish from nothing is the most expensive thing a local model
    # does here, and it is usually unnecessary: the corpus has hundreds of
    # thousands of recipes people actually cooked. If one of them uses what is
    # already on these shelves, the job becomes CONVERSION — map to ids, scale
    # to the table, split the steps — which is mechanical, far quicker, and
    # invents nothing. See app/template.py.
    #
    # Only for the full method. The two-idea pass is already cheap, and ideas
    # are the one place invention is the point.
    #
    # Not for a quick dinner, though. Measured with gemma-4-e2b and a demo
    # fridge, a "15 minutes" request spent 48 s converting a corpus recipe that
    # then failed the time check every time, before writing one in 20-25 s.
    # Corpus methods are rarely that short, and a tired person waits for both.
    candidate = None
    quick = budget is not None and budget <= 30
    if not body.options and body.template and not quick and not dish and not free and getattr(s, "template_enabled", True):
        candidate = await template.pick(
            rag, f"{user} {' '.join(stock_names)}", stock_names,
            floor=float(getattr(s, "template_floor", 0.72) or 0.72),
            candidates=int(getattr(s, "template_candidates", 8) or 8),
            max_minutes=budget,
        )

    if candidate:
        yield "stage", "template"
        got = template.cached(candidate["title"], body.serves)
        if got:
            # A fresh id every time, or saving the same dinner twice collides
            # with the copy already in the book.
            yield "done", {"recipe": {**got, "id": f"tpl_{secrets.token_hex(4)}"}}
            return
        try:
            raw = await llm.json(template.system(ids_for(known, keep_ids), stock, body.serves),
                                 template.user(candidate, body.brief, body.serves),
                                 RECIPE_SCHEMA, 1400)
            yield "stage", "check"
            if isinstance(raw, dict):
                raw["serves"] = body.serves      # scaled for the table asked for, whatever it says
            recipe = await complete(clean_recipe(raw, known, origin="template"))
            problems = recipe_problems(recipe, budget, in_kitchen, known=known, asked=wanted) if recipe else ["nothing usable"]
            if not problems:
                recipe["basedOn"] = candidate["title"]
                template.remember(candidate["title"], body.serves, recipe)
                yield "done", {"recipe": recipe}
                return
            print(f"template: {candidate['title']!r} converted into something that "
                  f"didn't hold together ({'; '.join(problems)}) — writing one instead")
        except Exception as e:  # noqa: BLE001
            # Never fatal. A conversion that fails just means we write one, which
            # is what this endpoint did before templates existed.
            print(f"template: converting {candidate['title']!r} failed "
                  f"({type(e).__name__}: {str(e)[:160]}) — writing one instead")

    # One reference recipe, not two, and none at all when a template was tried:
    # every one of these is prompt the model must read before it writes anything,
    # and on a local 9B reading is the expensive part — see RAG_CHARS in
    # prompts.py.
    # The search is on what they asked for and the NAMES of what's on the
    # shelves — not the prompt's own sentences ("on the table in 20 minutes")
    # or ingredient codes like "oliveoil", which outrank any real word.
    retrieved = (await rag.search(asked if free else asked + " " + " ".join(stock_names), 1)
                 if (rag and not candidate and not dish and not part) else [])

    yield "stage", "ideas" if body.options else "method"
    try:
        if body.options:
            raw = await llm.json(prompts.recipe_ideas_system(ids_for(known, keep_ids), stock, body.serves, retrieved, budget, dish, free), user, RECIPE_IDEAS_SCHEMA, 900)
        else:
            raw = await llm.json(prompts.recipe_system(ids_for(known, keep_ids), stock, body.serves, retrieved, budget, dish, part, free), user, RECIPE_SCHEMA, 1800)
    except Exception as e:  # noqa: BLE001
        # The server's own sentence, not just the exception's class name. A
        # local server that refuses a field answers 400 with a line naming it,
        # and that line is the entire diagnosis — "BadRequestError" on its own
        # sent us hunting for a timeout that wasn't there.
        raise HTTPException(502, f"The assistant didn't come back ({type(e).__name__}): "
                                 f"{str(e)[:700]}") from None

    if not body.options:
        yield "stage", "check"
        # The prompt said who is eating; the model's "serves": 4 is the schema's
        # habit, not a decision, and the app scales every amount from it.
        if isinstance(raw, dict):
            raw["serves"] = body.serves
        recipe = await complete(clean_recipe(raw, known, origin="assistant"))
        problems = (recipe_problems(recipe, budget, in_kitchen, dish, known, wanted)
                    if recipe else ["it didn't hold together"])

        # ONE second go, told exactly what was wrong. A small model that wrote
        # four hours of boiled courgettes and a hummus nobody bought fixes most
        # of it when handed the list; asked blind, it rolls the same dice again.
        # Never a third: each go is a full recipe's wait at the stove.
        if problems:
            print("recipes: sending it back — " + "; ".join(problems))
            yield "stage", "method"
            try:
                again = await llm.json(
                    prompts.recipe_system(ids_for(known, keep_ids), stock, body.serves, retrieved, budget, dish, part, free),
                    user + "\n\nYour last attempt was rejected because " + "; ".join(problems)
                    + ". Write it again with every one of those fixed.",
                    RECIPE_SCHEMA, 1800)
                yield "stage", "check"
                if isinstance(again, dict):
                    again["serves"] = body.serves
                second = await complete(clean_recipe(again, known, origin="assistant"))
                if second:
                    left = recipe_problems(second, budget, in_kitchen, dish, known, wanted)
                    if not recipe or len(left) <= len(problems):
                        recipe, problems = second, left
            except Exception as e:  # noqa: BLE001 — the first recipe still stands
                print(f"recipes: second go failed ({type(e).__name__}: {str(e)[:160]})")

        # Still incomplete after being told once: a food they asked for is
        # missing, something is used that nobody listed or made, the grain is
        # raw, or it never says how to serve it. That is a recipe nobody can
        # cook, so it gets ONE more go. When a corpus reference was followed,
        # this go is written without it: "Mom's Dessert" pulled a request for
        # flour, cornstarch and sugar towards instant pudding twice in a row.
        if recipe and problems and any(INCOMPLETE.search(p_) for p_ in problems):
            print("recipes: still incomplete, one last go" + (" without the reference" if (dish or part) else "")
                  + " — " + "; ".join(problems))
            yield "stage", "method"
            try:
                last = await llm.json(
                    prompts.recipe_system(ids_for(known, keep_ids), stock, body.serves,
                                          [] if (dish or part) else retrieved, budget, None, None, free),
                    user + "\n\nTwo attempts were rejected. What is still wrong: " + "; ".join(problems)
                    + ". Write a COMPLETE recipe: every ingredient any step uses is in the ingredients, "
                      "every crust, dough, sauce or filling is made in its own step before it is used, "
                      "and the last step says how it is served.",
                    RECIPE_SCHEMA, 1800)
                yield "stage", "check"
                if isinstance(last, dict):
                    last["serves"] = body.serves
                third = await complete(clean_recipe(last, known, origin="assistant"))
                if third:
                    left = recipe_problems(third, budget, in_kitchen, None, known, wanted)
                    worse = lambda ps: (sum(bool(INCOMPLETE.search(x)) for x in ps), len(ps))
                    if worse(left) < worse(problems):
                        recipe, problems = third, left
                        dish = part = None                     # it no longer follows the reference
            except Exception as e:  # noqa: BLE001 — the better of the first two still stands
                print(f"recipes: last go failed ({type(e).__name__}: {str(e)[:160]})")

        if not recipe:
            raise HTTPException(502, "The assistant's recipe didn't hold together. Try again.")
        if problems:
            # Still wrong after being told. Handed over, but not quietly.
            recipe["problems"] = problems
        if budget:
            recipe["budget"] = budget
        if dish:
            recipe["basedOn"] = dish["title"]
        yield "done", {"recipe": recipe}
        return

    ideas = []
    for item in (raw or {}).get("recipes") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        ideas.append({
            "id": f"idea_{len(ideas)}",
            "name": item["name"][:80],
            "description": str(item.get("description") or "")[:200],
            "minutes": max(1, min(600, int(item.get("minutes") or 30))),
            **dict(zip(("stars", "complexity"), clean_stars(item))),
            "cuisine": str(item.get("cuisine") or "Everyday")[:30],
        })
    if len(ideas) < 2:
        raise HTTPException(502, "The assistant couldn't make enough distinct ideas. Try again.")
    yield "done", {"recipes": ideas[:2]}


@router.post("/recipes/generate")
async def recipes_generate(body: GenerateIn, request: Request):
    """The plain answer, unchanged in shape — anything that isn't the new
    browser still gets exactly what it got before."""
    async for kind, payload in _write_recipe(body, request):
        if kind == "done":
            return payload
    raise HTTPException(502, "The assistant stopped without an answer.")


@router.post("/recipes/generate/stream")
async def recipes_generate_stream(body: GenerateIn, request: Request):
    """The same work, narrated.

    Events: {"type":"stage","key":...} as each real boundary is crossed, then
    one {"type":"result", ...} or {"type":"error","message":...}. The error has
    to travel in the body rather than as a status code: by the time the model
    fails, the response headers are long gone.
    """
    async def events():
        pf = svc(request, "prefetch")
        try:
            async with (pf.foreground() if pf else nullcontext()):
                async for kind, payload in _write_recipe(body, request):
                    if await request.is_disconnected():
                        return
                    if kind == "stage":
                        yield sse({"type": "stage", "key": payload})
                    else:
                        yield sse({"type": "result", **payload})
        except HTTPException as e:
            yield sse({"type": "error", "message": str(e.detail)})
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": f"The assistant stopped ({type(e).__name__})."})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- understand

@router.post("/understand")
async def understand(body: UnderstandIn, request: Request):
    llm = need(request, "llm", "The assistant")
    known = ingredients()
    cuisines = CUISINES
    await context_window.refresh(settings())
    user = f"{body.text}\n\n{ids_for(known)}"
    try:
        out = await llm.json(prompts.UNDERSTAND, user, prompts.understand_schema(cuisines), 400)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "The assistant didn't come back.") from None
    f: dict[str, Any] = {}
    if out.get("mealType") in ("breakfast", "lunch", "dinner", "snack"):
        f["mealType"] = out["mealType"]
    if isinstance(out.get("maxMinutes"), int) and 0 < out["maxMinutes"] <= 600:
        f["maxMinutes"] = out["maxMinutes"]
    if out.get("maxComplexity") in (1, 2, 3):
        f["maxComplexity"] = out["maxComplexity"]
    if out.get("cuisine") in cuisines:
        f["cuisine"] = out["cuisine"]
    if out.get("mustUse") in known:
        f["mustUse"] = out["mustUse"]
    avoid = [i for i in out.get("avoid") or [] if i in known]
    return {"filters": f, "avoid": avoid, "understood": [str(out.get("understood", ""))[:120]]}


# ---------------------------------------------------------------- chat

def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(body: ChatIn, request: Request):
    llm = need(request, "llm", "The assistant")
    known = ingredients(body.custom)
    await context_window.refresh(settings())
    last = body.messages[-1].content if body.messages else ""
    cooking = body.context.mode == "cooking"

    # What must survive the id-list trim here: the kitchen, and — while they are
    # cooking — the dish's own ingredients, so a substitution question can still
    # name what is in the pan. prompts.chef_system narrows it further in cooking
    # mode; this makes sure the narrowing has the right things to narrow to.
    recipe_ctx = body.context.recipe or {}
    chat_keep = [s_.id for s_ in body.context.stock if s_.id in known]
    chat_keep += [n.get("id") for n in (recipe_ctx.get("needs") or []) if isinstance(n, dict)]
    chat_keep += [n.get("id") for n in (recipe_ctx.get("seasoning") or []) if isinstance(n, dict)]

    async def events():
        attachment = None
        link = URL_RX.search(last)
        if link:
            yield sse({"type": "status", "text": "Reading the page…"})
            try:
                found = await import_url(request, link.group().rstrip(")., "), known)
                attachment = found["recipe"]
                yield sse({"type": "attachment", "recipe": attachment, "method": found["method"]})
            except ImportErrorPublic as e:
                yield sse({"type": "status", "text": str(e)})
            except Exception as e:  # noqa: BLE001
                yield sse({"type": "status", "text": f"Couldn't read that page ({type(e).__name__})."})

        # Cooking mode on a model that doesn't use tools: the short hob prompt,
        # streamed, so the first sentence is spoken while the rest is written.
        # Background prefetching steps aside for the length of the answer.
        s = settings()
        pf = svc(request, "prefetch")
        if cooking and not getattr(s, "cook_tools", False):
            ctx = body.context.model_dump()
            system = prompts.cook_system(ctx, known)
            # "What does opaque mean?" — the plain words go in before it answers,
            # or it hands the cue straight back (see prompts.term_note).
            system += prompts.term_note(last, ctx, [m.model_dump() for m in body.messages])
            msgs = [m.model_dump() for m in body.messages][-8:]
            try:
                async with (pf.foreground() if pf else nullcontext()):
                    async for text in llm.stream(system, msgs,
                                                 max_tokens=int(getattr(s, "cook_answer_tokens", 220) or 220)):
                        if await request.is_disconnected():
                            return
                        yield sse({"type": "delta", "text": text})
            except Exception as e:  # noqa: BLE001
                yield sse({"type": "error", "message": f"The assistant stopped ({type(e).__name__})."})
                return
            yield sse({"type": "done"})
            return

        # No corpus lookup while they're at the hob. They are cooking THIS dish:
        # four other people's recipes are a SQLite query, a few hundred more
        # tokens for the model to read before every single answer, and nothing
        # the answer can use. Every question in cooking mode paid for that.
        rag = None if cooking else svc(request, "rag")
        # "How do I make mochi?" gets a real mochi recipe, method and all; any
        # other question gets the usual couple of references.
        shelf = [str((known.get(s_.id) or {}).get("name", s_.id)) for s_ in body.context.stock]
        dish = await rag.find_dish(last, shelf) if rag else None
        retrieved = [] if dish else (await rag.search(last + " " + (body.context.recipe or {}).get("name", "")) if rag else [])

        system = prompts.chef_system(body.context.model_dump(), attachment,
                                     ids=ids_for(known, chat_keep, int(getattr(s, "llm_chat_ids_chars", 0) or 0)),
                                     customs=body.custom, retrieved=retrieved, dish=dish)
        msgs = [m.model_dump() for m in body.messages]
        try:
            async with (pf.foreground() if pf else nullcontext()):
                if hasattr(llm, "chat_actions"):
                    async for ev in llm.chat_actions(system, msgs, KITCHEN_TOOLS, actions.normalize, known):
                        if await request.is_disconnected():
                            return
                        yield sse(ev)
                else:
                    async for text in llm.stream(system, msgs):
                        if await request.is_disconnected():
                            return
                        yield sse({"type": "delta", "text": text})
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": f"The assistant stopped ({type(e).__name__})."})
            return
        yield sse({"type": "done"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- pictures

@router.post("/images")
async def images(body: ImageIn, request: Request):
    img = need(request, "images", "Picture generation")
    prompt = (prompts.step_image_prompt(body.name, body.step, body.cue) if body.kind == "step" and body.step
              else prompts.dish_image_prompt(body.name, body.cuisine, body.description))
    try:
        return {"url": await img.create(prompt)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _blame("Making the picture", e)) from None


# ---------------------------------------------------------------- voice


def _blame(what: str, e: BaseException) -> str:
    """Print the whole trace; hand back one line the browser can show.

    `HTTPException(502, f"Voice failed ({type(e).__name__}).")` was a mistake.
    The class name is the least informative part of an exception — `RuntimeError`
    tells you nothing, and `from None` deleted the context that did. The trace
    belongs in the terminal where the server is running, and the message
    belongs in the response, where it shows up in the browser's network tab.
    """
    import traceback
    print(f"\n--- {what} failed -------------------------------------", flush=True)
    traceback.print_exc()
    print("--- end -----------------------------------------------\n", flush=True)
    return f"{what} failed — {type(e).__name__}: {str(e)[:300]}"


@router.post("/speech/say")
async def speech_say(body: SpeakIn, request: Request):
    sp = need(request, "speech", "Server voice")
    try:
        return Response(await sp.say(body.text, body.voice),
                        media_type=getattr(sp, "mime", "audio/mpeg"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _blame("Speaking", e)) from None


@router.post("/speech/hear")
async def speech_hear(audio: UploadFile, request: Request):
    sp = need(request, "speech", "Server listening")
    data = await audio.read()
    if not data or len(data) > 15_000_000:
        raise HTTPException(413, "That recording is empty or too long.")
    try:
        return {"text": await sp.hear(data, audio.filename or "speech.webm", audio.content_type or "audio/webm")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, _blame("Hearing", e)) from None