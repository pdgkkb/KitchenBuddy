"""Happy Bite — server.

    uvicorn app.main:app --reload            (from backend/)

In development the React app runs on Vite (port 5173) and proxies /api
here. In production `npm run build` writes frontend/dist, and this one
process serves the app, the API and generated pictures — one address to
type into the kitchen tablet.

Start-up now does three extra things, all of them optional:

  * it CHECKS each provider's packages instead of assuming them, so a missing
    `diffusers` turns pictures off honestly and says so, rather than showing a
    button that 502s;
  * it builds the recipe picture store and the answer prefetcher;
  * it warms the models in the background, so the first thing you say isn't
    paying for Whisper, Qwen and Kokoro to load.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import warmup
from .api import router
from .api_extra import router as extra_router
from .config import ROOT, settings
from .imagestore import RecipeImages
from .prefetch import Prefetcher
from .providers.llm import make_llm
from .providers.media import make_images_checked, make_speech_checked
from .rag import RecipeRetriever


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    app.state.llm = make_llm(s)
    app.state.llm_why = None if app.state.llm else (
        "No chat model. Set LLM_PROVIDER and a key (or OPENAI_BASE_URL for a "
        "local Ollama) in backend/.env.")

    app.state.images, app.state.images_why = make_images_checked(s)
    app.state.speech, app.state.speech_why = make_speech_checked(s)

    app.state.recipe_images = RecipeImages(app.state.images, s.media_path,
                                           getattr(s, "image_count", 3))
    app.state.prefetch = Prefetcher(app.state.llm,
                                    getattr(s, "prefetch_count", 4),
                                    getattr(s, "prefetch_enabled", True))
    app.state.rag = RecipeRetriever(s.rag_source_path, s.rag_index_path,
                                    s.rag_enabled, s.rag_top_k)
    await app.state.rag.start()

    print("Happy Bite server —",
          f"chat: {app.state.llm.name if app.state.llm else 'off'},",
          f"pictures: {'on' if app.state.images else 'off'},",
          f"voice: {'server' if app.state.speech else 'browser'}")
    for label in ("images", "speech", "llm"):
        reason = getattr(app.state, f"{label}_why", None)
        if reason:
            print(f"  ! {label}: {reason}")

    warmup.start(app, s)
    yield


app = FastAPI(title="Happy Bite", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings().origins,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(extra_router)

media = settings().media_path
media.mkdir(parents=True, exist_ok=True)
(media / "recipes").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media), name="media")

dist = ROOT / "frontend" / "dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        file = (dist / path).resolve()
        if path and file.is_file() and dist in file.parents:
            return FileResponse(file)
        return FileResponse(dist / "index.html")
