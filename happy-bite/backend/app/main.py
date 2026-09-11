"""Happy Bite — server.

    uvicorn app.main:app --reload            (from backend/)

In development the React app runs on Vite (port 5173) and proxies /api
here. In production `npm run build` writes frontend/dist, and this one
process serves the app, the API and generated pictures — one address to
type into the kitchen tablet."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import ROOT, settings
from .providers.llm import make_llm
from .providers.media import make_images, make_speech


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    app.state.llm = make_llm(s)
    app.state.images = make_images(s)
    app.state.speech = make_speech(s)
    print("Happy Bite server —",
          f"chat: {app.state.llm.name if app.state.llm else 'off'},",
          f"pictures: {'on' if app.state.images else 'off'},",
          f"voice: {'server' if app.state.speech else 'browser'}")
    yield


app = FastAPI(title="Happy Bite", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings().origins,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

media = settings().media_path
media.mkdir(parents=True, exist_ok=True)
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
