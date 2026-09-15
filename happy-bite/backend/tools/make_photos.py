#!/usr/bin/env python3
"""Paint the dishes that are waiting for a photograph. Run it yourself.

    cd backend
    python tools/make_photos.py              # work through the queue once
    python tools/make_photos.py --watch      # stay open, check every 20 s
    python tools/make_photos.py --list       # what's waiting, change nothing
    python tools/make_photos.py --again ID   # throw a dish's picture away and redo it

This is a separate program on purpose. SD-Turbo holds about 2.5 GB of memory
for as long as it is loaded, and on a 16 GB Mac that is 2.5 GB the language
model wants. Running both inside the server is what made a recipe take seven
minutes: the machine swapped. Here the picture model is loaded when you start
this, and gone the moment you close it.

So the working rhythm is: cook with the server, and once in a while — while
you're not waiting on anything — run this and let it catch up. Set
`IMAGE_PROVIDER=none` in backend/.env and the server will simply write down
what it wants instead of trying to make it.

It writes exactly what the server used to write: files under media/recipes/,
and the same media/recipes.json index. The browser cannot tell the difference.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from app import photoqueue, prompts                      # noqa: E402
from app.config import Settings                          # noqa: E402
from app.imagestore import RecipeImages, _safe           # noqa: E402
from app.providers.media import make_images_checked      # noqa: E402


def settings_for_pictures() -> Settings:
    """The app's own settings, but with pictures forced on.

    backend/.env says IMAGE_PROVIDER=none because the *server* must not load
    the picture model. This program is the one place that should, so it
    overrides that one field and leaves everything else — model name, steps,
    size, device, media folder — exactly as configured.
    """
    s = Settings()
    model = (s.image_model or "").strip()
    # A HuggingFace repo id has an owner in it. "gpt-image-1" is OpenAI's name
    # and means nothing to diffusers — and it is exactly what .env is likely to
    # still say, because with IMAGE_PROVIDER=none the server stopped caring.
    # Handing that to from_pretrained() is a baffling failure ten seconds in.
    if "/" not in model:
        model = "stabilityai/sd-turbo"
    return s.model_copy(update={"image_provider": "local", "image_model": model})


async def paint_one(images, store: RecipeImages, row: dict, media: Path) -> bool:
    rid = _safe(row["id"])
    name = row.get("name") or "a home-cooked dish"
    prompt = prompts.dish_image_prompt(name, row.get("cuisine"), row.get("description"))

    started = time.time()
    print(f"  painting {name} …", end="", flush=True)
    made = await images.create_many(prompt, 1, name=f"recipes/{rid}")
    if not made:
        print(" nothing came back")
        return False

    rec = store.index.setdefault(rid, {"urls": []})
    rec["name"] = name[:80]
    rec["urls"] = list(dict.fromkeys([*rec.get("urls", []), *made]))[:1]
    rec["updated"] = int(time.time())
    store._save()                       # the same index the server reads
    photoqueue.remove(media, row["id"])
    print(f" {made[0]}  ({time.time() - started:.0f}s)")
    return True


async def run_once(images, store: RecipeImages, media: Path, skip_done: bool = True) -> int:
    rows = photoqueue.read(media)
    if not rows:
        return 0
    painted = 0
    for row in rows:
        rid = _safe(row["id"])
        if skip_done and store.get(rid)["urls"]:
            print(f"  {row.get('name') or rid} — already has one, skipping")
            photoqueue.remove(media, row["id"])
            continue
        try:
            painted += await paint_one(images, store, row, media)
        except KeyboardInterrupt:
            raise
        except Exception as e:                            # noqa: BLE001
            # One bad dish must not strand the rest of the queue, and it stays
            # queued so the next run tries it again.
            print(f"  {row.get('name') or rid} failed: {type(e).__name__}: {e}")
    return painted


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", action="store_true", help="keep running, check every 20 s")
    ap.add_argument("--list", action="store_true", help="show the queue and stop")
    ap.add_argument("--again", metavar="ID", help="delete a dish's picture and queue it again")
    ap.add_argument("--every", type=int, default=20, help="seconds between checks with --watch")
    args = ap.parse_args()

    s = settings_for_pictures()
    media = s.media_path
    store = RecipeImages(None, media, 1)          # index only; this file does the painting

    if args.again:
        gone = store.forget(args.again)
        photoqueue.add(media, {"id": args.again, "name": args.again})
        print(f"Deleted {gone} file(s) and queued {args.again} again.")
        return 0

    rows = photoqueue.read(media)
    if args.list:
        if not rows:
            print("Nothing waiting. Open a recipe in the app and it'll ask.")
            return 0
        print(f"{len(rows)} dish(es) waiting:")
        for r in rows:
            have = " (already has a picture)" if store.get(_safe(r["id"]))["urls"] else ""
            print(f"  {r['id']:24} {r.get('name') or ''}{have}")
        return 0

    if not rows and not args.watch:
        print("Nothing waiting. Open a recipe in the app and it'll ask for one,")
        print("then run this again.  (--watch stays open and picks them up live.)")
        return 0

    images, why = make_images_checked(s)
    if not images:
        print("Can't paint anything:", why or "pictures are switched off")
        print("\nInstall what's missing in the backend venv:")
        print("  pip install torch diffusers transformers accelerate safetensors pillow")
        return 2

    print(f"Picture model: {s.image_model} on {s.image_device}, {s.image_size}px, "
          f"{s.image_steps} steps")
    print("Loading it — about 2.5 GB, once …")
    await images.warmup()
    print("Ready.\n")

    total = await run_once(images, store, media)
    if not args.watch:
        print(f"\nDone — {total} picture(s). They're in {media / 'recipes'}.")
        print("Reload the app and they're there.")
        return 0

    print(f"\n{total} painted. Watching — Ctrl-C to stop.")
    try:
        while True:
            await asyncio.sleep(max(5, args.every))
            more = await run_once(images, store, media)
            if more:
                print(f"  ({more} more)")
    except KeyboardInterrupt:
        print("\nStopped. The picture model is unloaded with this process.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
