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
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import actions, prompts
from .actions import KITCHEN_TOOLS
from .catalog import id_list, ingredients, recipe_book
from .config import settings
from .importer import ImportErrorPublic, fetch, plain_recipe, read_page
from .recipes import RECIPE_IDEAS_SCHEMA, RECIPE_OPTIONS_SCHEMA, RECIPE_SCHEMA, clean_recipe

router = APIRouter(prefix="/api")
URL_RX = re.compile(r"https?://[^\s<>\"']+")


def svc(request: Request, name: str):
    return getattr(request.app.state, name, None)


def need(request: Request, name: str, what: str):
    s = svc(request, name)
    if not s:
        raise HTTPException(503, f"{what} is switched off on the server.")
    return s


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


class ChatIn(Known):
    messages: list[ChatMessage] = Field(max_length=60)
    context: ChatContext = Field(default_factory=ChatContext)


class GenerateIn(Known):
    brief: str = Field("", max_length=1000)
    conversation: list[ChatMessage] = Field(default_factory=list, max_length=40)
    stock: list[StockLine] = Field(default_factory=list)
    serves: int = Field(4, ge=1, le=12)
    options: bool = True


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
            raw = await llm.json(prompts.import_system(id_list(known)), material, RECIPE_SCHEMA)
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

@router.post("/recipes/generate")
async def recipes_generate(body: GenerateIn, request: Request):
    llm = need(request, "llm", "The assistant")
    known = ingredients(body.custom)
    stock = ", ".join(f"{s.id} ({s.qty:g} {s.unit or ''}"
                      + (f", {s.daysLeft} days left" if s.daysLeft is not None and s.daysLeft <= 3 else "") + ")"
                      for s in body.stock if s.id in known and s.qty is not None)
    user = body.brief or "Something good for tonight."
    if body.conversation:
        talk = "\n".join(f"{m.role}: {m.content}" for m in body.conversation[-12:])
        user = f"Turn what we agreed in this conversation into the recipe.\n\n{talk}\n\nExtra wishes: {user}"
    rag = svc(request, "rag")
    retrieved = await rag.search(user + " " + stock, 2) if rag else []
    try:
        if body.options:
            raw = await llm.json(prompts.recipe_ideas_system(id_list(known), stock, body.serves, retrieved), user, RECIPE_IDEAS_SCHEMA, 900)
        else:
            raw = await llm.json(prompts.recipe_system(id_list(known), stock, body.serves, retrieved), user, RECIPE_SCHEMA, 1800)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"The assistant didn't come back ({type(e).__name__}).") from None
    if not body.options:
        recipe = clean_recipe(raw, known, origin="assistant")
        if not recipe:
            raise HTTPException(502, "The assistant's recipe didn't hold together. Try again.")
        return {"recipe": recipe}
    ideas = []
    for item in (raw or {}).get("recipes") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        ideas.append({
            "id": f"idea_{len(ideas)}",
            "name": item["name"][:80],
            "description": str(item.get("description") or "")[:200],
            "minutes": max(1, min(600, int(item.get("minutes") or 30))),
            "complexity": item.get("complexity") if item.get("complexity") in (1, 2, 3) else 2,
            "cuisine": str(item.get("cuisine") or "Everyday")[:30],
        })
    if len(ideas) < 2:
        raise HTTPException(502, "The assistant couldn't make enough distinct ideas. Try again.")
    return {"recipes": ideas[:2]}


# ---------------------------------------------------------------- understand

@router.post("/understand")
async def understand(body: UnderstandIn, request: Request):
    llm = need(request, "llm", "The assistant")
    known = ingredients()
    cuisines = sorted({r["cuisine"] for r in recipe_book()["recipes"]})
    user = f"{body.text}\n\nValid ingredient ids: {', '.join(known)}"
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
    last = body.messages[-1].content if body.messages else ""

    async def events():
        attachment = None
        link = URL_RX.search(last)
        if link:
            yield sse({"type": "status", "text": "Reading the page…"})
            try:
                found = await import_url(request, link.group().rstrip(").,"), known)
                attachment = found["recipe"]
                yield sse({"type": "attachment", "recipe": attachment, "method": found["method"]})
            except ImportErrorPublic as e:
                yield sse({"type": "status", "text": str(e)})
            except Exception as e:  # noqa: BLE001
                yield sse({"type": "status", "text": f"Couldn't read that page ({type(e).__name__})."})

        rag = svc(request, "rag")
        retrieved = await rag.search(last + " " + (body.context.recipe or {}).get("name", "")) if rag else []
        system = prompts.chef_system(body.context.model_dump(), attachment,
                         ids=id_list(known), customs=body.custom, retrieved=retrieved)
        msgs = [m.model_dump() for m in body.messages]
        try:
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
        raise HTTPException(502, f"The picture couldn't be made ({type(e).__name__}).") from None


# ---------------------------------------------------------------- voice

@router.post("/speech/say")
async def speech_say(body: SpeakIn, request: Request):
    sp = need(request, "speech", "Server voice")
    try:
        return Response(await sp.say(body.text, body.voice),
                        media_type=getattr(sp, "mime", "audio/mpeg"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Voice failed ({type(e).__name__}).") from None


@router.post("/speech/hear")
async def speech_hear(audio: UploadFile, request: Request):
    sp = need(request, "speech", "Server listening")
    data = await audio.read()
    if not data or len(data) > 15_000_000:
        raise HTTPException(413, "That recording is empty or too long.")
    try:
        return {"text": await sp.hear(data, audio.filename or "speech.webm", audio.content_type or "audio/webm")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't make out the recording ({type(e).__name__}).") from None
