"""The endpoints added by this upgrade, kept in their own router.

Deliberately a separate file from api.py: everything here is an accelerator or
a convenience, none of it is on the path the kitchen needs to work, and keeping
it apart means api.py stays the short, readable description of the product.

    POST   /api/recipes/images          ask for a recipe's pictures (starts work)
    GET    /api/recipes/images/{id}     poll for them
    DELETE /api/recipes/images/{id}     throw them away and start again
    POST   /api/cook/prefetch           park likely answers for a step
    POST   /api/cook/quick              a parked answer, or 204
    POST   /api/receipt/read            a photo of a till receipt -> lines
    GET    /api/health/models           what is actually loaded, and why not
"""

from __future__ import annotations

import json
import os
import sys

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .catalog import ingredients
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
    if not store or not store.images:
        raise HTTPException(503, why(request, "images")
                            or "Picture generation is switched off on the server.")
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
    """Called when a cooking step comes on screen. Fire and forget."""
    pf = svc(request, "prefetch")
    if not pf:
        return {"started": False, "ready": 0}
    pf.prime(body.recipe, body.step, body.serves)
    rid = str(body.recipe.get("id") or body.recipe.get("name") or "")
    return {"started": True, "ready": pf.ready(rid, body.step)}


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


# ---------------------------------------------------------------- health

@router.get("/health/models")
async def health_models(request: Request):
    """What each model actually is, and for anything off, the reason in words.

    This is the page to open when a button is missing or a feature 502s — it
    says which package or key is absent instead of making you read the log.
    """
    s = settings()
    llm = svc(request, "llm")
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
            "on": bool(svc(request, "speech")),
            "provider": s.speech_provider,
            "hears": getattr(s, "whisper_model", None),
            "speaks": getattr(s, "tts_engine", None),
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
            "on": bool(svc(request, "rag") and svc(request, "rag").enabled),
            "ready": bool(svc(request, "rag") and svc(request, "rag").ready),
            "building": bool(svc(request, "rag") and svc(request, "rag").building),
            "error": getattr(svc(request, "rag"), "error", None),
        },
    }