"""Pictures that belong to a recipe, made once and kept.

The browser owns the recipe book; the server owns the pixels. A recipe asks
for its pictures by id, the store answers with whatever it already has, and
quietly generates the rest in the background. Ask again a second later and the
answer is the same list, a little longer.

Why an index file: SD-Turbo takes seconds per picture and the media folder
survives restarts, so throwing the mapping away on every `uvicorn --reload`
would mean regenerating the whole book. `media/recipes.json` is the mapping —
recipe id -> the files made for it — written after each take.

Nothing here is load-bearing. If generation is off or broken, `get` returns an
empty list and the app shows its tinted-plate fallback exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import photoqueue, prompts

MAX_PER_RECIPE = 1


def _safe(rid: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(rid))
    return keep.strip("-")[:48]


class RecipeImages:
    def __init__(self, images, media_path: Path, count: int = 1):
        self.images = images
        self.dir = Path(media_path)
        self.count = max(1, min(MAX_PER_RECIPE, int(count or 3)))
        self.index_path = self.dir / "recipes.json"
        self.index: dict[str, dict] = {}
        self._busy: set[str] = set()
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None
        self._load()

    # ---------------------------------------------------------------- index

    def _load(self) -> None:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.index = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.index = {}
        # Drop entries whose files were deleted by hand.
        for rid, rec in list(self.index.items()):
            urls = [u for u in rec.get("urls", []) if self._exists(u)]
            if urls:
                rec["urls"] = urls[:1]
                for extra in urls[1:]:
                    try:
                        (self.dir / extra[len("/media/"):]).unlink()
                    except OSError:
                        pass
            else:
                self.index.pop(rid, None)
        self._save()

    def _exists(self, url: str) -> bool:
        if not isinstance(url, str) or not url.startswith("/media/"):
            return False
        return (self.dir / url[len("/media/"):]).is_file()

    def _save(self) -> None:
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            self.index_path.write_text(json.dumps(self.index, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        except OSError as e:
            print("recipe image index not written:", e)

    # ---------------------------------------------------------------- api

    def get(self, recipe_id: str) -> dict:
        rid = _safe(recipe_id)
        rec = self.index.get(rid) or {}
        urls = list(rec.get("urls") or [])
        return {"urls": urls, "pending": rid in self._busy, "want": self.count}

    def ensure(self, recipe: dict, count: int | None = None) -> dict:
        """Return what exists now and start making up whatever is missing."""
        rid = _safe(recipe.get("id") or recipe.get("name") or "")
        if not rid:
            return {"urls": [], "pending": False, "want": 0}
        want = 1
        have = self.get(rid)
        missing = len(have["urls"]) < want
        if self.images and missing and rid not in self._busy:
            self._busy.add(rid)
            self._enqueue({"rid": rid, "recipe": recipe, "want": want,
                           "have": len(have["urls"])})
            have["pending"] = True
        elif missing:
            # No picture model in this process — by design, see photoqueue.py.
            # Write down what was asked for so `tools/make_photos.py` can paint
            # it later, when it isn't fighting the language model for memory.
            photoqueue.add(self.dir, {**recipe, "id": rid})
            have["queued"] = True
        have["want"] = want
        return have

    # ---------------------------------------------------------------- worker
    # One worker, one picture at a time. SD-Turbo on a laptop is happiest that
    # way, and it means a burst of recipe cards can't stall the chat.

    def _enqueue(self, job: dict) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._queue.put_nowait(job)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            rid = job["rid"]
            try:
                await self._make(job)
            except Exception as e:  # noqa: BLE001 — never kill the worker
                print("recipe pictures failed for", rid, ":", type(e).__name__, e)
            finally:
                self._busy.discard(rid)
                self._queue.task_done()

    async def _make(self, job: dict) -> None:
        r = job["recipe"]
        rid, want, have = job["rid"], job["want"], job["have"]
        prompt = prompts.dish_image_prompt(r.get("name") or "a home-cooked dish",
                                           r.get("cuisine"), r.get("description"))
        made = await self.images.create_many(prompt, want - have, name=f"recipes/{rid}")
        if not made:
            return
        rec = self.index.setdefault(rid, {"urls": []})
        rec["name"] = (r.get("name") or "")[:80]
        rec["urls"] = list(dict.fromkeys([*rec.get("urls", []), *made]))[:MAX_PER_RECIPE]
        rec["updated"] = int(time.time())
        self._save()

    def forget(self, recipe_id: str) -> int:
        """Delete a recipe's pictures — used when the user asks for new ones."""
        rid = _safe(recipe_id)
        rec = self.index.pop(rid, None)
        if not rec:
            return 0
        gone = 0
        for url in rec.get("urls", []):
            try:
                (self.dir / url[len("/media/"):]).unlink()
                gone += 1
            except OSError:
                pass
        self._save()
        return gone
