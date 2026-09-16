"""The endpoints added by this upgrade, kept in their own router.

Deliberately a separate file from api.py: everything here is an accelerator or
a convenience, none of it is on the path the kitchen needs to work, and keeping
it apart means api.py stays the short, readable description of the product.

    POST   /api/recipes/images          ask for a recipe's pictures (starts work)
    GET    /api/recipes/images/{id}     poll for them
    DELETE /api/recipes/images/{id}     throw them away and start again
    POST   /api/cook/prefetch           park likely answers for a step
    POST   /api/cook/quick              a parked answer, or 204
    POST   /api/recipes/adapt           cook this with the equipment you own
    POST   /api/receipt/read            a photo of a till receipt -> lines
    GET    /api/health/models           what is actually loaded, and why not
"""

from __future__ import annotations

import json
import os
import sys

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import context as context_window
from . import prompts, warmup
from .adapt import ADAPT_SCHEMA, EQUIPMENT, clean_adaptation, missing_for
from .catalog import id_list, ids_budget, ingredients
from .config import settings
from .receipt import ReceiptError, ocr_available
from .receipt import read as read_receipt

router = APIRouter(prefix="/api")

MAX_UPLOAD = 12_000_000          # a phone photo is 2-5 MB; 12 is generous


def svc(request: Request, name: str):
    return getattr(request.app.state, name, None)


def why(request: Request, name: str) -> str | None:
    return getattr(request.app.state, f"{name}_why", None)


# ---------------------------------------------------------------- pictures

class RecipeRef(BaseModel):
    id: str = Field(max_length=80)
    name: str = Field("", max_length=120)
    cuisine: str | None = Field(None, max_length=60)
    description: str | None = Field(None, max_length=400)
    count: int | None = Field(None, ge=1, le=6)


@router.post("/recipes/images")
async def recipe_images(body: RecipeRef, request: Request):
    """Everything we already have for this dish, and work started on the rest.

    Returns at once — generation happens on a background worker. The browser
    polls the GET below, so a slow picture never holds up a screen.
    """
    store = svc(request, "recipe_images")
    if not store:
        raise HTTPException(503, "The picture store isn't running on the server.")
    # No longer a 503 when the picture model is off: it is written down instead,
    # and `backend/tools/make_photos.py` paints it when you run it. The reply
    # carries "queued": true so the browser can say so rather than showing an
    # error for something that will simply arrive later.
    return store.ensure(body.model_dump(), body.count)


@router.get("/recipes/images/{recipe_id}")
async def recipe_images_get(recipe_id: str, request: Request):
    store = svc(request, "recipe_images")
    if not store:
        return {"urls": [], "pending": False, "want": 0}
    return store.get(recipe_id)


@router.delete("/recipes/images/{recipe_id}")
async def recipe_images_clear(recipe_id: str, request: Request):
    store = svc(request, "recipe_images")
    if not store:
        return {"removed": 0}
    return {"removed": store.forget(recipe_id)}


# ---------------------------------------------------------------- prefetch

class PrefetchIn(BaseModel):
    recipe: dict
    step: int = Field(0, ge=0, le=200)
    serves: int | None = Field(None, ge=1, le=20)


class QuickIn(BaseModel):
    recipeId: str = Field(max_length=80)
    step: int = Field(0, ge=0, le=200)
    question: str = Field(max_length=400)


@router.post("/cook/prefetch")
async def cook_prefetch(body: PrefetchIn, request: Request):
    """Called when a cooking step comes on screen. Fire and forget.

    `ensure`, not `prime`. `prime` filled this step AND the next one, which
    doubled the model work behind a browser that now asks for each step
    explicitly — so a single "next" started four background jobs on a machine
    that was also trying to answer a question at the hob. The caller decides
    what to park; this endpoint parks exactly that.
    """
    pf = svc(request, "prefetch")
    if not pf:
        return {"started": False, "ready": 0, "why": "Answer prefetching is off on the server."}
    started = pf.ensure(body.recipe, body.step, body.serves)
    rid = str(body.recipe.get("id") or body.recipe.get("name") or "")
    return {"started": bool(started), "ready": pf.ready(rid, body.step)}


@router.post("/cook/quick")
async def cook_quick(body: QuickIn, request: Request):
    """An answer we already wrote, or 204 so the browser asks the model."""
    pf = svc(request, "prefetch")
    hit = pf.quick(body.recipeId, body.step, body.question) if pf else None
    if not hit:
        return Response(status_code=204)
    return hit


# ---------------------------------------------------------------- receipt

@router.post("/receipt/read")
async def receipt_read(request: Request,
                       photo: UploadFile = File(...),
                       custom: str = Form("{}")):
    """A photograph of a till receipt -> the same shape the sample receipt has,
    so it drops straight into the screen that already exists for correcting it."""
    data = await photo.read()
    if not data:
        raise HTTPException(400, "That photo arrived empty.")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "That photo is too big — take it again at a "
                                 "normal size, or crop it to the receipt.")
    try:
        extra = json.loads(custom or "{}")
        extra = extra if isinstance(extra, dict) else {}
    except ValueError:
        extra = {}

    s = settings()
    try:
        return await read_receipt(data, svc(request, "llm"), ingredients(extra),
                                  getattr(s, "ocr_languages", "fra+eng"))
    except ReceiptError as e:
        raise HTTPException(422, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"The receipt couldn't be read "
                                 f"({type(e).__name__}: {e}).") from None


# ---------------------------------------------------------------- equipment

class AdaptIn(BaseModel):
    recipe: dict
    equipment: list[str] = Field(default_factory=list, max_length=40)
    serves: int | None = Field(None, ge=1, le=20)
    question: str = Field("", max_length=400)
    custom: dict[str, dict] = Field(default_factory=dict)


@router.post("/recipes/adapt")
async def recipe_adapt(body: AdaptIn, request: Request):
    """Can this dish be cooked with the equipment this kitchen has?

    Returns a structured answer the recipe panel draws, NOT a rewritten recipe.
    The author's version stays the authority; this sits beside it. "No" is a
    first-class answer — see `adapt.clean_adaptation`, which rejects a "yes"
    that changes no steps, because that is the model agreeing with the question
    rather than answering it.
    """
    llm = svc(request, "llm")
    if not llm:
        raise HTTPException(503, why(request, "llm")
                            or "The assistant is switched off on the server.")

    owned = [e for e in dict.fromkeys(body.equipment) if e in EQUIPMENT]
    if not owned:
        raise HTTPException(422, "Tell me what you cook with first — I can't work "
                                 "round an empty list.")

    recipe = body.recipe
    if not isinstance(recipe.get("steps"), list) or not recipe["steps"]:
        raise HTTPException(422, "That recipe has no steps to adapt.")

    missing = missing_for(recipe, owned)
    known = ingredients(body.custom)
    s = settings()
    # Sized to the loaded context window, like every other prompt (api.ids_for).
    budget = int(getattr(s, "llm_ids_chars", 0) or 0) or ids_budget(
        await context_window.refresh(s), float(getattr(s, "llm_reserve", 0.35) or 0.35))
    system = prompts.adapt_system(owned, missing, body.serves or recipe.get("serves") or 4,
                                  id_list(known, limit=budget,
                                          keep=[n.get("id") for part in ("needs", "seasoning")
                                                for n in (recipe.get(part) or []) if isinstance(n, dict)]))
    ask = body.question.strip() or (
        f"Can I cook this without {', '.join(missing)}?" if missing
        else "Is there another way to cook this with what I have?")
    try:
        raw = await llm.json(system, prompts.adapt_user(recipe, ask), ADAPT_SCHEMA, 1600)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"The chef didn't come back ({type(e).__name__}): "
                                 f"{str(e)[:300]}") from None

    adaptation = clean_adaptation(raw, recipe, owned)
    if not adaptation:
        raise HTTPException(502, "The chef's answer didn't hold together — it said yes "
                                 "without changing anything, or named steps this recipe "
                                 "hasn't got. Ask again.")
    return {"adaptation": adaptation, "missing": missing}


# ---------------------------------------------------------------- health

@router.get("/health/models")
async def health_models(request: Request):
    """What each model actually is, and for anything off, the reason in words.

    This is the page to open when a button is missing or a feature 502s — it
    says which package or key is absent instead of making you read the log.
    """
    s = settings()
    llm = svc(request, "llm")
    speech = svc(request, "speech")
    rag = svc(request, "rag")
    venv = os.environ.get("VIRTUAL_ENV")
    inside = bool(venv and sys.executable.startswith(venv))
    return {
        # Which Python is ACTUALLY running this server. Worth reporting, because
        # the most confusing failure in this whole stack is installing a package
        # into one interpreter and running the server under another: every
        # feature reports "package missing" while `import` works fine in your
        # shell. `uvicorn` on PATH is often a different Python from the venv's —
        # `python -m uvicorn` is the form that can't get this wrong.
        "runtime": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "virtualenv": venv,
            "runningInsideVenv": inside,
            "warning": None if inside or not venv else (
                f"A virtualenv is active ({venv}) but this server is running under "
                f"{sys.executable}. Packages installed into the venv are invisible to it. "
                "Stop the server and start it with:  python -m uvicorn app.main:app --reload --port 8000"
            ),
        },
        "chat": {
            "on": bool(llm),
            "name": getattr(llm, "name", None),
            "provider": s.llm_provider,
            "model": s.llm_model,
            "baseUrl": s.openai_base_url or None,
            "thinking": bool(getattr(s, "llm_think", False)),
            # Configured is not the same as working. A local server will happily
            # accept a model name it cannot load; this is what the start-up ping
            # actually found, and its own words for why not.
            "reachable": warmup.LLM_STATUS.get("reachable"),
            "reachableWhy": warmup.LLM_STATUS.get("why"),
            "why": why(request, "llm"),
        },
        "pictures": {
            "on": bool(svc(request, "images")),
            "provider": s.image_provider,
            "model": s.image_model,
            "perRecipe": getattr(s, "image_count", 3),
            "why": why(request, "images"),
        },
        "voice": {
            "on": bool(speech),
            "provider": s.speech_provider,
            "hears": getattr(s, "stt_engine", None) or getattr(s, "whisper_model", None),
            "speaks": getattr(s, "tts_engine", None),
            # Which engine actually made a sound, as opposed to which one is
            # configured. Speaking is a fallback chain (see speech_local.py):
            # asking for Kokoro and getting the system voice is a normal,
            # successful outcome, and this is the only place that says so.
            "speaksNow": (getattr(speech, "_tts_impl", None) or (None, None))[0],
            "why": why(request, "speech"),
        },
        "receipts": {
            "on": ocr_available() is None and bool(llm),
            "why": ocr_available() or (None if llm else "The assistant is off, so "
                                       "receipt lines can't be matched."),
        },
        "prefetch": {
            "on": bool(svc(request, "prefetch")),
            "perStep": getattr(s, "prefetch_count", 4),
        },
        "rag": {
            "on": bool(rag and rag.enabled),
            "ready": bool(rag and rag.ready),
            "building": bool(rag and rag.building),
            "error": getattr(rag, "error", None),
        },
        "templates": {
            "on": bool(getattr(s, "template_enabled", True)),
            "floor": getattr(s, "template_floor", 0.72),
            "candidates": getattr(s, "template_candidates", 8),
            "why": None if (rag and rag.ready) else
                   "Templates need the corpus index; it isn't ready yet.",
        },
    }